import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

from pyasar import AsarFormatError, open_archive, pack


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
             "-Path $env:PYASAR_TEST_LINK -Target $env:PYASAR_TEST_TARGET | Out-Null"],
            check=True, capture_output=True,
            env={**os.environ, "PYASAR_TEST_LINK": str(link),
                 "PYASAR_TEST_TARGET": str(target)},
        )
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("nested", [False, True])
def test_pack_preserves_source_inside_sidecar(tmp_path, nested):
    archive = tmp_path / "app.asar"
    archive.write_bytes(b"original archive")
    source = tmp_path / "app.asar.unpacked"
    if nested:
        source /= "nested"
    source.mkdir(parents=True)
    content = source / "important.txt"
    content.write_bytes(b"original source")
    with pytest.raises(ValueError, match="must not contain"):
        pack(source, archive)
    assert content.read_bytes() == b"original source"
    assert archive.read_bytes() == b"original archive"


@pytest.mark.parametrize("root_link", [False, True])
def test_unpacked_links_cannot_read_external_files(tmp_path, root_link):
    archive = tmp_path / "app.asar"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.node").write_bytes(b"private")
    sidecar = tmp_path / "app.asar.unpacked"
    files = {"private.node": {"size": 7, "unpacked": True}}
    name = "private.node"
    if root_link:
        _directory_link(sidecar, outside)
    else:
        sidecar.mkdir()
        _directory_link(sidecar / "external", outside)
        files = {"external": {"files": files}}
        name = "external/private.node"
    _write_archive_header(archive, {"files": files})
    opened = open_archive(archive)
    with pytest.raises(AsarFormatError, match="unpacked"):
        opened.read(name)
    with pytest.raises(AsarFormatError, match="unpacked"):
        opened.extract(tmp_path / "out")
    assert not (tmp_path / "out" / name).exists()
    assert (outside / "private.node").read_bytes() == b"private"


def test_directory_link_chains_and_cycles(tmp_path, monkeypatch):
    archive = tmp_path / "app.asar"
    _write_archive_header(archive, {"files": {
        "alias": {"link": "middle/sub"},
        "middle": {"link": "folder"},
        "folder": {"files": {"sub": {"files": {}}}},
    }})
    calls = []
    monkeypatch.setattr(Path, "symlink_to", lambda self, target, **kw:
                        calls.append((self.name, kw["target_is_directory"])))
    open_archive(archive).extract(tmp_path / "out")
    assert calls == [("alias", True), ("middle", True)]
    _write_archive_header(archive, {"files": {
        "a": {"link": "b"}, "b": {"link": "a"},
    }})
    with pytest.raises(AsarFormatError, match="circular"):
        open_archive(archive).extract(tmp_path / "cycle")


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
