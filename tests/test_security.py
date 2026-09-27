import json
import os
import stat
import struct
import subprocess
import types
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


def _write_raw_header(path: Path, encoded: bytes) -> None:
    padding = (-len(encoded)) % 4
    payload = struct.pack("<I", len(encoded)) + encoded + b"\x00" * padding
    header_pickle = struct.pack("<I", len(payload)) + payload
    path.write_bytes(struct.pack("<II", 4, len(header_pickle)) + header_pickle)


def test_rejects_oversized_decimal_offset(tmp_path) -> None:
    archive = tmp_path / "big-offset.asar"
    _write_raw_header(
        archive, b'{"files":{"a.txt":{"offset":"' + b"9" * 5000 + b'","size":0}}}'
    )

    with pytest.raises(AsarFormatError, match="offset"):
        open_archive(archive)


def test_rejects_non_ascii_decimal_offset(tmp_path) -> None:
    archive = tmp_path / "unicode-offset.asar"
    _write_archive_header(
        archive, {"files": {"a.txt": {"offset": "\u00b2", "size": 0}}}
    )

    with pytest.raises(AsarFormatError, match="offset"):
        open_archive(archive)


def test_rejects_oversized_json_integer(tmp_path) -> None:
    archive = tmp_path / "big-size.asar"
    _write_raw_header(
        archive, b'{"files":{"a.txt":{"offset":"0","size":1' + b"0" * 5000 + b"}}}"
    )

    with pytest.raises(AsarFormatError):
        open_archive(archive)


def test_rejects_deeply_nested_header(tmp_path) -> None:
    archive = tmp_path / "deep.asar"
    _write_raw_header(archive, b'{"files":{"n":' * 300 + b"{}" + b"}}" * 300)

    with pytest.raises(AsarFormatError):
        open_archive(archive)


def test_rejects_nul_byte_in_link_target(tmp_path) -> None:
    archive = tmp_path / "nul-link.asar"
    _write_archive_header(archive, {"files": {"a": {"link": "dir\u0000name"}}})

    with pytest.raises(AsarFormatError, match="unsafe archive path"):
        open_archive(archive)


def test_rejects_drive_letter_link_target(tmp_path) -> None:
    archive = tmp_path / "drive-link.asar"
    _write_archive_header(archive, {"files": {"a": {"link": "C:/x"}}})

    with pytest.raises(AsarFormatError, match="unsafe archive path"):
        open_archive(archive)


def test_rejects_drive_letter_entry_names(tmp_path) -> None:
    archive = tmp_path / "drive-name.asar"
    _write_archive_header(
        archive,
        {"files": {"C:": {"files": {"x.txt": {"offset": "0", "size": 0}}}}},
    )

    with pytest.raises(AsarFormatError, match="invalid ASAR entry name"):
        open_archive(archive)


def test_pack_directory_link_is_not_expanded(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "f.txt").write_bytes(b"data")
    _directory_link(source / "loop", source / "sub")

    pack(source, tmp_path / "app.asar")
    opened = open_archive(tmp_path / "app.asar")
    assert opened.names() == ["loop", "sub/f.txt"]
    assert opened.info("loop") == {"link": "sub"}
    assert opened.read("sub/f.txt") == b"data"


def test_pack_self_referencing_link_does_not_hang(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "f.txt").write_bytes(b"data")
    _directory_link(source / "sub" / "inner", source / "sub")

    pack(source, tmp_path / "app.asar")
    opened = open_archive(tmp_path / "app.asar")
    assert opened.names() == ["sub/f.txt", "sub/inner"]
    assert opened.info("sub/inner") == {"link": "sub"}


def test_pack_link_to_source_root_is_refused(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _directory_link(source / "loop", source)

    with pytest.raises(ValueError, match="source root"):
        pack(source, tmp_path / "app.asar")


def test_pack_removes_junction_sidecar_without_following(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.js").write_bytes(b"main")
    archive = tmp_path / "app.asar"
    _directory_link(archive.with_name("app.asar.unpacked"), outside)

    pack(source, archive)
    assert not archive.with_name("app.asar.unpacked").exists()
    assert (outside / "keep.txt").read_bytes() == b"keep"


def test_writer_rejects_over_u32_structure_fields() -> None:
    from pyasar.writer import _pickle

    with pytest.raises(ValueError, match="4 GiB"):
        _pickle(2**32)


def test_rejects_boolean_size(tmp_path) -> None:
    archive = tmp_path / "bool-size.asar"
    _write_archive_header(archive, {"files": {"x.txt": {"offset": "0", "size": True}}})

    with pytest.raises(AsarFormatError, match="size"):
        open_archive(archive)


def test_resolve_link_target_maps_unc_verbatim_prefix(
    tmp_path, monkeypatch
) -> None:
    from pyasar.writer import _resolve_link_target

    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    item = tmp_path / "source" / "remote"
    target = _resolve_link_target(item, "\\\\?\\UNC\\invalid.invalid\\share\\data")
    assert str(target) == "\\\\invalid.invalid\\share\\data"


def test_pack_refuses_junction_to_unc_target(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.js").write_bytes(b"main")
    link_path = source / "remote"
    link_path.write_bytes(b"")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        return path == link_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(
        os, "readlink", lambda _path: "\\\\?\\UNC\\invalid.invalid\\share\\data"
    )
    monkeypatch.setattr(os, "getcwd", lambda: str(source))

    with pytest.raises(ValueError, match="outside source"):
        pack(source, tmp_path / "app.asar")


def test_pack_treats_cloud_reparse_points_as_regular_files(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.js").write_bytes(b"main")
    cloud = source / "cloudfile.bin"
    cloud.write_bytes(b"cloud")
    tag = getattr(stat, "IO_REPARSE_TAG_CLOUD", 0x90000000)
    original_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if str(path).endswith("cloudfile.bin"):
            fields = {
                name: getattr(result, name)
                for name in dir(result)
                if name.startswith("st_")
            }
            fields["st_file_attributes"] = stat.FILE_ATTRIBUTE_REPARSE_POINT
            fields["st_reparse_tag"] = tag
            return types.SimpleNamespace(**fields)
        return result

    monkeypatch.setattr(os, "lstat", fake_lstat)

    pack(source, tmp_path / "app.asar")
    opened = open_archive(tmp_path / "app.asar")
    assert opened.read("cloudfile.bin") == b"cloud"
