"""Deterministic modern-ASAR archive writer."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import struct
from pathlib import Path
from typing import Callable


DEFAULT_UNPACK_EXTENSIONS = frozenset({".node"})
BLOCK_SIZE = 4 * 1024 * 1024


def _pickle(value: int) -> bytes:
    return struct.pack("<I", 4) + struct.pack("<I", value)


def _string_pickle(value: bytes) -> bytes:
    padding = (-len(value)) % 4
    payload = struct.pack("<I", len(value)) + value + b"\x00" * padding
    return struct.pack("<I", len(payload)) + payload


def _integrity(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    blocks: list[str] = []
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(BLOCK_SIZE), b""):
            digest.update(chunk)
            blocks.append(hashlib.sha256(chunk).hexdigest())
    if not blocks:
        blocks.append(hashlib.sha256(b"").hexdigest())
    return {
        "algorithm": "SHA256",
        "hash": digest.hexdigest(),
        "blockSize": BLOCK_SIZE,
        "blocks": blocks,
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _clean_unpack_root(unpack_root: Path) -> None:
    if unpack_root.is_symlink() or unpack_root.is_file():
        unpack_root.unlink()
    elif unpack_root.is_dir():
        shutil.rmtree(unpack_root)


def pack(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    unpack_extensions: frozenset[str] | set[str] = DEFAULT_UNPACK_EXTENSIONS,
    filter: Callable[[Path], bool] | None = None,
) -> None:
    """Pack a directory into an Electron-compatible ASAR archive.

    Matching extension files are copied to the sibling unpacked directory.
    """

    root = Path(source).resolve()
    output_path = Path(destination)
    if not root.is_dir():
        raise NotADirectoryError(root)
    if output_path.is_symlink():
        raise ValueError(f"destination must not be a symbolic link: {output_path}")
    output = output_path.resolve()
    if output.is_dir():
        raise IsADirectoryError(output)
    unpack_root = output.with_name(output.name + ".unpacked")
    if _is_within(root, unpack_root.resolve()):
        raise ValueError("unpacked destination must not contain the source directory")
    _clean_unpack_root(unpack_root)
    files: dict[str, object] = {}
    payloads: list[Path] = []
    offset = 0
    for item in sorted(root.rglob("*")):
        item_absolute = item.absolute()
        if item_absolute == output or _is_within(item_absolute, unpack_root):
            continue
        relative = item.relative_to(root)
        if filter and not filter(relative):
            continue
        cursor = files
        parts = relative.parts
        for part in parts[:-1]:
            child = cursor.setdefault(part, {"files": {}})
            cursor = child["files"]  # type: ignore[index]
        if item.is_symlink():
            target = (item.parent / os.readlink(item)).resolve()
            try:
                link = target.relative_to(root)
            except ValueError:
                raise ValueError(
                    f"symbolic link points outside source: {relative}"
                ) from None
            if not link.parts:
                raise ValueError(f"symbolic link points to source root: {relative}")
            cursor[parts[-1]] = {"link": link.as_posix()}
        elif item.is_dir():
            cursor.setdefault(parts[-1], {"files": {}})
        elif item.is_file():
            size, unpacked = (
                item.stat().st_size,
                item.suffix.lower() in unpack_extensions,
            )
            node = {
                "size": size,
                "integrity": _integrity(item),
            }
            if os.name != "nt" and item.stat().st_mode & stat.S_IXUSR:
                node["executable"] = True
            if unpacked:
                node["unpacked"] = True
                target = unpack_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            else:
                node["offset"] = str(offset)
                payloads.append(item)
                offset += size
            cursor[parts[-1]] = node
    header = json.dumps(
        {"files": files}, ensure_ascii=False, separators=(",", ":")
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    header_pickle = _string_pickle(header)
    with output.open("wb") as stream:
        stream.write(_pickle(len(header_pickle)))
        stream.write(header_pickle)
        for payload in payloads:
            with payload.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, stream, 1024 * 1024)
