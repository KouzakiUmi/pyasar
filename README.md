# pyasar

[English](#english) | [简体中文](#简体中文)

`pyasar` is a dependency-free Python library for inspecting, validating, reading,
extracting, and creating modern Electron ASAR archives. It is a standalone
project and is not a dependency of DeviConHan.

---

## English

### Features

- Read and write Electron's current JSON-header ASAR format.
- Read packed files and `.asar.unpacked` sidecar files through one API.
- Generate whole-file and 4 MiB block SHA-256 integrity metadata.
- Preserve empty directories and archive-root-relative symbolic links.
- Reject malformed headers, invalid payload ranges, path traversal, escaping
  links, and symbolic-link output destinations.
- Repack safely when the destination is located inside the source tree: the
  output archive and its sidecar are excluded from the next pack.
- Run on Python 3.9 or newer with no runtime dependencies.

### Installation

Install the latest code directly from GitHub:

```console
python -m pip install "pyasar @ git+https://github.com/KouzakiUmi/pyasar.git"
```

Or clone the repository and install it locally:

```console
git clone https://github.com/KouzakiUmi/pyasar.git
cd pyasar
python -m pip install .
```

For development:

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

### Quick start

```python
from pyasar import extract, open_archive, pack

# Create an archive from a directory.
pack("app", "app.asar")

# Inspect and read it without extracting everything.
archive = open_archive("app.asar")
print(archive.names())
main_js = archive.read("main.js", verify=True)

# Extract all files and verify integrity metadata when present.
extract("app.asar", "output", verify=True)
```

### Inspect an archive

`open_archive()` parses and structurally validates the header without loading
all payloads into memory. `names()` returns regular files and symbolic links as
POSIX-style paths; directory nodes are not included.

```python
from pyasar import open_archive

archive = open_archive("app.asar")

for name in archive.names():
    metadata = archive.info(name)
    print(name, metadata.get("size"), metadata.get("unpacked", False))
```

The repository also includes a small inspection example:

```console
python examples/inspect.py path/to/app.asar
```

### Read individual files

```python
from pyasar import open_archive

archive = open_archive("app.asar")
source = archive.read("dist/main.js")
verified_source = archive.read("dist/main.js", verify=True)
```

With `verify=True`, `pyasar` compares the payload with `integrity.hash` when
that field exists. An archive without integrity metadata can still be read, so
this option detects corruption but does not establish publisher authenticity.

For an unpacked entry, `read()` automatically loads the corresponding file
from the sibling sidecar. For example:

```text
app.asar                          # header and packed payloads
app.asar.unpacked/native/addon.node
```

### Extract an archive

Use either the convenience function or the opened archive object:

```python
from pyasar import extract, open_archive

extract("app.asar", "output", verify=True)

archive = open_archive("another.asar")
archive.extract("another-output")
```

Extraction creates missing and empty directories. It refuses to overwrite an
existing file or symbolic link. Extraction is not transactional: if a later
entry fails, files written earlier remain in the destination.

On Windows, creating symbolic links may require Developer Mode or an elevated
process. Normal filesystem filename restrictions also apply on the target
platform.

### Create an archive

By default, native `.node` modules are placed in `app.asar.unpacked`, matching
the usual Electron layout:

```python
from pyasar import pack

pack("application-directory", "app.asar")
```

Keep every regular file inside the archive by passing an empty set:

```python
pack("application-directory", "app.asar", unpack_extensions=set())
```

Or choose additional unpacked extensions:

```python
pack(
    "application-directory",
    "app.asar",
    unpack_extensions={".node", ".dll"},
)
```

Extension matching is case-insensitive for source filenames; provide lowercase
extensions in the set.

Existing `app.asar.unpacked` content is removed before packing so deleted native
files cannot survive in a stale sidecar. The destination archive itself is
overwritten directly and packing is not transactional.

### Filter files while packing

The filter receives a `pathlib.Path` relative to the source directory:

```python
from pathlib import Path

from pyasar import pack


def include(path: Path) -> bool:
    return ".git" not in path.parts and path.suffix.lower() != ".map"


pack("app", "app.asar", filter=include)
```

The filter is called for both files and directories. Returning `False` for a
directory does not prune traversal by itself, so test `path.parts` when an
entire subtree must be excluded.

### Error handling

```python
from pyasar import AsarError, AsarFormatError, open_archive

try:
    archive = open_archive("app.asar")
    data = archive.read("main.js", verify=True)
except AsarFormatError as error:
    print(f"Unsafe or malformed ASAR: {error}")
except AsarError as error:
    print(f"Integrity failure: {error}")
except (FileNotFoundError, PermissionError, EOFError) as error:
    print(f"Filesystem or truncated-payload error: {error}")
```

Filesystem exceptions intentionally remain standard Python exceptions. In
particular, missing sidecar files raise `FileNotFoundError`, truncated payloads
raise `EOFError`, existing extraction targets raise `FileExistsError`, and a
directory passed to `info()` raises `IsADirectoryError`.

### Compatibility and security boundary

`pyasar` supports the modern Electron Pickle container with a JSON file table,
regular files, unpacked files, relative links, and SHA-256 integrity entries. It
does not implement historical Chromium ASAR header variants.

Archive entry names and link targets are validated before access or extraction.
The decoded header Pickle is limited to 50 MiB, and packed file ranges must fit
inside the archive. These checks reduce accidental and malicious filesystem
access, but they do not make untrusted executable content safe to run.

For the exact API and format behavior, see [docs/API.md](docs/API.md) and
[docs/FORMAT.md](docs/FORMAT.md).

---

## 简体中文

### 功能概览

- 读取和写入 Electron 当前使用的 JSON 头部 ASAR 格式。
- 使用同一套 API 读取归档内文件和 `.asar.unpacked` 旁挂目录文件。
- 生成整文件以及 4 MiB 分块的 SHA-256 完整性元数据。
- 保留空目录和以归档根目录为基准的符号链接。
- 拒绝格式错误的头部、越界数据、路径穿越、逃逸符号链接以及符号链接形式的输出文件。
- 输出 ASAR 位于源目录内部时也可重复打包；下次打包会自动排除输出文件和旁挂目录。
- 支持 Python 3.9 及以上版本，无运行时第三方依赖。

### 安装

直接从 GitHub 安装最新代码：

```console
python -m pip install "pyasar @ git+https://github.com/KouzakiUmi/pyasar.git"
```

也可以克隆仓库后本地安装：

```console
git clone https://github.com/KouzakiUmi/pyasar.git
cd pyasar
python -m pip install .
```

开发环境安装与测试：

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

### 快速开始

```python
from pyasar import extract, open_archive, pack

# 将目录打包为 ASAR。
pack("app", "app.asar")

# 在不解包全部文件的情况下检查并读取内容。
archive = open_archive("app.asar")
print(archive.names())
main_js = archive.read("main.js", verify=True)

# 解包所有文件，并在完整性元数据存在时进行校验。
extract("app.asar", "output", verify=True)
```

### 检查归档

`open_archive()` 会解析并验证头部结构，但不会把所有文件数据载入内存。
`names()` 返回普通文件和符号链接的 POSIX 风格路径，不包含目录节点。

```python
from pyasar import open_archive

archive = open_archive("app.asar")

for name in archive.names():
    metadata = archive.info(name)
    print(name, metadata.get("size"), metadata.get("unpacked", False))
```

仓库中还提供了一个简单的检查示例：

```console
python examples/inspect.py path/to/app.asar
```

### 读取单个文件

```python
from pyasar import open_archive

archive = open_archive("app.asar")
source = archive.read("dist/main.js")
verified_source = archive.read("dist/main.js", verify=True)
```

设置 `verify=True` 后，如果条目包含 `integrity.hash`，`pyasar` 会比较文件内容与该哈希值。
没有完整性元数据的归档仍然可以读取，因此这个选项可以发现损坏，但不能证明发布者身份。

如果条目标记为 unpacked，`read()` 会自动从同名旁挂目录读取对应文件。例如：

```text
app.asar                          # 头部和归档内数据
app.asar.unpacked/native/addon.node
```

### 解包归档

可以使用便捷函数，也可以调用已打开归档对象的方法：

```python
from pyasar import extract, open_archive

extract("app.asar", "output", verify=True)

archive = open_archive("another.asar")
archive.extract("another-output")
```

解包时会创建缺失目录和空目录，但不会覆盖已经存在的文件或符号链接。解包并非事务操作：
如果后续条目失败，先前已写入目标目录的文件会保留。

在 Windows 上创建符号链接可能需要开启“开发人员模式”或以提升权限运行。目标平台自身的文件名限制同样适用。

### 创建归档

默认情况下，原生 `.node` 模组会被放入 `app.asar.unpacked`，与 Electron 常见布局一致：

```python
from pyasar import pack

pack("application-directory", "app.asar")
```

传入空集合可将所有普通文件保留在 ASAR 内：

```python
pack("application-directory", "app.asar", unpack_extensions=set())
```

也可以指定更多需要放入旁挂目录的扩展名：

```python
pack(
    "application-directory",
    "app.asar",
    unpack_extensions={".node", ".dll"},
)
```

源文件扩展名匹配不区分大小写；集合中的扩展名应使用小写。

打包前会删除已有的 `app.asar.unpacked` 内容，避免已经删除的原生文件残留在旧旁挂目录中。
目标 ASAR 会被直接覆盖，打包过程并非事务操作。

### 打包时过滤文件

过滤函数接收一个相对于源目录的 `pathlib.Path`：

```python
from pathlib import Path

from pyasar import pack


def include(path: Path) -> bool:
    return ".git" not in path.parts and path.suffix.lower() != ".map"


pack("app", "app.asar", filter=include)
```

文件和目录都会调用过滤函数。仅对某个目录返回 `False` 并不会停止遍历其子项；如果需要排除整个子树，
应像上例一样检查 `path.parts`。

### 异常处理

```python
from pyasar import AsarError, AsarFormatError, open_archive

try:
    archive = open_archive("app.asar")
    data = archive.read("main.js", verify=True)
except AsarFormatError as error:
    print(f"不安全或格式错误的 ASAR：{error}")
except AsarError as error:
    print(f"完整性校验失败：{error}")
except (FileNotFoundError, PermissionError, EOFError) as error:
    print(f"文件系统或数据截断错误：{error}")
```

文件系统异常会保留为标准 Python 异常：旁挂文件缺失时抛出 `FileNotFoundError`，数据截断时抛出
`EOFError`，解包目标已存在时抛出 `FileExistsError`，向 `info()` 传入目录时抛出
`IsADirectoryError`。

### 兼容性与安全边界

`pyasar` 支持现代 Electron Pickle 容器及其 JSON 文件表，包括普通文件、unpacked 文件、相对链接和
SHA-256 完整性条目；不支持历史 Chromium ASAR 头部变体。

归档条目名和链接目标会在访问或解包前进行验证；解码后的头部 Pickle 上限为 50 MiB，归档内文件区间
必须位于 ASAR 文件范围内。这些检查能够降低意外或恶意文件系统访问风险，但不能让不受信任的可执行
内容变得安全。

完整 API 和格式说明请参阅 [docs/API.md](docs/API.md) 与 [docs/FORMAT.md](docs/FORMAT.md)。

## License / 许可证

MIT. See [LICENSE](LICENSE).
