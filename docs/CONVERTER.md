# PureRef ⇄ FrameRef converter

One Windows program, `PureRef-FrameRef-Converter.exe`, converts boards in both
directions between PureRef (`.pur`) and native FrameRef boards
(`main.frameref`, file_version 2). No Python installation is needed.

<p align="center">
  <img src="screenshots/converter.png" width="70%" alt="Converter window after converting a 1000-image board">
</p>

## Using it

1. Run the program, or drop a `.pur` / `main.frameref` onto its icon.
2. **Choose file…** picks a board. The direction is chosen automatically and a
   new output name is suggested next to the board; **Change…** picks another place.
3. **Convert**. The result is green when nothing was simplified and yellow with a
   grouped list of simplifications otherwise; **Show result** opens it in
   Explorer and **Open report** shows `conversion.json`. If the board cannot be
   converted, the reason is shown in red and the original stays untouched.

PureRef input produces a new `<name>-FrameRef` folder with `main.frameref`,
`content/` and `conversion.json`; keep the folder together. FrameRef input
produces a new `<name>-PureRef.pur` with `<name>-PureRef.pur.conversion.json`.
Nothing is overwritten: a taken name becomes `name (2)`, `name (3)` and so on.

The window uses the dark Windows 11 (Fluent) look of the author's calibration
apps. The same program converts from the command line and prints the report:

```powershell
PureRef-FrameRef-Converter.exe input.pur new-FrameRef-folder
PureRef-FrameRef-Converter.exe board/main.frameref new-board.pur
```

## What is transferred

Images (original bytes), affine placement including mirroring, scale, rotation,
opacity, stacking, locks, grayscale and note text. Notes keep their HTML,
including font size; the text colour is added to the document's `<body>`.

| Simplification (reported) | Why |
|---|---|
| PureRef groups are flattened; children keep world positions and inherited opacity | FrameRef has no PureRef-style groups |
| PureRef crops and selected GIF frames are baked into PNGs | Crop/frame semantics differ |
| PureRef pen drawings and FrameRef colour swatches become images | No matching item type |
| FrameRef connections become static Bézier curves with side anchors | PureRef has no live connectors; routing is approximate |
| Note text can wrap differently | The two applications lay out text differently |
| PureRef comments, FrameRef tags, ratings, borders and overlays are dropped | No counterpart |

Conversion stops, explains why and writes nothing for: FrameRef video, PDF,
portals and native drawings; local image zoom/pan or `cropOffset`; perspective
transforms; PureRef images that are only linked (run `pur-adapter embed` first);
non-rectangular and fractional-pixel PureRef crops.

## Checks

- 112 automated tests, including the converter and the window's helpers.
- The rewritten converter produces identical output to the first version on
  all 19 repository boards, in both directions.
- PureRef → FrameRef → PureRef for five boards (up to the 1000-image board):
  native PureRef 2.1.3 opened, rendered and resaved every result without
  warnings and kept every item.
- Opening converted boards in FrameRef itself has not been checked here
  (FrameRef is not installed on the test machine).

## Build

```powershell
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install pyinstaller sv-ttk
.venv/Scripts/python -m pip install --no-deps .
.venv/Scripts/python -m unittest discover -s tests
.venv/Scripts/python -m PyInstaller --noconfirm PureRef-FrameRef-Converter.spec
```

`tools/make_converter_icon.py` redraws `packaging/converter.ico`. The release
workflow builds the program and attaches it with `CONVERTER.txt`, `LICENSE`
and `NOTICE`.

Based on PureRef Interop by Andrii Orlov (DeadSue). Apache-2.0; keep LICENSE
and NOTICE with distributions. The window theme is Sun Valley ttk (MIT).
