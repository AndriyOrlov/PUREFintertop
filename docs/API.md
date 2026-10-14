# API reference

## Board

| Operation | Result |
|---|---|
| `Board()` | Empty modern board |
| `Board.load(path, max_bytes=...)` | Modern or tested legacy board |
| `board.items` | Immutable snapshots in ID order |
| `board.get(item_id)` | One immutable snapshot; missing ID raises KeyError |
| `board.add_image(path, x=0, y=0, name=None, parent_id=None)` | New item ID; embeds the original file bytes |
| `board.add_note(text, x=0, y=0, name='Note', parent_id=None)` | New note ID |
| `board.update(id, **properties)` | Updated item snapshot |
| `board.duplicate(id, **properties)` | New ID, shared image resource where applicable |
| `board.remove(id)` | Removes the item and its descendant subtree |
| `board.save(path, overwrite=False, format=None)` | Saves atomically; default preserves source format |
| `board.to_dict()` | JSON-friendly scene summary |
| `board.export_assets(directory)` | Paths to original embedded image bytes |
| `board.close()` | Releases the in-memory SQLite connection |

Use `with Board.load(...) as board:` to release the connection automatically.

Update properties: `x`, `y`, `name`, `text` (notes only), `scale`, `rotation`,
`matrix`, `opacity`, `locked`, `z`, `parent_id`.

`rotation` is degrees and `scale` is absolute uniform scale. `matrix` is the four
linear affine components `[m11,m12,m21,m22]`. Use it for reflections, shear or
nonuniform scale; the scale/rotation shortcut rejects those transforms.
`parent_id=None` means a root item. Parent cycles are rejected.

No-op save of a loaded board is byte-identical. Edited modern boards preserve the
underlying schema and untouched fields. Modern previews/thumbnails are retained.
`save(format='legacy')` is for the known legacy subset. Use the compatibility
exporter for flattening and a conversion report.

## Drawings

```python
from pur_adapter.drawings import rasterize_drawing

report = rasterize_drawing(board, drawing_id, scale=4)
```

This changes the item in place into an image, retaining its ID, parent, opacity,
lock and z value. `scale=4` gives four PNG pixels per local board unit. The outer
matrix compensates for pixel density. Child world transforms are preserved.
Measured solid, dashed and arrow paths are rendered. Rectangle and ellipse paths
use the same geometry representation. Unknown styles raise an error.

## Compatibility export

```python
from pur_adapter.compatibility import export_legacy, simplify_board

report = export_legacy(board, 'simplified.pur')
flat_board, report = simplify_board(board)
```

Both operate on a copy. The original remains unchanged. Parent transforms and
opacity are composed before groups are removed. Supported strokes become PNG,
GIF uses its stored playback frame, rich text becomes plain text and image opacity
is baked into alpha. The report identifies changes. Unsupported crops, image
flags, shape styles, perspective and linked resources stop export before writing.

## Errors

`InvalidPurFile`: malformed bytes, checksum or SQLite integrity failure.
`UnsupportedFormat`: unrecognized layout, schema or unsupported conversion.
`ValueError`: invalid requested property or invalid manifest.
`KeyError`: missing item ID.
`FileExistsError`: output already exists without explicit overwrite.

The prototype is intended for local use with trusted files. Do not expose its
filesystem-based API as an unauthenticated network service.

## Grouped authoring and reversible layout

```python
from pur_adapter.drawings import Stroke
from pur_adapter.layout import capture_layout, shuffle_images, restore_layout

with Board() as board:
    group = board.add_group("References", x=100, y=200, color="#ff202a35")
    image = board.add_image("reference.png", parent_id=group)
    caption = board.add_note("<p>Reference 1</p>", y=70, parent_id=image)
    board.style_note(caption, width=145, height=36)
    board.update(group, comment="Check annotations")
    board.add_drawing([Stroke((255, 200, 70, 255), 2,
                             ((0, 0, 0), (1, 80, 0)), 0, (0, 0), 2)],
                      parent_id=group)
    snapshot = capture_layout(board)
    shuffle_images(board, seed=20261014)
    restore_layout(board, snapshot)
    board.save("restored.pur")
```

`style_note` takes `background_color`, `text_color` as #AARRGGBB and positive
`width`/`height`, or -1 for automatic size. Fixed-size note positions are centers.
`ordered_children(parent_id)` reads the measured integer BigRational subset;
`reorder(ids)` requires every sibling exactly once. Unknown ordering encodings
are preserved on ordinary edits and rejected by operations needing comparison.

`configure_image(id, crop=(x, y, width, height), flip_horizontal=True,
grayscale=True, bilinear=False, frame=0, speed=1)` validates every supplied
option before applying changes. Crop coordinates start at the original bitmap's
upper-left corner; they are not canvas coordinates. `image_options(id)` exposes
the current measured native settings. Legacy simplification rejects crops and
unknown bitmap transforms/flags requiring a host-rendered PNG.

`restore_layout` restores parent, full transform, z and sort payloads only when
the non-layout scene content hash and item set match. All validation precedes a
single transaction. Snapshots are independent JSON objects; saving them is the
caller's responsibility. `shuffle_images` supports root images and direct group
children; child captions and drawings move through their parent relation.
