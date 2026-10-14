# FrameRef integration

The goal: FrameRef opens and saves PureRef boards, the industry standard, while
keeping its own richer features. This adapter offers three ways to get there.

| Option | Fits when |
|---|---|
| Call `pur-adapter.exe` (no Python needed) with JSON in and out | The quickest working integration |
| Embed the Python package | The host already ships Python |
| Write a native codec (e.g. C++) and check it with `conformance/` | Long-term, no extra runtime |

There is no assumption about FrameRef's implementation language or private
save format.

## PureRef board → FrameRef

```bash
pur-adapter export-package board.pur package
```

`package/board.json` (schema 2, described in
[manifest.schema.json](manifest.schema.json)), `package/assets/` with the
original image files and pen-drawing PNGs, and `package/preview.png`.

Each item has `id`, `kind`, `name`, `parent_id`, the full local `matrix`, `x`,
`y`, `opacity`, `locked`, `z`, `comment`, `order_index`, and two helpers for
hosts: `world_matrix` (including all parents) and `stacking_index` (global
drawing order, 0 at the bottom). Coordinates: y down, images centred on their
origin, Qt row-vector matrices (`[m11, m12, m13, m21, m22, m23, m31, m32, m33]`,
CSS `matrix(m11, m12, m21, m22, m31, m32)` for affine ones).

| PureRef | In board.json | Suggested FrameRef object |
|---|---|---|
| Image | `path`, `image.crop` (bitmap pixels), flips, `grayscale`, `bilinear`, `frame`, `speed`, `image.native` | Image with non-destructive crop/flip |
| Note | `text` (Qt HTML), `plain_text`, `style` | Note; map the HTML to FrameRef's rich text |
| Group | `style.background_color`, `style.locked_mode` | Group |
| Pen drawing | `strokes` (vector) and `preview_path` (PNG) | Arrows/lines, or the PNG |
| Comment | `comment` | Note or tooltip |

## FrameRef → PureRef board

The host writes the same JSON (schema 1 or 2) and runs:

```bash
pur-adapter create board.json board.pur
```

Media PureRef cannot show is mapped and reported in `report.changes`:

| FrameRef item | PureRef result |
|---|---|
| `video` with `preview_path` and `url` | Image of the chosen frame; the URL becomes the item comment |
| `pdf` with `pages` | A group with one image per page, laid out left to right |
| `link`, `svg` with `preview_path` | Image of the preview |
| `link`, `audio` without a preview | A note with title and URL |
| `arrow`, `line` with `points` | A native PureRef pen drawing (arrow, solid or dashed) |
| `note` with `text_format: "plain"` | HTML that keeps the line breaks |

Unknown fields and kinds are listed in `report.unresolved` (or refused with
`--strict`). IDs may be strings; `report.id_map` gives the PureRef IDs.

## Keeping FrameRef-only data

PureRef deletes unknown tables and columns when it saves a board. So whatever
FrameRef needs to get back (video trims, audio, PDF page numbers, …) goes into
the item's `extensions`. `create` writes these next to the board:

```
board.pur
board.pur.host.json
```

`export-package` puts them back into `extensions`. PureRef keeps item IDs on
resave; each entry also stores a fingerprint, so if the user changed or
removed that item in PureRef, the entry is reported as stale instead of being
attached to the wrong item.

## Older PureRef (1.x)

```bash
pur-adapter export-legacy board.pur for-pureref-1.pur
```

Flattens groups (keeping the drawing order), turns pen strokes into images,
selects GIF frames, bakes opacity, converts rich text to plain text and writes
a report of every change. Crops, image flags, perspective and linked images
stop the export; supply pre-rendered images for them.

## Suggested Polygonflow implementation

1. Start with `pur-adapter.exe` for `Import PureRef` / `Export PureRef`.
2. Map JSON items to FrameRef objects (tables above); show `report.changes`
   and `report.unresolved` to the user.
3. If a native codec is preferred later, port the documented formats and
   check it against `conformance/`.
