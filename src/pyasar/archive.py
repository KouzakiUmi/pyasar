"""Reading, validation and safe extraction for modern Electron ASAR files."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


MAX_HEADER_SIZE = 50 * 1024 * 1024


class AsarError(Exception):
    """Base error raised by pyasar."""


class AsarFormatError(AsarError):
    """The archive header or its file table is malformed."""


def _read_header(path: Path) -> tuple[dict[str, Any], int]:
    with path.open("rb") as stream:
        archive_size = os.fstat(stream.fileno()).st_size
        raw = stream.read(8)
        if len(raw) != 8:
            raise AsarFormatError("archive is shorter than the ASAR preamble")
        first_size, second_size = struct.unpack("<II", raw)
        if first_size != 4 or second_size < 8:
            raise AsarFormatError("invalid ASAR pickle preamble")
        if second_size > archive_size - 8:
            raise AsarFormatError("declared ASAR header exceeds archive size")
        if second_size > MAX_HEADER_SIZE:
            raise AsarFormatError("ASAR header exceeds the supported size limit")
        raw = stream.read(second_size)
    if len(raw) != second_size:
        raise AsarFormatError("truncated ASAR header")
    payload_size = struct.unpack("<I", raw[:4])[0]
    if payload_size + 4 != second_size or payload_size < 4:
        raise AsarFormatError("invalid ASAR header length")
    json_size = struct.unpack("<I", raw[4:8])[0]
    if json_size <= 0 or json_size > payload_size - 4:
        raise AsarFormatError("invalid ASAR JSON length")
    try:
        header = json.loads(raw[8 : 8 + json_size].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AsarFormatError("header is not UTF-8 JSON") from error
    if not isinstance(header, dict) or not isinstance(header.get("files"), dict):
        raise AsarFormatError("header must contain a files object")
    return header, 8 + second_size


def _parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) == ".":
        raise AsarFormatError(f"unsafe archive path: {name!r}")
    return path.parts


def _validate_entry_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise AsarFormatError(f"invalid ASAR entry name: {name!r}")


def _walk(
    files: dict[str, Any], prefix: str = ""
) -> Iterator[tuple[str, dict[str, Any]]]:
    for name, node in files.items():
        if not isinstance(name, str) or not isinstance(node, dict):
            raise AsarFormatError("file table contains an invalid entry")
        _validate_entry_name(name)
        full_name = f"{prefix}/{name}" if prefix else name
        children = node.get("files")
        if children is not None:
            if not isinstance(children, dict):
                raise AsarFormatError(f"{full_name!r}.files is not an object")
            yield from _walk(children, full_name)
        else:
            yield full_name, node


def _walk_directories(files: dict[str, Any], prefix: str = "") -> Iterator[str]:
    for name, node in files.items():
        full_name = f"{prefix}/{name}" if prefix else name
        children = node.get("files")
        if children is not None:
            yield full_name
            yield from _walk_directories(children, full_name)


def _file_offset(node: dict[str, Any]) -> int:
    value = node.get("offset")
    if not isinstance(value, str) or not value.isdigit():
        raise AsarFormatError("regular file offset must be a decimal string")
    return int(value)


def _file_size(node: dict[str, Any]) -> int:
    value = node.get("size")
    if not isinstance(value, int) or value < 0:
        raise AsarFormatError("regular file size must be a non-negative integer")
    return value


@dataclass(frozen=True)
class AsarArchive:
    """Opened ASAR metadata. Payloads are read on demand."""

    path: Path
    header: dict[str, Any]
    base_offset: int

    def names(self) -> list[str]:
        return [name for name, _ in _walk(self.header["files"])]

    def info(self, name: str) -> dict[str, Any]:
        parts = _parts(name)
        current: Any = self.header["files"]
        node: Any = None
        for index, part in enumerate(parts):
            if not isinstance(current, dict) or part not in current:
                raise KeyError(name)
            node = current[part]
            if index < len(parts) - 1:
                if not isinstance(node, dict) or not isinstance(
                    node.get("files"), dict
                ):
                    raise KeyError(name)
                current = node["files"]
        if not isinstance(node, dict):
            raise AsarFormatError(f"invalid ASAR entry: {name!r}")
        if "files" in node:
            raise IsADirectoryError(name)
        return node

    def read(self, name: str, *, verify: bool = False) -> bytes:
        node = self.info(name)
        if "link" in node:
            raise OSError("cannot read a symbolic link as a regular file")
        size = _file_size(node)
        if node.get("unpacked") is True:
            sidecar = self.path.parent.resolve() / (self.path.name + ".unpacked")
            if sidecar.resolve() != sidecar:
                raise AsarFormatError("unpacked root must not be a filesystem link")
            source = sidecar.joinpath(*_parts(name)).resolve()
            if sidecar not in source.parents:
                raise AsarFormatError(f"unpacked file escapes sidecar: {name}")
            try:
                data = source.read_bytes()
            except FileNotFoundError as error:
                raise FileNotFoundError(
                    f"unpacked ASAR file is missing: {name}"
                ) from error
        else:
            with self.path.open("rb") as stream:
                stream.seek(self.base_offset + _file_offset(node))
                data = stream.read(size)
        if len(data) != size:
            raise EOFError(f"truncated ASAR payload: {name}")
        if verify:
            integrity = node.get("integrity", {})
            expected = integrity.get("hash") if isinstance(integrity, dict) else None
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise AsarError(f"integrity check failed: {name}")
        return data

    def _link_is_directory(self, name: str) -> bool:
        """Resolve header links, including links in intermediate components."""
        parts = _parts(name)
        seen: set[tuple[str, ...]] = set()
        for _ in range(40):
            if parts in seen:
                raise AsarFormatError(f"circular symbolic link: {name}")
            seen.add(parts)
            node = self.header
            for index, part in enumerate(parts):
                node = node.get("files", {}).get(part)
                if node is None:
                    return False  # Preserve dangling links as file links.
                if "link" in node:
                    parts = _parts(node["link"]) + parts[index + 1 :]
                    break
            else:
                return "files" in node
        raise AsarFormatError(f"too many symbolic links: {name}")

    def extract(
        self, destination: str | os.PathLike[str], *, verify: bool = False
    ) -> None:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        entries = list(_walk(self.header["files"]))
        for name in _walk_directories(self.header["files"]):
            output = target.joinpath(*_parts(name))
            if root not in (output.parent.resolve(), *output.parent.resolve().parents):
                raise AsarFormatError(f"unsafe extraction target: {name}")
            if output.is_symlink() or (output.exists() and not output.is_dir()):
                raise FileExistsError(
                    f"refusing to overwrite extraction target: {output}"
                )
            output.mkdir(parents=True, exist_ok=True)
        for name, node in entries:
            output = target.joinpath(*_parts(name))
            if root not in (output.parent.resolve(), *output.parent.resolve().parents):
                raise AsarFormatError(f"unsafe extraction target: {name}")
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or output.is_symlink():
                raise FileExistsError(
                    f"refusing to overwrite extraction target: {output}"
                )
            if "link" in node:
                link = node["link"]
                if not isinstance(link, str):
                    raise AsarFormatError(f"unsafe symbolic link: {name}")
                link_target = target.joinpath(*_parts(link))
                resolved_link = link_target.resolve()
                if root not in (resolved_link, *resolved_link.parents):
                    raise AsarFormatError(f"symbolic link escapes destination: {name}")
                output.symlink_to(
                    os.path.relpath(link_target, output.parent),
                    target_is_directory=self._link_is_directory(link),
                )
            else:
                output.write_bytes(self.read(name, verify=verify))
                if os.name != "nt" and node.get("executable") is True:
                    output.chmod(0o755)


def open_archive(path: str | os.PathLike[str]) -> AsarArchive:
    """Open and structurally validate an ASAR archive."""

    archive_path = Path(path)
    header, base_offset = _read_header(archive_path)
    length = archive_path.stat().st_size
    for _, node in _walk(header["files"]):
        if "link" in node:
            if not isinstance(node["link"], str):
                raise AsarFormatError("symbolic link target must be a string")
            _parts(node["link"])
            continue
        if node.get("unpacked") is True:
            _file_size(node)
            continue
        offset, size = _file_offset(node), _file_size(node)
        if base_offset + offset + size > length:
            raise AsarFormatError("file table refers past the end of the archive")
    return AsarArchive(archive_path, header, base_offset)


def extract(
    path: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    verify: bool = False,
) -> None:
    """Extract an archive using path traversal-safe defaults."""

    open_archive(path).extract(destination, verify=verify)
