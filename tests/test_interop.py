"""Optional integration test against an installed official @electron/asar."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from pyasar import open_archive, pack


@pytest.mark.skipif(
    not os.environ.get("PYASAR_OFFICIAL_MODULE"),
    reason="set PYASAR_OFFICIAL_MODULE to @electron/asar/lib/asar.js",
)
def test_official_bidirectional_interoperability(tmp_path):
    source = tmp_path / "source"
    (source / "empty-dir").mkdir(parents=True)
    contents = {
        "empty.txt": b"",
        "exact.bin": b"x" * (4 * 1024 * 1024),
        "tail.bin": b"y" * (4 * 1024 * 1024 + 1),
        "native.node": b"native",
        "unicode-中文.txt": b"utf8 filename",
    }
    for name, data in contents.items():
        (source / name).write_bytes(data)
    ours = tmp_path / "ours.asar"
    official = tmp_path / "official.asar"
    pack(source, ours)
    script = """
import { pathToFileURL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const [modulePath, source, ours, official] = process.argv.slice(1);
const asar = await import(pathToFileURL(modulePath));
await asar.createPackageWithOptions(source, official, { unpack: '*.node' });
const metadata = {};
for (const name of fs.readdirSync(source)) {
  if (!fs.statSync(path.join(source, name)).isFile()) continue;
  assert.deepEqual(asar.extractFile(ours, name), fs.readFileSync(path.join(source, name)));
  metadata[name] = asar.statFile(official, name);
}
asar.extractAll(ours, path.join(path.dirname(ours), 'official-out'));
console.log(JSON.stringify(metadata));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script,
         str(Path(os.environ["PYASAR_OFFICIAL_MODULE"]).resolve()),
         str(source), str(ours), str(official)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    metadata = json.loads(result.stdout)
    opened = open_archive(official)
    opened.extract(tmp_path / "python-out", verify=True)
    for name, data in contents.items():
        assert opened.read(name, verify=True) == data
        assert open_archive(ours).info(name)["integrity"] == metadata[name]["integrity"]
        assert (tmp_path / "official-out" / name).read_bytes() == data
        assert (tmp_path / "python-out" / name).read_bytes() == data
    assert (tmp_path / "official-out" / "empty-dir").is_dir()
    assert (tmp_path / "python-out" / "empty-dir").is_dir()
