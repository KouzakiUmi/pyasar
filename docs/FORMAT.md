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
object. A packed regular file node has at least size and offset fields. An unpacked
regular file has size and unpacked set to true, with no offset required; its contents live in the
sibling .unpacked tree instead of the ASAR payload area. A link node has a link
string and has no file payload.

An unpacked link also has unpacked set to true and a relative filesystem link
in the sidecar. pyasar requires its target to be present there; selected
directory links require all descendant leaves to be unpacked. The unpack
callback selects files and links by relative path, independently of suffixes.

The link string is archive-root-relative. info/read can resolve file links,
directory links and chains with follow_links=True; the default retains raw
link metadata and refuses to read links. Resolution detects cycles and limits
link expansion. Unpacked reads use the resolved target's sidecar path.

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
POSIX compatibility tests also check that official extractFile and extractAll
can consume pyasar's unpacked symbolic links.
To run it, install @electron/asar outside this project, set
PYASAR_OFFICIAL_MODULE to its absolute lib/asar.js path, and run pytest with
Node.js available on PATH. Without that variable the integration test is skipped.

pyasar additionally restricts sidecar filesystem links and preserves symbolic
links on Windows. These are intentional API differences from the official tool.

Newer @electron/asar releases may deduplicate identical file contents, so
several entries can share one offset; pyasar reads such archives normally but
never writes them that way. The official writer refuses individual files larger
than 4 GiB; pyasar packs them when the destination volume supports them (on
FAT-family volumes, oversized single files and oversized archives are rejected
up front), and the decimal-string offset format stays valid, but such archives
exceed what the official tool itself creates.

See the upstream format description and implementation:
https://github.com/electron/asar#format
