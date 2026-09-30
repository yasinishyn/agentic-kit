# Licence texts of the components linked into the bundled Python runtime

`fetch-python.sh` copies this folder into every runtime tree as `licences/`
(`Kanban.app/Contents/Resources/python/<arch>/licences/`), next to CPython's own
`lib/python3.12/LICENSE.txt`. `app/python.lock` (`licence_files`) lists each text, so a fetch fails if one is missing.

Scope: the libraries that python-build-standalone release `20260924`
(tag commit `6a729962cddc76630b59b1b895501b1539412524`) links into the `aarch64-apple-darwin` and
`x86_64-apple-darwin` CPython 3.12.14 `install_only_stripped` builds. Versions are those of that tag's
`pythonbuild/downloads.json`; the macOS dependency list is `cpython-unix/targets.yml` (`needs`) and the links are
`cpython-unix/extension-modules.yml`, cross-checked against the fetched interpreter (`ssl.OPENSSL_VERSION`,
`sqlite3.sqlite_version`, `pyexpat.EXPAT_VERSION`, `_decimal.__libmpdec_version__`, embedded version strings and
`otool -L`).

Every text is the upstream file byte for byte, except `xz-5.8.4.txt`, which is upstream `COPYING` followed by a
`==> COPYING.0BSD <==` line and upstream `COPYING.0BSD` (liblzma is 0BSD; `COPYING` gives the copyright line).
The sha256 column is of each text as fetched; the test suite checks the files against it.

| Component | Version | SPDX | File | Source (as fetched) | sha256 of the text as fetched | Fetched |
|---|---|---|---|---|---|---|
| OpenSSL | 3.5.8 | Apache-2.0 | `openssl-3.5.8.txt` | <https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.8/LICENSE.txt> | `7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a` | 2026-09-30 |
| SQLite | 3.53.1 (3530100) | blessing (public domain) | `sqlite-3.53.1.txt` | <https://raw.githubusercontent.com/sqlite/sqlite/version-3.53.1/LICENSE.md> | `ee6af51062b30d532991face5164136ae6f84e265ecf8abe89dc69dac45ca1e7` | 2026-09-30 |
| libffi | 3.4.8 | MIT | `libffi-3.4.8.txt` | <https://raw.githubusercontent.com/libffi/libffi/v3.4.8/LICENSE> | `67894089811f93fca47a76f85e017da6f8582d4ba0905963c6e0f1ad6df7a195` | 2026-09-30 |
| bzip2 (libbzip2) | 1.0.8 | bzip2-1.0.6 | `bzip2-1.0.8.txt` | <https://raw.githubusercontent.com/python/cpython-source-deps/05301997b2f9590f49c672cf3dfd3d3dfa7ad521/LICENSE> (CPython's import of the bzip2 1.0.8 release, branch `bzip2-1.0.8`) | `c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3` | 2026-09-30 |
| XZ Utils (liblzma) | 5.8.4 | 0BSD | `xz-5.8.4.txt` | <https://raw.githubusercontent.com/tukaani-project/xz/v5.8.4/COPYING> + <https://raw.githubusercontent.com/tukaani-project/xz/v5.8.4/COPYING.0BSD> | `616a3ad264ce29b8f1cb97e53037b139d406899ca8d1f799651e17bfa09830b8` + `0b01625d853911cd0e2e088dcfb743261034a091bb379246cb25a14cc4c74bf1` | 2026-09-30 |
| mpdecimal (libmpdec) | 4.0.0 | BSD-2-Clause | `mpdecimal-4.0.0.txt` | <https://raw.githubusercontent.com/python/cpython-source-deps/48316ec025c1ebe500854c332be0a12c640c7301/COPYRIGHT.txt> (CPython's import of the mpdecimal 4.0.0 release, branch `mpdecimal-4.0.0`) | `2713324211652ce4a60e6e21e54f9dc3004299e591b0933a352f1a89c5fb53c2` | 2026-09-30 |
| Expat | 2.8.5 | MIT | `expat-2.8.5.txt` | <https://raw.githubusercontent.com/libexpat/libexpat/R_2_8_5/expat/COPYING> | `31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87` | 2026-09-30 |
| libuuid | 1.0.3 | BSD-3-Clause | `libuuid-1.0.3.txt` | <https://raw.githubusercontent.com/astral-sh/python-build-standalone/20260924/LICENSE.libuuid.txt> (python-build-standalone's copy; upstream is a SourceForge tarball, not fetched) | `122ee1f7e258f2c3c0e538a75c037684f420454bf3850ddc74ce750bbf5fe86b` | 2026-09-30 |

## Not shipped (no text here)

| Component | Why |
|---|---|
| zlib | macOS builds link the system `/usr/lib/libz.1.dylib` (not in the darwin `needs`) |
| ncurses, panel | the system `/usr/lib/libncurses.5.4.dylib` / `libpanel.5.4.dylib` |
| libedit | the system `/usr/lib/libedit.3.dylib` (`readline` module) |
| Tcl, Tk, Tix, itcl, thread | built by python-build-standalone, pruned by `fetch-python.sh` (`tkinter` is removed) |
| zstd | in the darwin `needs`, but only the 3.14+ `_zstd` module links it; CPython 3.12 has none |
| Berkeley DB, libX11, libXau, libxcb, zlib-ng | Linux or Windows builds only |
