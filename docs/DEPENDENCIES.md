# Dependencies

## Using and integrating the adapter

| Dependency | Required | Purpose |
|---|---|---|
| Python 3.11+ | Yes | `sqlite3.Connection.serialize/deserialize`, public API and CLI |
| Pillow | Yes | Read image headers, encode PNG, render measured pen paths |
| SQLite | Built into Python | Read/edit the recovered modern board database |
| PureRef | No for the API | Optional native visual verification and canonical re-save |
| Qt / PySide / PyQt | No | The small required QVariant subset is decoded directly |
| FFmpeg | No | A future host can use it to provide chosen video-frame previews |
| PDF renderer | No | A future host must provide the selected PDF-page previews |

Reproducible tested runtime: **Python 3.12.14, Pillow 12.3.0, SQLite 3.53.1**.
`requirements.txt` pins the tested Pillow. The package metadata permits Pillow
10.3+, but older dependency versions have not been separately certified here.

The host bridge receives ready preview images for video, PDF, link cards and SVG.
It does not require a network connection or an AI model.

## Development checks

Tests use Python `unittest`; pytest is not required.
Formatting used Black, which is optional for running the project.
Packaging uses setuptools 68+.

```bash
python -m unittest discover -s tests -v
python -m compileall -q src
python -m pip wheel --no-deps --no-build-isolation .
```

## Repeating the binary/native research

These are research tools, not adapter runtime dependencies:

- Official PureRef builds downloaded from https://www.pureref.com/download.php.
- Ghidra 11.0.3 and a Java 17 runtime/JDK for targeted decompilation.
- `readelf`, `nm`, `objdump`, `strings` for ELF inspection.
- Xvfb, xdotool, xkbcomp, X11 runtime libraries and ImageMagick for Linux UI tests.
- PureRef's own bundled Qt libraries. The old builds used Qt 5.12.x and the
  modern builds bundled Qt 6.2.4.

The application binaries, research tool installs and proprietary recovered code
are not dependencies of the delivered adapter and are not bundled with it.
