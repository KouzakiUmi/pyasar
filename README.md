# pyasar

Dependency-free Python tooling for Electron ASAR archives. It can inspect,
validate, read, extract and create the modern JSON-header ASAR layout.

The package rejects malformed headers and payload ranges, extraction path
traversal and escaping link targets. Payloads can be checked against SHA-256
integrity metadata, and missing or truncated unpacked files fail explicitly.

## Install

    pip install .

For development and tests:

    pip install -e .[dev]

## Quick start

    from pyasar import open_archive, pack
    pack("app", "app.asar")
    archive = open_archive("app.asar")
    data = archive.read("main.js", verify=True)

Native .node files are unpacked by default to app.asar.unpacked. Pass an empty
unpack_extensions set to keep every file in the archive. The API exports
DEFAULT_UNPACK_EXTENSIONS for callers that need the default explicitly.

## Compatibility

pyasar reads and writes the current Electron ASAR JSON header format, including
regular files, symbolic links, SHA-256 integrity entries and unpacked sidecars.
It does not implement historical Chromium ASAR header variants. The public API,
exception behavior and security model are in [docs/API.md](docs/API.md).
The on-disk layout and interoperability boundary are in
[docs/FORMAT.md](docs/FORMAT.md).
