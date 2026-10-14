# Conformance suite

Test files for anyone writing their own `.pur` reader, for example a C++
codec inside FrameRef. The suite does not depend on Python: each case is a
board plus the JSON a correct reader should produce.

```
cases/<name>/board.pur            input
cases/<name>/expected.json        what a reader must find in it
cases/<name>/native-resaved.pur   the same board after PureRef 2.1.3 opened and saved it
invalid/*.pur                     damaged or unsupported files
invalid/expected-errors.json      which error each must cause
native-resave-results.json        what PureRef changed when it resaved each case
```

## Cases

| Case | Contents |
|---|---|
| `native-1.10.4`, `native-1.11.1` | Legacy 1.x files written by PureRef itself |
| `native-2.0.3`, `native-2.1.3` | Modern files written by PureRef itself |
| `native-drawing-2.1.3` | Native group, note, image and pen drawing |
| `image-options` | Crop, flips, grayscale, smooth-scaling off, animation frame and speed, opacity, lock |
| `groups-and-notes` | Nested groups with colours and lock modes, rich-text note with style, comment, Unicode names, sibling order |
| `pen-styles` | Solid, dashed, arrow and curved (circle) pen strokes |
| `transforms` | Rotation with scale, shear, mirroring and a perspective term |
| `exif-photo` | A JPEG with EXIF rotation: stored with its displayed size |

## How to compare

`expected.json` follows `pur_adapter.conformance.describe`:

- `items` in ID order with `kind`, `name`, `parent_id` (null at the top level),
  the full nine-number `matrix` (Qt row-vector order), `opacity`, `locked`,
  `z`, `comment` and `order_index` (position among siblings by sort key).
- Images add `image_sha256` of the stored file bytes, `format`, `pixel_size`,
  `crop` in bitmap pixels, flips, `grayscale`, `bilinear`, `frame`, `speed`.
- Notes add the stored `text` and `style`; groups add `style`; drawings add
  `strokes` with path elements named `move`, `line`, `curve`, `curve_data`.
- `drawing_order` lists item IDs from the bottom to the top.

Compare numbers with a tolerance of 1e-9 and everything else exactly. For
`native-resaved.pur`, PureRef renumbers `z` (keeping `drawing_order`) and
rewrites sort keys, so ignore `z` and `order_index` there.

## Rebuilding

```bash
python tools/build_conformance.py --native
```

`--native` needs an installed PureRef (or `PUREREF` pointing to it). Without
it, the native resaves are not refreshed.
