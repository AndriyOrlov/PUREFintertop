# PureRef Interop

A local document API and compatibility bridge by **Andrii Orlov / Dead_Sue**.
It reads, creates and edits `.pur` boards without launching PureRef. The initial
research covers official Linux builds **1.10.4, 1.11.1, 2.0.3 and 2.1.3**.

This is a prototype for real-board testing and a future integration proposal to
Polygonflow. It is not an installed FrameRef plugin and does not decode
FrameRef's private project format. The host-facing boundary is JSON plus assets.

## Validation and research

The 0.2 acceptance case creates 1000 numbered images with captions in ten groups,
shuffles all 1000, and restores their exact layout. Both PureRef 2.0.3 and 2.1.3
produce identical before/after pixels within each release. The automated suite
passes 44 tests.

- [1000-image boards, native screenshots and results](docs/THOUSAND_BOARD.md)
- [Common scene core](docs/CORE.md)
- [Format evolution and compatibility forecast](docs/research/VERSION_RESEARCH.md)

![1000-image board in native PureRef](docs/screenshots/thousand-ordered-ui-2.1.3.png)

[Compare the shuffled board](docs/screenshots/thousand-shuffled-ui-2.1.3.png)

## Install

Python 3.11 or newer is required. Python 3.12 is the tested environment.
The only external runtime package is Pillow. SQLite is part of Python.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install --no-deps .
.venv\Scripts\python -m unittest discover -s tests -v
```

On Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m unittest discover -s tests -v
```

See [dependencies](docs/DEPENDENCIES.md) for runtime versus research tools.

## Python API

```python
from pur_adapter import Board

with Board() as board:
    image_id = board.add_image("reference.png", x=100, y=200)
    board.add_note("Check the indirect lighting", x=450, y=100)
    board.save("created.pur")

with Board.load("created.pur") as board:
    print(board.to_dict())
    board.update(image_id, x=180, rotation=15, scale=0.8)
    board.save("edited.pur")
```

`x` and `y` are item-local coordinates, relative to the parent when present.
Images are centered on the item origin. Positive y points down. `get(id)` returns
an immutable snapshot; use `update()` for changes.

For the inverse integration direction, a host supplies a neutral manifest:

```python
from pur_adapter.manifest import import_manifest, export_manifest
from pur_adapter.compatibility import export_legacy

board, import_report = import_manifest("package/board.json")
with board:
    export_legacy(board, "simplified.pur")

with Board.load("existing.pur") as board:
    export_manifest(board, "new-package")
```

See [integration contract](docs/INTEGRATION.md) and [API reference](docs/API.md).

## Command line

```powershell
pur-adapter inspect existing.pur
pur-adapter create examples/manifest.json created.pur
pur-adapter patch created.pur edited.pur --item 0 --set '{"x":200,"rotation":15}'
pur-adapter rasterize drawing.pur rasterized.pur --all --scale 4
pur-adapter export-legacy drawing.pur simplified.pur
pur-adapter export-package existing.pur new-package
pur-adapter extract-assets existing.pur new-assets
```

Commands print JSON. Errors go to stderr and return exit code 1. Existing outputs
are protected unless the relevant command explicitly receives `--overwrite`.
`export-legacy` also writes a `.report.json` with the conversions performed.

## What is supported

- Modern native format `2.1` used by the tested 2.0.3 and 2.1.3 builds.
- Legacy `1.10` layout used by 1.10.4 and 1.11.1.
- Embedded image extraction, creation, naming, movement, rotation and scaling.
- Text notes, item opacity, z order, locks and parent relationships in modern files.
- Modern groups, drawings and unedited columns remain in the SQLite database.
- Unedited copies are byte-identical to their source.
- Freehand pen strokes can become PNG images while retaining placement, parent,
  item ID and z order. Child transforms are compensated.
- Compatibility export flattens groups, rasterizes supported pen strokes,
  selects the current GIF frame and bakes image opacity, with a change report.

## Current limits

- Native verification ran on Linux. Windows and macOS installation/native UI
  checks are part of the next manual pass.
- Freehand rasterization approximates Qt antialiasing. Shape styles, arrows and
  unknown stroke layouts are rejected rather than discarded.
- Compatibility export stops on modern image crops, nondefault image flags,
  perspective and unembedded linked assets. Pre-rendered previews are required
  for these cases. Native modern read/save preserves their stored data.
- Modern thumbnails are retained, not regenerated. A fresh board starts with
  an empty thumbnail; opening and re-saving in PureRef refreshes it.
- The legacy codec covers its known subset, not every historical property.
  Modern-to-legacy conversion changes rich text/layout and group presentation.
- The manifest retains native crop properties for a host mapper. Group visual
  styling and comments need explicit host mapping in a production FrameRef plugin.
- The default input budget is 256 MiB. Boards are loaded into memory; there is
  no large-board streaming implementation yet.

[Native compatibility evidence](docs/VALIDATION.md),
[format notes](docs/FORMAT.md),
[binary research](docs/research/README.md),
[real-board test plan](docs/REAL_BOARD_TEST_PLAN.md),
[Claude review brief](docs/CLAUDE_REVIEW.md).

## Next steps

1. Andrii tests copies of real boards and records differences.
2. Claude reviews the code and tests; required findings are fixed.
3. Agree with Polygonflow on the host API and feature mapping.
4. Prepare the proposal letter. Send only after Andrii's final check.

No proposal email has been sent.
