import json
import os
import struct
from pathlib import Path

import pytest

from pyasar import AsarFormatError, open_archive, pack


def _write_archive_header(path: Path, header: dict) -> None:
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    padding = (-len(encoded)) % 4
    payload = struct.pack("<I", len(encoded)) + encoded + b"\x00" * padding
    header_pickle = struct.pack("<I", len(payload)) + payload
    path.write_bytes(struct.pack("<II", 4, len(header_pickle)) + header_pickle)


def test_rejects_short_file(tmp_path) -> None:
    archive = tmp_path / "bad.asar"
    archive.write_bytes(b"bad")
    with pytest.raises(AsarFormatError):
        open_archive(archive)


def test_rejects_header_length_past_archive_end(tmp_path) -> None:
    archive = tmp_path / "bad-header.asar"
    archive.write_bytes(struct.pack("<II", 4, 1024))

    with pytest.raises(AsarFormatError, match="header exceeds archive size"):
        open_archive(archive)


def test_rejects_path_separator_in_file_table_key(tmp_path) -> None:
    archive = tmp_path / "bad-name.asar"
    _write_archive_header(
        archive,
        {"files": {"nested/file.txt": {"offset": "0", "size": 0}}},
    )

    with pytest.raises(AsarFormatError, match="invalid ASAR entry name"):
        open_archive(archive)


def test_refuses_absolute_symbolic_link(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    link_path = source / "unsafe"
    link_path.write_bytes(b"")
    absolute_target = str(Path(tmp_path.anchor) / "outside")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == link_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(os, "readlink", lambda _path: absolute_target)

    with pytest.raises(ValueError):
        pack(source, tmp_path / "bad.asar")


def test_refuses_symbolic_link_destination(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "app.asar"
    destination.write_bytes(b"")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == destination or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    with pytest.raises(ValueError, match="destination must not be a symbolic link"):
        pack(source, destination)
