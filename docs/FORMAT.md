# ASAR format and compatibility

pyasar version 0.1.0 supports the current Electron Pickle-framed JSON ASAR
format. It intentionally does not read legacy Chromium header variants.

## Byte layout

All integer fields are unsigned 32-bit little-endian values.

    [4: size Pickle payload length, always 4]
    [4: header Pickle byte length]
    [4: header Pickle payload length]
    [4: JSON byte length]
    [N: UTF-8 JSON bytes]
    [0 to 3: zero padding]
    [packed file payloads]

The header Pickle byte length includes its leading payload-length field, the
JSON-length field and padding. Packed file offsets in the JSON header are
decimal strings relative to the first packed payload byte, not to the beginning
of the archive.

## Header nodes

The root object contains a files object. A directory node has its own files
object. A regular file node has at least size and offset fields. An unpacked
regular file additionally has unpacked set to true; its contents live in the
sibling .unpacked tree instead of the ASAR payload area. A link node has a link
string and has no file payload.

pyasar writes integrity metadata with algorithm SHA256, a whole-file hash, a
4 MiB blockSize and one hash per block. Empty files have one hash for the empty
block, matching current Electron ASAR tooling. The reader only uses
integrity.hash when verification is requested; it does not currently verify
block hashes individually.

## Interoperability boundary

The layout follows the format used by Electron ASAR tooling. Compatibility is
tested with pyasar round trips and bidirectional integration against official
@electron/asar 4.3.0. The integration test covers packed and unpacked files,
Unicode names, empty directories, and SHA-256 blocks at the 4 MiB boundary.
To run it, install @electron/asar outside this project, set
PYASAR_OFFICIAL_MODULE to its absolute lib/asar.js path, and run pytest with
Node.js available on PATH. Without that variable the integration test is skipped.

pyasar additionally restricts sidecar filesystem links and preserves symbolic
links on Windows. These are intentional API differences from the official tool.

See the upstream format description and implementation:
https://github.com/electron/asar#format
