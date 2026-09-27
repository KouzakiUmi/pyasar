import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from pyasar import extract, open_archive, pack


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX permission bits")
@pytest.mark.parametrize("suffix", [".sh", ".node"])
def test_executable_permissions_roundtrip(tmp_path, suffix):
    source = tmp_path / "source"
    source.mkdir()
    executable = source / ("helper" + suffix)
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o744)
    ordinary = source / "data.txt"
    ordinary.write_bytes(b"data")
    ordinary.chmod(0o644)
    archive = tmp_path / "app.asar"
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.info(executable.name)["executable"] is True
    assert "executable" not in opened.info(ordinary.name)
    opened.extract(tmp_path / "out", verify=True)
    assert (tmp_path / "out" / executable.name).stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / "out" / ordinary.name).stat().st_mode & 0o111


def _can_create_directory_link() -> bool:
    """Whether this process may create directory symlinks at all."""
    if os.name != "nt":
        return True
    probe_dir = tempfile.mkdtemp(prefix="pyasar-symlink-probe-")
    try:
        Path(probe_dir, "probe").symlink_to(probe_dir, target_is_directory=True)
        return True
    except OSError:
        return False
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_real_directory_link_roundtrip(tmp_path):
    if not _can_create_directory_link():
        pytest.skip("requires symlink privileges on Windows")
    source = tmp_path / "source"
    (source / "folder").mkdir(parents=True)
    (source / "folder" / "data").write_bytes(b"data")
    (source / "alias").symlink_to("folder", target_is_directory=True)
    archive = tmp_path / "app.asar"
    pack(source, archive)
    extract(archive, tmp_path / "out")
    assert (tmp_path / "out" / "alias").is_symlink()
    assert (tmp_path / "out" / "alias" / "data").read_bytes() == b"data"


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
    original_readlink = os.readlink
    monkeypatch.setattr(
        os, "readlink",
        lambda path, *args, **kwargs: os.path.join("..", "target.txt") if Path(path) == link_path
        else original_readlink(path, *args, **kwargs),
    )

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


def test_pack_records_linked_directory_without_expanding(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "f.txt").write_bytes(b"data")
    (source / "loop").mkdir()
    (source / "loop" / "f.txt").write_bytes(b"inner")
    link_path = source / "loop"
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == link_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    original_readlink = os.readlink
    monkeypatch.setattr(
        os, "readlink",
        lambda path, *args, **kwargs: "sub" if Path(path) == link_path
        else original_readlink(path, *args, **kwargs),
    )

    pack(source, tmp_path / "app.asar")
    opened = open_archive(tmp_path / "app.asar")
    assert opened.names() == ["loop", "sub/f.txt"]
    assert opened.info("loop") == {"link": "sub"}
    assert opened.read("sub/f.txt") == b"data"


def test_pack_warns_and_skips_special_files(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    special = source / "fifo"
    special.write_bytes(b"data")
    (source / "main.js").write_bytes(b"main")
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: False if path == special else original_is_file(path),
    )

    with pytest.warns(UserWarning, match="special file"):
        pack(source, tmp_path / "app.asar")

    assert open_archive(tmp_path / "app.asar").names() == ["main.js"]


def test_info_and_read_accept_path_objects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.js").write_bytes(b"content")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.info(Path("main.js"))["size"] == 7
    assert opened.read(Path("main.js")) == b"content"
    with pytest.raises(KeyError):
        opened.info(Path("missing.txt"))
