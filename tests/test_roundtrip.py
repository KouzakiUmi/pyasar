import hashlib
import os
from pathlib import Path

import pytest

from pyasar import extract, open_archive, pack


def test_pack_read_verify_and_extract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "hello.txt").write_text("hello", encoding="utf-8")
    (source / "native.node").write_bytes(b"native")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.names() == ["native.node", "nested/hello.txt"]
    assert opened.read("nested/hello.txt", verify=True) == b"hello"
    assert opened.read("native.node", verify=True) == b"native"
    destination = tmp_path / "out"
    extract(archive, destination, verify=True)
    assert (destination / "nested" / "hello.txt").read_text() == "hello"
    assert (destination / "native.node").read_bytes() == b"native"


def test_extract_preserves_empty_directories_and_info_rejects_them(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "empty").mkdir(parents=True)
    archive = tmp_path / "app.asar"
    pack(source, archive)

    opened = open_archive(archive)
    with pytest.raises(IsADirectoryError):
        opened.info("empty")

    destination = tmp_path / "out"
    opened.extract(destination)
    assert (destination / "empty").is_dir()


def test_integrity_contains_electron_block_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    content = b"block-data"
    (source / "main.js").write_bytes(content)
    archive = tmp_path / "app.asar"

    pack(source, archive)

    integrity = open_archive(archive).info("main.js")["integrity"]
    expected = hashlib.sha256(content).hexdigest()
    assert integrity["hash"] == expected
    assert integrity["blocks"] == [expected]


def test_pack_can_reuse_destination_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    content = source / "main.js"
    content.write_text("first", encoding="utf-8")
    archive = source / "app.asar"

    pack(source, archive)
    content.write_text("second", encoding="utf-8")
    pack(source, archive)

    opened = open_archive(archive)
    assert opened.names() == ["main.js"]
    assert opened.read("main.js", verify=True) == b"second"


def test_repack_removes_stale_unpacked_sidecar(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    native = source / "native.node"
    native.write_bytes(b"native")
    archive = tmp_path / "app.asar"

    pack(source, archive)
    assert archive.with_name("app.asar.unpacked").is_dir()

    native.unlink()
    (source / "main.js").write_bytes(b"main")
    pack(source, archive)

    assert not archive.with_name("app.asar.unpacked").exists()


def test_nested_symlink_uses_archive_root_relative_target(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "target.txt").write_text("target", encoding="utf-8")
    link_path = nested / "alias.txt"
    link_path.write_bytes(b"")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == link_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(os, "readlink", lambda _path: os.path.join("..", "target.txt"))

    archive = tmp_path / "app.asar"
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.info("nested/alias.txt")["link"] == "target.txt"

    created_targets = []

    def capture_target(
        self: Path, target: str, target_is_directory: bool = False
    ) -> None:
        created_targets.append((self, target, target_is_directory))

    monkeypatch.setattr(Path, "symlink_to", capture_target)
    destination = tmp_path / "out"
    opened.extract(destination)

    assert created_targets == [
        (destination / "nested" / "alias.txt", os.path.join("..", "target.txt"), False)
    ]
