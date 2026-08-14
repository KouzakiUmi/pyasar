import sys

from pyasar import open_archive


archive = open_archive(sys.argv[1])
for name in archive.names():
    print(name, archive.info(name))
