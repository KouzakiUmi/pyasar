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
tested for archives created by pyasar itself. Before relying on a new Electron
release or an archive producer with legacy output, add a fixture from that
producer and test it with open_archive.

See the upstream format description and implementation:
https://github.com/electron/asar#format
