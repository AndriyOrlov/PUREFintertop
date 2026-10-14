# PureRef ↔ FrameRef converter

Run `dist/PureRef-FrameRef-Converter.exe`, choose a `.pur` or `main.frameref`,
then click **Конвертувати**. No Python installation is needed.

PureRef input produces a new `<name>-FrameRef` folder containing `main.frameref`,
`content/` and `conversion.json`. Open `main.frameref` in FrameRef; keep the folder
together when moving it. FrameRef input produces a new `.pur` and an adjacent
`.pur.conversion.json` report. Existing outputs are refused.

Command-line usage with the same executable:

```powershell
PureRef-FrameRef-Converter.exe input.pur new-FrameRef-folder
PureRef-FrameRef-Converter.exe board/main.frameref new-board.pur
```

The reader targets the installed FrameRef's JSON **file_version 2**. It transfers
images, affine placement (including reflections), scale, rotation, opacity,
stacking, locks, grayscale and text. PureRef groups are flattened while retaining
world positions and inherited opacity. PureRef crops/selected GIF frames are baked
into PNGs; pen drawings become images. FrameRef swatches become images and live
connections become static Bezier curves with side anchors. Text layout can differ. Reports list these
simplifications; they are not lossless round trips.

Unsupported FrameRef items (including video, PDF, portals and native drawings),
local image zoom/pan/cropOffset, perspective, linked PureRef images,
nonrectangular and fractional-pixel PureRef crops stop conversion without
publishing a board. Embed linked images before converting. FrameRef-specific
ratings, tags, borders, overlays and PureRef comments are reported as lost.

Validation: the existing 99 tests plus six converter tests cover affine origins,
byte-preserved image payloads, nested world transforms, crop/flip/grayscale,
refusing overwrites, newer versions, asset path traversal and native notes with
unset text colours. All nine repository fixture boards now convert successfully.
The updated EXE converted a real FrameRef board (30 items) to PureRef and a
PureRef version (32 items) back to FrameRef. These counts describe different
input boards, not an exact round-trip comparison. Opening generated output in
native FrameRef/PureRef has not been verified: computer-use verification was
stopped with Escape. Treat this as an initial converter and check copies first.

## Updating and troubleshooting

Close the old converter and run the newly built EXE from `dist/`. If you copied
it elsewhere, replace that copy too. The `.exe` contains the Python code, so
editing source files alone does not update it. A separate instruction sheet is
included as `dist/CONVERTER-UA.txt`.

`'NoneType' object is not subscriptable` on PureRef notes is fixed: an unset
text colour now uses white. Existing outputs are never overwritten; use a new
name when retrying conversion. Keep the adjacent `.conversion.json` report.

## Build

Build on Windows using the repository's Python environment:

```powershell
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pip install pyinstaller
.venv/Scripts/python -m pip install --no-deps .
.venv/Scripts/python -m unittest discover -s tests
.venv/Scripts/python -m PyInstaller --noconfirm PureRef-FrameRef-Converter.spec
```

Based on PureRef Interop by Andrii Orlov (DeadSue). Apache-2.0; retain LICENSE
and NOTICE with distributions.
