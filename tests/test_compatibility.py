import json
import os
import subprocess
from pathlib import Path

import pytest

from pyasar import AsarFormatError, open_archive, pack
from pyasar import writer


def mock_link(monkeypatch, alias, target):
    original = writer._is_link_entry
    monkeypatch.setattr(writer, "_is_link_entry", lambda p: p == alias or original(p))
    original_readlink = os.readlink
    monkeypatch.setattr(
        os, "readlink",
        lambda path, *args, **kwargs: str(target) if Path(path) == alias
        else original_readlink(path, *args, **kwargs),
    )


def test_unpack_predicate_overrides_extensions(tmp_path):
    source = tmp_path / "source"
    for folder in ("native", "assets"):
        (source / folder).mkdir(parents=True)
        (source / folder / "config.json").write_bytes(b"{}")
    (source / "module.node").write_bytes(b"module")
    archive = tmp_path / "app.asar"
    pack(source, archive, unpack=lambda p: p.parts[0] == "native")
    opened = open_archive(archive)
    assert opened.info("native/config.json")["unpacked"] is True
    assert "unpacked" not in opened.info("assets/config.json")
    assert "unpacked" not in opened.info("module.node")
    for name in opened.names():
        assert opened.read(name, verify=True) == (source / name).read_bytes()


@pytest.mark.parametrize("unpacked", [False, True])
def test_read_links_and_directory_aliases(tmp_path, monkeypatch, unpacked):
    source = tmp_path / "source"
    (source / "folder").mkdir(parents=True)
    (source / "folder/data.txt").write_bytes(b"data")
    alias = source / "alias"
    alias.write_bytes(b"placeholder")
    mock_link(monkeypatch, alias, "folder")
    archive = tmp_path / "app.asar"
    pack(source, archive, unpack=lambda p: unpacked and p.name == "data.txt")
    opened = open_archive(archive)
    assert opened.read("alias/data.txt", follow_links=True, verify=True) == b"data"
    assert opened.info("alias/data.txt", follow_links=True) == opened.info("folder/data.txt")
    with pytest.raises(KeyError):
        opened.read("alias/data.txt")
    with pytest.raises(IsADirectoryError):
        opened.read("alias", follow_links=True)


def test_resolver_cycles_missing_and_expansion_limit(tmp_path):
    from pyasar import AsarArchive
    archive = AsarArchive(tmp_path / "unused", {"files": {
        "a": {"link": "b"}, "b": {"link": "a"},
        "missing": {"link": "absent"}, "grow": {"link": "grow/child"},
    }}, 0)
    for name in ("a", "grow"):
        with pytest.raises(AsarFormatError):
            archive.read(name, follow_links=True)
    with pytest.raises(KeyError):
        archive.read("missing", follow_links=True)
    with pytest.raises(OSError):
        archive.read("a")


@pytest.mark.parametrize("target_selected", [False, True])
def test_unpacked_link_checks_target_before_creating_alias(tmp_path, monkeypatch, target_selected):
    source = tmp_path / "source"
    source.mkdir()
    alias = source / "alias.node"
    alias.write_bytes(b"placeholder")
    (source / "target.bin").write_bytes(b"data")
    mock_link(monkeypatch, alias, "target.bin")
    calls = []
    monkeypatch.setattr(Path, "symlink_to", lambda self, *args, **kwargs: calls.append(self))
    archive = tmp_path / "app.asar"
    if target_selected:
        pack(source, archive, unpack=lambda p: True)
        assert open_archive(archive).info("alias.node")["unpacked"] is True
        assert calls == [tmp_path / "app.asar.unpacked/alias.node"]
    else:
        with pytest.raises(ValueError, match="also be unpacked"):
            pack(source, archive)
        assert not calls
        assert not archive.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlinks")
def test_unpacked_alias_official_interop(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.node").write_bytes(b"native")
    (source / "alias.node").symlink_to("target.node")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.info("alias.node")["unpacked"] is True
    assert (tmp_path / "app.asar.unpacked/alias.node").is_symlink()
    assert opened.read("alias.node", follow_links=True, verify=True) == b"native"
    module = os.environ.get("PYASAR_OFFICIAL_MODULE")
    if module:
        result = subprocess.run(["node", "--input-type=module", "-e", """
import {pathToFileURL} from 'node:url';
const asar = await import(pathToFileURL(process.argv[1]));
console.log(JSON.stringify(asar.extractFile(process.argv[2], 'alias.node').toString()));
asar.extractAll(process.argv[2], process.argv[3]);
""", module, str(archive), str(tmp_path / "official-out")], check=True, capture_output=True, text=True)
        assert json.loads(result.stdout) == "native"
        assert (tmp_path / "official-out/alias.node").read_bytes() == b"native"


def test_selected_link_missing_target_and_creation_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    alias = source / "alias.node"
    alias.write_bytes(b"placeholder")
    mock_link(monkeypatch, alias, "target.node")
    archive = tmp_path / "app.asar"
    with pytest.raises(ValueError, match="target is missing"):
        pack(source, archive)
    (source / "target.node").write_bytes(b"native")
    with pytest.raises(ValueError, match="target is missing"):
        pack(source, archive, filter=lambda p: p.name != "target.node")
    def deny(*args, **kwargs):
        raise PermissionError("symlink permission denied")
    monkeypatch.setattr(Path, "symlink_to", deny)
    with pytest.raises(PermissionError, match="symlink permission"):
        pack(source, archive)
    assert not archive.exists()


def test_selected_directory_link_requires_unpacked_subtree(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "folder/empty").mkdir(parents=True)
    (source / "folder/data.txt").write_bytes(b"data")
    alias = source / "alias"
    alias.write_bytes(b"placeholder")
    mock_link(monkeypatch, alias, "folder")
    calls = []
    monkeypatch.setattr(Path, "symlink_to", lambda self, *args, **kw: calls.append(kw))
    archive = tmp_path / "app.asar"
    with pytest.raises(ValueError, match="also be unpacked"):
        pack(source, archive, unpack=lambda p: p.name == "alias")
    assert not calls
    pack(source, archive, unpack=lambda p: True)
    assert calls == [{"target_is_directory": True}]
    assert (tmp_path / "app.asar.unpacked/folder/empty").is_dir()
