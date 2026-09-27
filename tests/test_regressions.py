import json
import os
import shutil
import struct
import sys
from pathlib import Path

import pytest

import pyasar.writer
from pyasar import AsarFormatError, open_archive, pack


def _write_header_archive(path: Path, header: dict) -> None:
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    _write_raw_header(path, encoded)


def _write_raw_header(path: Path, encoded: bytes) -> None:
    padding = (-len(encoded)) % 4
    payload = struct.pack("<I", len(encoded)) + encoded + b"\x00" * padding
    header_pickle = struct.pack("<I", len(payload)) + payload
    path.write_bytes(struct.pack("<II", 4, len(header_pickle)) + header_pickle)


def test_open_archive_rejects_null_files(tmp_path):
    # files: null used to be treated as a file leaf instead of malformed.
    archive = tmp_path / "app.asar"
    _write_header_archive(
        archive,
        {"files": {"a.txt": {"size": 1, "offset": "0", "files": None}}},
    )
    with pytest.raises(AsarFormatError, match="files"):
        open_archive(archive)


def test_open_archive_rejects_undecodable_name(tmp_path):
    # JSON can carry lone surrogates that no UTF-8 header could store.
    archive = tmp_path / "app.asar"
    _write_raw_header(
        archive, b'{"files":{"\\udcff.txt":{"size":1,"offset":"0"}}}'
    )
    with pytest.raises(AsarFormatError, match="UTF-8"):
        open_archive(archive)


@pytest.mark.parametrize(
    "integrity",
    [{"hash": 123, "blocks": []}, {"hash": None, "blocks": []}, 42],
)
def test_verify_rejects_malformed_integrity(tmp_path, integrity):
    archive = tmp_path / "app.asar"
    _write_header_archive(
        archive,
        {"files": {"a.txt": {"size": 4, "offset": "0", "integrity": integrity}}},
    )
    with archive.open("ab") as stream:
        stream.write(b"data")
    with pytest.raises(AsarFormatError, match="integrity"):
        open_archive(archive).read("a.txt", verify=True)


def test_read_unpacked_sidecar_not_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "native.node").write_bytes(b"native")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    sidecar = tmp_path / "app.asar.unpacked"
    shutil.rmtree(sidecar)
    sidecar.write_bytes(b"impostor")
    with pytest.raises(NotADirectoryError):
        open_archive(archive).read("native.node")


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink creation")
def test_pack_rejects_dangling_link_with_drive_segment(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "alias").symlink_to("C:evil")
    with pytest.raises(ValueError, match="unsupported symbolic link target"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink creation")
def test_pack_rejects_link_with_backslash_target(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "alias").symlink_to("back\\slash.txt")
    with pytest.raises(ValueError, match="unsupported symbolic link target"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(os.name == "nt", reason="Windows filenames are decodable")
def test_pack_rejects_non_utf8_filename(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    descriptor = os.open(
        os.path.join(os.fsencode(source), b"bad-\xff.txt"),
        os.O_CREAT | os.O_WRONLY,
    )
    os.close(descriptor)
    with pytest.raises(ValueError, match="unsupported entry name"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(os.name == "nt", reason="exceeds Windows path limits")
def test_pack_rejects_deeply_nested_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    current = source
    for index in range(257):
        current = current / f"d{index:03d}"
    current.mkdir(parents=True)
    with pytest.raises(ValueError, match="depth limit"):
        pack(source, tmp_path / "app.asar")


def test_pack_rejects_oversized_header(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")
    monkeypatch.setattr("pyasar.writer.MAX_HEADER_SIZE", 20)
    with pytest.raises(ValueError, match="header"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires a case-sensitive, non-normalizing filesystem",
)
def test_pack_rejects_case_colliding_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "A.txt").write_bytes(b"a")
    (source / "a.txt").write_bytes(b"b")
    with pytest.raises(ValueError, match="collides"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires a case-sensitive, non-normalizing filesystem",
)
def test_pack_rejects_unicode_colliding_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "é.txt").write_bytes(b"a")  # NFC
    (source / "é.txt").write_bytes(b"b")  # NFD
    with pytest.raises(ValueError, match="collides"):
        pack(source, tmp_path / "app.asar")


@pytest.mark.parametrize("fake_size, match", [(2, "grew"), (8, "shrank")])
def test_pack_refuses_files_that_change_size(
    tmp_path, monkeypatch, fake_size, match
):
    source = tmp_path / "source"
    source.mkdir()
    target_file = source / "data.txt"
    target_file.write_bytes(b"1234")
    original_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = original_stat(self, *args, **kwargs)
        if self == target_file:
            values = list(result)
            values[6] = fake_size  # st_size lies about the real length
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    with pytest.raises(ValueError, match=match):
        pack(source, tmp_path / "app.asar")


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX mkfifo")
def test_pack_removes_special_file_sidecar(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "native.node").write_bytes(b"native")
    archive = tmp_path / "app.asar"
    os.mkfifo(tmp_path / "app.asar.unpacked")
    pack(source, archive)
    opened = open_archive(archive)
    assert opened.read("native.node", verify=True) == b"native"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink creation")
def test_extract_resolves_long_link_chain(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "real.txt").write_bytes(b"payload")
    chain = 42  # exceeds the old 40-hop reader cap
    (source / f"link_{chain:02d}").symlink_to("real.txt")
    for index in range(chain - 1, 0, -1):
        (source / f"link_{index:02d}").symlink_to(f"link_{index + 1:02d}")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    out = tmp_path / "out"
    open_archive(archive).extract(out)
    assert (out / "link_01").is_symlink()
    assert (out / "link_01").read_bytes() == b"payload"


def test_open_archive_rejects_mixed_directory_link_node(tmp_path):
    # A node carrying both files and link fields classifies as a directory
    # in the tree walk but as a link during link resolution.
    archive = tmp_path / "app.asar"
    _write_header_archive(
        archive,
        {"files": {"mixed": {"files": {}, "link": "elsewhere.txt"}}},
    )
    with pytest.raises(AsarFormatError, match="mixes"):
        open_archive(archive)


def test_open_archive_rejects_undecodable_link_target(tmp_path):
    archive = tmp_path / "app.asar"
    _write_raw_header(
        archive,
        b'{"files":{"real.txt":{"size":1,"offset":"0"},'
        b'"alias":{"link":"bad-\\udcff"}}}',
    )
    with archive.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(AsarFormatError, match="UTF-8"):
        open_archive(archive)


def _nested_directory_header(levels: int) -> dict:
    node: dict = {"files": {}}
    for _ in range(levels):
        node = {"files": {"d": node}}
    return node


def test_open_archive_directory_depth_boundary(tmp_path):
    archive = tmp_path / "app.asar"
    _write_header_archive(archive, _nested_directory_header(255))
    open_archive(archive)  # 255 components: the deepest the reader allows
    _write_header_archive(archive, _nested_directory_header(256))
    with pytest.raises(AsarFormatError, match="too deeply"):
        open_archive(archive)


@pytest.mark.skipif(os.name == "nt", reason="exceeds Windows path limits")
def test_pack_directory_depth_boundary(tmp_path):
    source = tmp_path / "source"
    source.mkdir()

    def nest(levels: int) -> Path:
        current = source
        for index in range(levels):
            current = current / f"d{index:03d}"
        current.mkdir(parents=True)
        return current

    nest(256)
    with pytest.raises(ValueError, match="depth limit"):
        pack(source, tmp_path / "app.asar")
    shutil.rmtree(source)
    source.mkdir()
    leaf = nest(255)
    archive = tmp_path / "app.asar"
    pack(source, archive)  # directories may nest exactly 255 components
    open_archive(archive)
    (leaf / "f.txt").write_bytes(b"data")
    pack(source, archive)  # a file leaf may still reach 256 components
    open_archive(archive)


def test_extract_windows_data_stream_name(tmp_path):
    archive = tmp_path / "app.asar"
    _write_header_archive(
        archive, {"files": {"a.txt:stream": {"size": 4, "offset": "0"}}}
    )
    with archive.open("ab") as stream:
        stream.write(b"data")
    out = tmp_path / "out"
    if os.name == "nt":
        with pytest.raises(AsarFormatError, match="data stream"):
            open_archive(archive).extract(out)
    else:
        # On POSIX the colon is an ordinary name character.
        open_archive(archive).extract(out)
        assert (out / "a.txt:stream").read_bytes() == b"data"


@pytest.mark.parametrize("extension", [".txt", ".node"])
def test_pack_refuses_files_that_change_content(tmp_path, monkeypatch, extension):
    # Same-size content changes between the header pass and the payload
    # copy used to produce archives that fail their own integrity check.
    source = tmp_path / "source"
    source.mkdir()
    target_file = source / f"data{extension}"
    target_file.write_bytes(b"aaaa")
    original_integrity = pyasar.writer._integrity

    def mutating_integrity(path):
        result = original_integrity(path)
        if path == target_file:
            path.write_bytes(b"bbbb")  # same size, different content
        return result

    monkeypatch.setattr(pyasar.writer, "_integrity", mutating_integrity)
    with pytest.raises(ValueError, match="changed content"):
        pack(source, tmp_path / "app.asar")


def test_pack_header_limit_counts_pickle_overhead(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"data")
    archive = tmp_path / "app.asar"
    pack(source, archive)
    with archive.open("rb") as stream:
        _, pickle_size = struct.unpack("<II", stream.read(8))
    monkeypatch.setattr("pyasar.writer.MAX_HEADER_SIZE", pickle_size)
    pack(source, tmp_path / "exact.asar")  # exactly at the reader's limit
    monkeypatch.setattr("pyasar.writer.MAX_HEADER_SIZE", pickle_size - 1)
    with pytest.raises(ValueError, match="header"):
        pack(source, tmp_path / "over.asar")
