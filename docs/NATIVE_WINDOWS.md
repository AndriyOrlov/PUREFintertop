# Native checks with PureRef 2.1.3 on Windows

Run on 2026-10-14 with the installed official PureRef 2.1.3 (Windows 11),
driven only through its documented command line (`load`, `save`,
`exportScene`, `clearScene`, `exit`). Boards were created by the adapter or by
PureRef itself in fresh temporary folders. No PureRef files are redistributed.

`save` only overwrites an existing file. Copy the board to the target path
first, then `load` and `save` that path.

## What PureRef does

| Question | Native answer | Adapter consequence |
|---|---|---|
| Unknown SQLite table or column | Logged as "will be dropped on save" and removed | Host-only data goes into a sidecar file, never into the `.pur` |
| Thumbnail | 256×256 JPEG; the same bytes in the header and in `metadata.thumbnail` | The adapter can write its own thumbnail of the same shape |
| `metadata.scene_rect` | QRectF of the union of item bounds | Written by the adapter on save |
| Image format string | Detected from content, spelled like the file extension when it agrees: `.jpg`→`JPG`, `.jpeg`→`JPEG`, `.tif`→`TIF`, `.tiff`→`TIFF`; a PNG named `.jpg`→`PNG`; a phone MPO named `.jpg`→`JPG` | `images.describe_image_file` copies this |
| EXIF-rotated JPEG | Original bytes kept; width/height stored *after* rotation (60×20 photo with orientation 6 → 20×60) | The adapter stores displayed size; native renders of both are pixel-identical |
| Identical image files | Stored once and shared | Same as the adapter |
| `z` inside a group | Only orders siblings: an item outside a group (z=3) is drawn over a child (z=10) of a group with z=1 | Compatibility flattening renumbers z in drawing order |
| Siblings with equal `z` | Higher ID is drawn on top, whatever `sort_order` says | `scene.drawing_order` breaks ties by ID |
| Crop with horizontal flip | Image bounds live in item space after the flip | `configure_image` keeps a crop on the same bitmap pixels in any call order; both orders render the same quarter natively |
| Group frame | A dark rounded background reaching 10 units past the children | Lost when flattening (reported as `group_flattened`); stacking of the children matches the original; `render.py` uses the same margin |
| Automatic note size | About 37 + 9.6 units per character wide and 62 tall for one line; a fixed height is only a minimum | Used by `render.py` for bounds and thumbnails |
| Line breaks in note text | A bare newline is shown as a space (text is read as HTML) | Manifest import turns multi-line plain text into HTML |
| Item IDs on resave | Kept (all 2050 items of the 1000-image board) | Sidecar entries are keyed by item ID |
| `z` on resave | Renumbered, drawing order unchanged | Conformance ignores `z` after a native resave |
| `sort_order` on resave | Rewritten as fractions 1/1, 1/2, 1/3 … stored as `[1, 0, numerator, denominator, 1, 0, 1, 1]`; the new order does not simply follow the old one | Keys are read as fractions; what PureRef uses them for is not established |
| Feature boards of the conformance suite | Every case opens and resaves without warnings and with the same scene | `conformance/native-resave-results.json` |
| Large boards | A 1.49 GB board opens, edits and saves through the adapter with a 34 MB peak working set; in memory would cost about five times the file size (`docs/benchmarks.json`) | Boards above 32 MiB use a temporary SQLite file |

The checks use `pur_adapter.pureref_app` (a thin wrapper around PureRef's
command line) and `tools/build_conformance.py --native`.
