# pyasar

[English](#english) | [简体中文](#简体中文)

## English

Python library for reading, validating, extracting, and creating Electron ASAR
archives. Requires Python 3.9+; no runtime dependencies.

### Installation

```console
python -m pip install "pyasar @ git+https://github.com/KouzakiUmi/pyasar.git"
```

From a local checkout:

```console
python -m pip install .
```

### Usage

```python
from pyasar import extract, open_archive, pack

pack("app", "app.asar")
archive = open_archive("app.asar")

for name in archive.names():
    print(name, archive.info(name))

content = archive.read("main.js", verify=True)
extract("app.asar", "output", verify=True)
```

| API | Behavior |
| --- | --- |
| `open_archive(path)` | Parse and structurally validate the header without loading file payloads. |
| `archive.names()` | List files and symbolic links as POSIX paths; exclude directories. |
| `archive.info(name, follow_links=False)` | Return raw entry metadata; treat it as read-only. |
| `archive.read(name, verify=False, follow_links=False)` | Read one complete file as bytes, including unpacked files; optionally follow symbolic links. |
| `archive.extract(destination, verify=False)` | Extract files, empty directories, and symbolic links. |
| `pack(source, destination, *, unpack_extensions=..., filter=None, unpack=None)` | Create an archive from a directory. |

### Packing options

By default, `.node` files are stored in the sibling `app.asar.unpacked`
directory. Pass an empty set to store every regular file in the archive:

```python
pack("app", "app.asar", unpack_extensions=set())
```

Use lowercase extensions to select other unpacked file types. The filter
receives a source-relative `pathlib.Path` for each discovered file or directory:

```python
from pathlib import Path


def include(path: Path) -> bool:
    return ".git" not in path.parts and path.suffix.lower() != ".map"


pack("app", "app.asar", unpack_extensions={".node", ".dll"}, filter=include)
```

Returning `False` for a directory does not prune its descendants; exclude each
member of a subtree using `path.parts` when needed.

For precise sidecar selection, `unpack` replaces the extension rule for each
included file or link:

```python
pack("app", "app.asar", unpack=lambda p: p.parts[0] == "native")
archive = open_archive("app.asar")
content = archive.read("alias/main.js", follow_links=True, verify=True)
```

`info()` also accepts `follow_links=True`. Both methods keep their existing
defaults. Selected links are recreated in the sidecar; their targets must also
be included and unpacked. Links to unpacked files are mirrored automatically.
Creating sidecar links on Windows requires symlink privileges.

### Format and filesystem behavior

- Supports Pickle-framed JSON headers, packed and unpacked files, empty
  directories, and archive-root-relative symbolic links. Legacy Chromium
  header variants are unsupported. On Windows, packing treats junctions as
  symbolic links, and other reparse points such as cloud placeholders as
  regular files; linked directories are recorded as links and never expanded,
  so source link cycles cannot hang packing. Junction targets pointing at
  UNC network shares are refused as outside the source.
- Writes whole-file and 4 MiB block SHA-256 metadata. `verify=True` checks
  `integrity.hash` when present; it does not independently verify block hashes
  or authenticate the archive. Files without integrity metadata remain readable.
- Limits header Pickles to 50 MiB and checks packed payload ranges. Malformed
  structures — oversized decimal offsets, oversized JSON integers, boolean
  sizes, deeply nested file tables, NUL bytes and Windows drive-letter
  segments in entry names and paths — raise `AsarFormatError` instead of
  leaking parser exceptions. Unpacked paths must resolve inside the sidecar;
  the sidecar root cannot be a symbolic link or junction.
- Extraction refuses to overwrite existing files or links. Both packing and
  extraction are non-transactional; a failure may leave partial output.
- Packing overwrites the archive and clears its old sidecar. It rejects a source
  equal to or inside the resolved sidecar before cleanup. Outputs inside the
  source tree are excluded from traversal. A directory named like the sidecar
  next to the destination is removed as stale even if it holds user data, and
  archives whose header exceeds 4 GiB raise `ValueError`. A destination volume
  that cannot hold files larger than 4 GiB (FAT-family filesystems) rejects
  oversized single files and archives with `ValueError`. This volume check is
  best effort: filesystems hidden behind a bridge (such as a Windows drive
  reached through WSL's `/mnt`) cannot be recognized.
- On POSIX, packing records the owner's execute bit as `executable: true`;
  extraction restores such files with mode `0755`.
- Special source files that are neither regular files, directories, nor links
  (FIFOs, sockets, devices) are skipped during packing with a `UserWarning`.
- Source entry names the archive format cannot represent safely (such as the
  Windows drive-relative name `C:`) make `pack` fail with `ValueError`, so it
  never emits an archive that `open_archive` would refuse.
- Windows extraction preserves symbolic links and may require Developer Mode
  or elevated privileges. Target filesystem naming rules still apply.
- `read()` loads the entire requested file into memory; extraction uses this
  method for each file. There is no public streaming API.

Malformed or unsafe archives raise `AsarFormatError`, a subclass of `AsarError`.
Integrity mismatches raise `AsarError`. Filesystem errors retain their standard
Python exception types; truncated payloads raise `EOFError`.

### Development and compatibility tests

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

Bidirectional interoperability has been tested with `@electron/asar 4.3.0`,
including unpacked files, Unicode names, empty directories, and hash block
boundaries. To enable the optional integration test, install that package
outside this checkout, put Node.js on `PATH`, and set `PYASAR_OFFICIAL_MODULE`
to the absolute path of its `lib/asar.js`. Otherwise that test is skipped.

See the [API reference](docs/API.md) and [format specification](docs/FORMAT.md)
for details.

## 简体中文

用于读取、验证、解包和创建 Electron ASAR 归档的 Python 库。
要求 Python 3.9 及以上版本，无运行时第三方依赖。

### 安装

```console
python -m pip install "pyasar @ git+https://github.com/KouzakiUmi/pyasar.git"
```

在本地仓库中安装：

```console
python -m pip install .
```

### 使用

```python
from pyasar import extract, open_archive, pack

pack("app", "app.asar")
archive = open_archive("app.asar")

for name in archive.names():
    print(name, archive.info(name))

content = archive.read("main.js", verify=True)
extract("app.asar", "output", verify=True)
```

| API | 行为 |
| --- | --- |
| `open_archive(path)` | 解析并验证头部结构，不加载文件内容。 |
| `archive.names()` | 以 POSIX 路径列出文件和符号链接，不包含目录。 |
| `archive.info(name, follow_links=False)` | 返回条目的原始元数据，调用方应按只读使用。 |
| `archive.read(name, verify=False, follow_links=False)` | 返回单个文件的完整字节，支持旁挂文件；可选择跟随符号链接。 |
| `archive.extract(destination, verify=False)` | 解包文件、空目录和符号链接。 |
| `pack(source, destination, *, unpack_extensions=..., filter=None, unpack=None)` | 将目录打包为归档。 |

### 打包选项

默认将 `.node` 文件放入同级 `app.asar.unpacked` 目录。
传入空集合可将所有普通文件写入归档：

```python
pack("app", "app.asar", unpack_extensions=set())
```

使用小写扩展名指定其他旁挂类型。过滤函数接收每个已发现文件或目录相对于源目录的 `pathlib.Path`：

```python
from pathlib import Path


def include(path: Path) -> bool:
    return ".git" not in path.parts and path.suffix.lower() != ".map"


pack("app", "app.asar", unpack_extensions={".node", ".dll"}, filter=include)
```

对目录返回 `False` 不会停止遍历其子项；排除整个子树时，应通过 `path.parts` 排除其中每个条目。

`unpack` 回调接收每个已包含文件或链接的相对路径，替代扩展名规则，可精确选择旁挂内容：

```python
pack("app", "app.asar", unpack=lambda p: p.parts[0] == "native")
archive = open_archive("app.asar")
content = archive.read("alias/main.js", follow_links=True, verify=True)
```

`info()` 也支持 `follow_links=True`，两者均保留原有默认行为。选中的链接会在旁挂目录中重建，其目标也必须包含在包中并旁挂；指向旁挂文件的链接会自动重建。Windows 创建旁挂链接需要符号链接权限。

### 格式与文件系统行为

- 支持 Pickle 封装的 JSON 头部、归档内文件、旁挂文件、空目录和以归档根目录为基准的符号链接；不支持旧版 Chromium 头部变体。Windows 打包时将 junction 视为符号链接，将云占位等其他重分析点视作普通文件；被链接的目录只记录为链接、不展开，源目录中的链接环不会导致打包挂起；指向 UNC 网络共享的 junction 目标会因位于源目录之外而被拒绝。
- 写入整文件及 4 MiB 分块 SHA-256 元数据。`verify=True` 在存在 `integrity.hash` 时校验整文件，不单独校验分块哈希，也不验证归档来源；没有完整性元数据的文件仍可读取。
- 头部 Pickle 上限为 50 MiB，并检查归档内数据范围。畸形结构——超长十进制偏移、超大 JSON 整数、布尔值 size、过深嵌套的文件表、条目名与路径中的 NUL 字符和 Windows 盘符段——一律抛出 `AsarFormatError`，不会泄漏解析器内部异常。旁挂文件的实际路径必须位于旁挂目录内；旁挂根目录不能是符号链接或 junction。
- 解包拒绝覆盖已有文件或链接。打包和解包均非事务操作，失败后可能留下部分输出。
- 打包直接覆盖归档并清理旧旁挂目录；清理前拒绝源目录等于或位于实际旁挂目录内部的情况。输出位于源目录中时，会从遍历中排除。与旁挂目录同名的目录会被当作旧旁挂清理（即使其中是用户数据）；头部超过 4 GiB 的归档抛出 `ValueError`。目标卷无法容纳大于 4 GiB 的单文件（FAT 系列文件系统）时，超限的单个文件或整个归档都会抛出 `ValueError`。该卷检测是尽力而为的：被桥接层隐藏的文件系统（例如经 WSL 的 `/mnt` 访问的 Windows 盘）无法识别。
- POSIX 平台打包时将所有者执行位记录为 `executable: true`，解包时将此类文件权限设为 `0755`。
- 源目录中既非普通文件、目录也非链接的特殊文件（FIFO、socket、设备文件）在打包时跳过并发出 `UserWarning`。
- 归档格式无法安全表示的源条目名（例如 Windows 驱动器相对名 `C:`）会让 `pack` 抛出 `ValueError`，保证不会写出连 `open_archive` 都会拒绝的归档。
- Windows 解包保留符号链接，创建链接可能需要开发人员模式或提升权限；仍受目标文件系统命名规则限制。
- `read()` 将所请求文件完整载入内存；解包逐文件调用该方法。目前没有公共流式 API。

格式错误或不安全的归档抛出 `AsarFormatError`，它继承自 `AsarError`。
完整性校验失败抛出 `AsarError`。文件系统错误保留标准 Python 异常类型；数据截断抛出 `EOFError`。

### 开发与兼容性测试

```console
python -m pip install -e ".[dev]"
python -m pytest -q
```

已与 `@electron/asar 4.3.0` 进行双向互操作测试，覆盖旁挂文件、中文文件名、空目录和哈希分块边界。
启用可选集成测试时，在本仓库之外安装该包，将 Node.js 加入 `PATH`，并将
`PYASAR_OFFICIAL_MODULE` 设为其 `lib/asar.js` 的绝对路径；未设置时跳过该测试。

详细说明见 [API 文档](docs/API.md)和[格式说明](docs/FORMAT.md)。
