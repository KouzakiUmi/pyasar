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
MAX_TREE_DEPTH = 255
MAX_OFFSET_DIGITS = 20


class AsarError(Exception):
    """Base error raised by pyasar."""


class AsarFormatError(AsarError):
    """The archive header or its file table is malformed."""


def _bounded_json_int(text: str) -> int:
    """Cap JSON integers by digit count, independent of interpreter version."""
    if len(text.lstrip("-")) > MAX_OFFSET_DIGITS:
        raise AsarFormatError("header integer exceeds the supported digit limit")
    return int(text)


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
        header = json.loads(
            raw[8 : 8 + json_size].decode("utf-8"), parse_int=_bounded_json_int
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise AsarFormatError("header is not valid UTF-8 JSON") from error
    if not isinstance(header, dict) or not isinstance(header.get("files"), dict):
        raise AsarFormatError("header must contain a files object")
    return header, 8 + second_size


def _is_drive_segment(part: str) -> bool:
    """Reject Windows drive-relative segments such as 'C:' or 'C:name'."""
    return (
        len(part) >= 2 and part[1] == ":" and part[0].isascii() and part[0].isalpha()
    )


def _parts(name: str | os.PathLike[str]) -> tuple[str, ...]:
    if not isinstance(name, str):
        name = os.fspath(name)
        if isinstance(name, bytes):
            raise TypeError("archive entry paths must be text, not bytes")
    path = PurePosixPath(name.replace("\\", "/"))
    if (
        "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or str(path) == "."
        or any(_is_drive_segment(part) for part in path.parts)
    ):
        raise AsarFormatError(f"unsafe archive path: {name!r}")
    return path.parts


def _validate_entry_name(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or _is_drive_segment(name)
    ):
        raise AsarFormatError(f"invalid ASAR entry name: {name!r}")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as error:
        # POSIX sources can carry undecodable bytes as lone surrogates, which
        # have no ASAR header representation.
        raise AsarFormatError(
            f"ASAR entry name is not UTF-8 encodable: {name!r}"
        ) from error


def _windows_filesystem_parts(name: str) -> tuple[str, ...]:
    """Components of name for mapping onto a Windows filesystem.

    A component such as 'file.txt:stream' is an ordinary name on POSIX but
    addresses a named data stream of 'file.txt' on Windows; mapping it onto
    the filesystem there would read or modify a different file than the
    archive entry describes. POSIX keeps the plain name for interop.
    """
    parts = _parts(name)
    if os.name == "nt":
        for part in parts:
            if ":" in part:
                raise AsarFormatError(
                    f"archive name maps to a Windows data stream: {name!r}"
                )
    return parts


def _walk(
    files: dict[str, Any], prefix: str = "", depth: int = 0
) -> Iterator[tuple[str, dict[str, Any]]]:
    if depth > MAX_TREE_DEPTH:
        raise AsarFormatError("ASAR file table is nested too deeply")
    for name, node in files.items():
        if not isinstance(name, str) or not isinstance(node, dict):
            raise AsarFormatError("file table contains an invalid entry")
        _validate_entry_name(name)
        full_name = f"{prefix}/{name}" if prefix else name
        if "files" in node:
            children = node["files"]
            # An explicit files key, including null, must hold an object.
            if not isinstance(children, dict):
                raise AsarFormatError(f"{full_name!r}.files is not an object")
            if "link" in node:
                # Directory and link fields are mutually exclusive; a node
                # carrying both would classify differently across readers.
                raise AsarFormatError(
                    f"{full_name!r} mixes directory and link fields"
                )
            yield from _walk(children, full_name, depth + 1)
        else:
            yield full_name, node


def _walk_directories(files: dict[str, Any], prefix: str = "") -> Iterator[str]:
    for name, node in files.items():
        full_name = f"{prefix}/{name}" if prefix else name
        children = node.get("files")
        if isinstance(children, dict):
            yield full_name
            yield from _walk_directories(children, full_name)


def _file_offset(node: dict[str, Any]) -> int:
    value = node.get("offset")
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or len(value) > MAX_OFFSET_DIGITS
    ):
        raise AsarFormatError("regular file offset must be a decimal string")
    return int(value)


def _file_size(node: dict[str, Any]) -> int:
    value = node.get("size")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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

    def _resolve(self, name: str, follow_links: bool) -> tuple[str, dict[str, Any]]:
        parts = _parts(name)
        seen: set[tuple[str, ...]] = set()
        for _ in range(MAX_TREE_DEPTH + 1):
            if parts in seen:
                raise AsarFormatError(f"circular symbolic link: {name}")
            seen.add(parts)
            node = self.header
            for index, part in enumerate(parts):
                children = node.get("files", {})
                if part not in children:
                    raise KeyError(name)
                node = children[part]
                if follow_links and "link" in node:
                    parts = _parts(node["link"]) + parts[index + 1 :]
                    break
            else:
                return "/".join(parts), node
        raise AsarFormatError(f"too many symbolic links: {name}")

    def info(self, name: str, *, follow_links: bool = False) -> dict[str, Any]:
        _, node = self._resolve(name, follow_links)
        if "files" in node:
            raise IsADirectoryError(name)
        return node

    def read(
        self, name: str, *, verify: bool = False, follow_links: bool = False
    ) -> bytes:
        resolved_name, node = self._resolve(name, follow_links)
        if "files" in node:
            raise IsADirectoryError(name)
        if "link" in node:
            raise OSError("cannot read a symbolic link as a regular file")
        size = _file_size(node)
        if node.get("unpacked") is True:
            sidecar = self.path.parent.resolve() / (self.path.name + ".unpacked")
            if sidecar.resolve() != sidecar:
                raise AsarFormatError("unpacked root must not be a filesystem link")
            if sidecar.exists() and not sidecar.is_dir():
                # A regular file or special file in place of the sidecar
                # directory reports differently per platform (ENOTDIR on
                # POSIX, path-not-found on Windows); normalize the error.
                raise NotADirectoryError(
                    f"unpacked ASAR root is not a directory: {sidecar}"
                )
            source = sidecar.joinpath(*_windows_filesystem_parts(resolved_name)).resolve()
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
            if not isinstance(integrity, dict):
                raise AsarFormatError(f"invalid integrity metadata: {name}")
            if "hash" in integrity:
                # A present hash key must hold a string: an explicit null is
                # malformed metadata, not the absence of metadata.
                expected = integrity["hash"]
                if not isinstance(expected, str):
                    raise AsarFormatError(f"invalid integrity hash: {name}")
                if hashlib.sha256(data).hexdigest() != expected:
                    raise AsarError(f"integrity check failed: {name}")
        return data

    def _link_is_directory(self, name: str) -> bool:
        """Resolve header links, including links in intermediate components."""
        try:
            _, node = self._resolve(name, True)
        except KeyError:
            return False  # Preserve dangling links as file links.
        return "files" in node

    def extract(
        self, destination: str | os.PathLike[str], *, verify: bool = False
    ) -> None:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        root = target.resolve()
        entries = list(_walk(self.header["files"]))
        for name in _walk_directories(self.header["files"]):
            output = target.joinpath(*_windows_filesystem_parts(name))
            if root not in (output.parent.resolve(), *output.parent.resolve().parents):
                raise AsarFormatError(f"unsafe extraction target: {name}")
            if output.is_symlink() or (output.exists() and not output.is_dir()):
                raise FileExistsError(
                    f"refusing to overwrite extraction target: {output}"
                )
            output.mkdir(parents=True, exist_ok=True)
        for name, node in entries:
            output = target.joinpath(*_windows_filesystem_parts(name))
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
                link_target = target.joinpath(*_windows_filesystem_parts(link))
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

    archive_path = Path(path).resolve()
    header, base_offset = _read_header(archive_path)
    length = archive_path.stat().st_size
    for _, node in _walk(header["files"]):
        if "link" in node:
            if not isinstance(node["link"], str):
                raise AsarFormatError("symbolic link target must be a string")
            for part in _parts(node["link"]):
                # Link targets must survive the same UTF-8 round trip as
                # entry names, or extraction fails with a codec error.
                _validate_entry_name(part)
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
