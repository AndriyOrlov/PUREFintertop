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
Only measured freehand strokes are rendered. Unsupported styles raise an error.

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
