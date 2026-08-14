"""Safe, dependency-free Electron ASAR tooling."""

from .archive import AsarArchive, AsarError, AsarFormatError, extract, open_archive
from .writer import DEFAULT_UNPACK_EXTENSIONS, pack

__all__ = [
    "AsarArchive",
    "AsarError",
    "AsarFormatError",
    "DEFAULT_UNPACK_EXTENSIONS",
    "extract",
    "open_archive",
    "pack",
]
__version__ = "0.1.0"
