"""Deterministic modern-ASAR archive writer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import warnings
from pathlib import Path
from typing import Callable, Iterable

from .archive import AsarFormatError, _validate_entry_name


DEFAULT_UNPACK_EXTENSIONS = frozenset({".node"})
BLOCK_SIZE = 4 * 1024 * 1024
_LINK_REPARSE_TAGS = frozenset(
    tag
    for tag in (
        # These constants only exist on Windows builds of CPython.
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
    )
    if tag is not None
)
# FAT12/16/32 volumes cannot store any single file of 4 GiB or more.
_FAT_MAX_FILE_SIZE = 4 * 1024**3 - 1


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"ASAR structure field exceeds the 4 GiB limit: {value}")
    return struct.pack("<I", value)


def _pickle(value: int) -> bytes:
    return _u32(4) + _u32(value)


def _string_pickle(value: bytes) -> bytes:
    padding = (-len(value)) % 4
    payload = _u32(len(value)) + value + b"\x00" * padding
    return _u32(len(payload)) + payload


def _is_link_entry(path: Path) -> bool:
    """True for symbolic links and, on Windows, directory junctions.

    Other Windows reparse points, such as cloud placeholder files, are not
    links and are packed as regular files.
    """
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            result = os.lstat(path)
        except FileNotFoundError:
            # A not-yet-existing destination is not a link.
            return False
        # Other OSErrors fail closed: when the reparse classification cannot
        # be determined, refuse to pack the entry as a regular file.
        if not getattr(result, "st_file_attributes", 0) & (
            stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            return False
        return getattr(result, "st_reparse_tag", 0) in _LINK_REPARSE_TAGS
    return False


def _iter_tree(root: Path) -> list[Path]:
    """Collect every entry below root in lexicographic order.

    Link entries are listed but never traversed, so source link cycles cannot
    hang packing and linked content is never expanded into duplicate files.
    """
    entries: list[Path] = []
    pending = [root]
    while pending:
        for child in sorted(pending.pop().iterdir()):
            entries.append(child)
            if child.is_dir() and not _is_link_entry(child):
                pending.append(child)
    return sorted(entries)


def _resolve_link_target(item: Path, raw_target: str) -> Path:
    """Resolve a link target, normalizing Windows verbatim path prefixes."""
    target = item.parent / raw_target
    text = str(target)
    if os.name == "nt" and text.startswith("\\\\?\\"):
        if text.startswith("\\\\?\\UNC\\"):
            # \\?\UNC\server\share is the verbatim form of \\server\share.
            target = Path("\\\\" + text[8:])
        else:
            target = Path(text[4:])
    return target.resolve()


_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_FAT_FILESYSTEM_TYPES = frozenset({"vfat", "msdos", "umsdos"})


def _unescape_mount(text: str) -> str:
    """Decode the octal escapes /proc/mounts uses for special characters."""
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), text)


def _mount_table_allows_large_files(resolved: str, lines: Iterable[str]) -> bool:
    """Whether the longest mount covering resolved is not FAT-family.

    Matching is path-component aware, so a mount at /mnt/usb never claims a
    destination under /mnt/usb-backup.
    """
    best_type, best_len = "", -1
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = _unescape_mount(parts[1])
        if resolved == mount or resolved.startswith(mount.rstrip("/") + "/"):
            if len(mount) > best_len:
                best_len, best_type = len(mount), parts[2]
    return best_type not in _FAT_FILESYSTEM_TYPES


def _volume_allows_large_files(destination: Path) -> bool:
    """Whether the volume hosting destination can hold files over 4 GiB-1.

    Only FAT-family filesystems are known to reject such files. Detection is
    best effort: on any failure the answer is True, leaving the operating
    system to report its own write error instead.
    """
    if os.name == "nt":
        try:
            import ctypes

            name = ctypes.create_unicode_buffer(261)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                destination.anchor, None, 0, None, None, None, name, 261
            )
            return ok == 0 or name.value.upper() not in {
                "FAT",
                "FAT12",
                "FAT16",
                "FAT32",
            }
        except Exception:
            return True
    try:
        resolved = str(destination.resolve())
    except OSError:
        return True
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as stream:
            return _mount_table_allows_large_files(resolved, stream)
    except OSError:
        return True


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
    if _is_link_entry(unpack_root) or unpack_root.is_file():
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
    if _is_link_entry(output_path):
        raise ValueError(f"destination must not be a symbolic link: {output_path}")
    output = output_path.resolve()
    if output.is_dir():
        raise IsADirectoryError(output)
    large_files_supported = _volume_allows_large_files(output)
    unpack_root = output.with_name(output.name + ".unpacked")
    if _is_within(root, unpack_root.resolve()):
        raise ValueError("unpacked destination must not contain the source directory")
    _clean_unpack_root(unpack_root)
    files: dict[str, object] = {}
    payloads: list[Path] = []
    offset = 0
    for item in _iter_tree(root):
        item_absolute = item.absolute()
        if item_absolute == output or _is_within(item_absolute, unpack_root):
            continue
        relative = item.relative_to(root)
        if filter and not filter(relative):
            continue
        cursor = files
        parts = relative.parts
        for part in parts:
            try:
                _validate_entry_name(part)
            except AsarFormatError as error:
                raise ValueError(
                    f"unsupported entry name {part!r} in source: {relative}"
                ) from error
        for part in parts[:-1]:
            child = cursor.setdefault(part, {"files": {}})
            child_files = child.get("files")
            if not isinstance(child_files, dict):
                raise ValueError(
                    f"source path conflicts with a file or link: {relative}"
                )
            cursor = child_files
        if _is_link_entry(item):
            target = _resolve_link_target(item, os.readlink(item))
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
            item_stat = item.stat()
            size, unpacked = (
                item_stat.st_size,
                item.suffix.lower() in unpack_extensions,
            )
            if not large_files_supported and size > _FAT_MAX_FILE_SIZE:
                raise ValueError(
                    f"destination volume cannot hold a {size}-byte file: {relative}"
                )
            node = {
                "size": size,
                "integrity": _integrity(item),
            }
            if os.name != "nt" and item_stat.st_mode & stat.S_IXUSR:
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
        else:
            warnings.warn(
                f"skipping unsupported special file: {relative}",
                stacklevel=2,
            )
    header = json.dumps(
        {"files": files}, ensure_ascii=False, separators=(",", ":")
    ).encode()
    header_pickle = _string_pickle(header)
    if not large_files_supported:
        total = 8 + len(header_pickle) + offset
        if total > _FAT_MAX_FILE_SIZE:
            raise ValueError(
                f"destination volume cannot hold the {total}-byte archive: {output}"
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        stream.write(_pickle(len(header_pickle)))
        stream.write(header_pickle)
        for payload in payloads:
            with payload.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, stream, 1024 * 1024)
