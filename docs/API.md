# Public API reference

The package exports the following names from pyasar:
AsarArchive, AsarError, AsarFormatError, DEFAULT_UNPACK_EXTENSIONS,
open_archive, extract, and pack. All path arguments accept a string or a
pathlib-compatible path object.

This reference describes version 0.1.0. Attributes or helpers beginning with an
underscore are implementation details and are not part of the compatibility
promise.

## open_archive(path)

Opens path and returns an AsarArchive.

Opening parses the modern Electron Pickle header and walks the complete file
table. It checks path safety, directory-node shape, regular-file size and
offset types, and that packed ranges remain within the archive file. It does
not read file payloads and it does not verify SHA-256 hashes at this point.
The declared header length must fit within the archive and the decoded header
Pickle is limited to 50 MiB.

It raises FileNotFoundError or PermissionError for normal filesystem failures,
and AsarFormatError when the archive has an invalid or truncated header or file
table. A valid header is not proof that each payload is present: a file can be
truncated after opening, and an unpacked sidecar is checked only when read.

## AsarArchive

An immutable metadata object returned by open_archive. It has these public
attributes:

- path: pathlib.Path of the archive supplied to open_archive.
- header: decoded ASAR JSON object. Treat it as read-only. It is exposed for
  inspection and is not a stable schema abstraction.
- base_offset: integer byte offset at which packed payloads begin.

### names()

Returns a list of POSIX-style relative paths for regular files and symbolic
links, in JSON header insertion order. Directory nodes are not returned.

### info(name)

Returns the raw header dictionary for one regular-file or symbolic-link entry.
name is normalized as a POSIX relative path; backslashes are accepted as input
separators. Absolute paths, parent traversal and an empty path raise
AsarFormatError. A missing entry raises KeyError, and a directory raises
IsADirectoryError.

The returned dictionary follows the raw ASAR schema. In particular, regular
file offsets are strings and not integers. Do not mutate it.

### read(name, verify=False)

Returns the complete bytes of a regular file. Symbolic links cannot be read as
files and raise OSError. The method applies the same name, missing-entry and
directory rules as info.

For packed files, size bytes are read at base_offset plus the declared offset.
For unpacked files, bytes are read from the sibling directory whose name is the
archive filename plus .unpacked. For example, app.asar and lib/native.node map
to app.asar.unpacked/lib/native.node.

If the requested number of bytes cannot be read, EOFError is raised. A missing
unpacked sidecar raises FileNotFoundError.

When verify is false, no digest is calculated. When it is true and the node has
a truthy integrity.hash value, the complete content is hashed with SHA-256 and
compared to that value. A mismatch raises AsarError. Archives without that
metadata still read successfully; verify is therefore not an authenticity
mechanism.

### extract(destination, verify=False)

Extracts every file and relative symbolic link into destination. Missing
directories are created. destination itself may already exist, but pyasar
refuses to overwrite any file or link entry that already exists there and raises
FileExistsError. This makes repeated extraction into the same directory fail
unless its prior contents are removed by the caller.

Every archive name is checked for traversal. ASAR symbolic-link targets are
relative to the archive root. They are converted to filesystem-relative links
when extracted and must resolve inside the extraction directory; otherwise
AsarFormatError is raised. Existing symlinked parent directories that escape
the destination are also rejected. This operation is not transactional: if a
later entry fails, files extracted before it remain in destination.

## extract(path, destination, verify=False)

Convenience function equivalent to opening path and calling its extract
method. It returns None and raises the same exceptions as those two operations.

## DEFAULT_UNPACK_EXTENSIONS

Frozen set containing .node. It is the default value used by pack. Pass an empty
set to pack to keep native modules inside the ASAR.

## pack(source, destination, unpack_extensions=DEFAULT_UNPACK_EXTENSIONS, filter=None)

Creates a modern Electron-compatible ASAR from the source directory and returns
None. source must be an existing directory or NotADirectoryError is raised.
destination and its parent are created or overwritten directly; pack is not an
atomic publishing operation.

Regular files are recursively added in lexicographic path order. The writer
creates Electron-compatible whole-file and 4 MiB block SHA-256 integrity
metadata for each file. Files with a lowercase suffix present in
unpack_extensions are copied to a sidecar at destination.asar.unpacked and
marked unpacked.

Symbolic links are resolved and represented as archive-root-relative ASAR link
nodes. Links whose targets fall outside source are refused with ValueError.

filter, when supplied, is called once for every discovered relative Path,
including directories. Returning false omits that item. It does not prune
directory traversal: a child may still be included and will recreate its parent
node. Filter out every member of a subtree when it must be excluded completely.

The destination ASAR and its sidecar are excluded if they live below source, so
the same destination can be reused safely. Existing sidecar contents are
removed before packing to prevent stale unpacked files.

## Exception hierarchy

AsarError is the package base exception. AsarFormatError is raised for malformed
or unsafe archive structure and subclasses AsarError. Integrity mismatches also
raise AsarError. Filesystem exceptions are intentionally not wrapped, so callers
can distinguish FileNotFoundError, PermissionError, FileExistsError, EOFError,
NotADirectoryError and OSError using standard Python handling.

## Version and stability

pyasar.__version__ reports the installed package version. Public names and the
documented exception behavior follow semantic versioning. The raw header
dictionary, generated JSON ordering and byte-for-byte archive layout should not
be relied on for reproducible-build guarantees.
