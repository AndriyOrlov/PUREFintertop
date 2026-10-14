# API reference

## Board

| Operation | Result |
|---|---|
| `Board(use_temp_file=False)` | Empty modern board; `use_temp_file=True` keeps the database on disk |
| `Board.load(path, max_bytes=16 GiB, in_memory_limit=128 MiB)` | Modern or legacy board; larger files use a temporary SQLite file |
| `board.items` | Immutable snapshots in ID order |
| `board.get(item_id)` | One snapshot; a missing ID raises KeyError |
| `board.transform_matrix(item_id)` | All nine local matrix numbers, including perspective |
| `board.add_image(path, x=0, y=0, name=None, parent_id=None)` | New item ID; embeds the original file bytes |
| `board.add_image_bytes(data, name, ...)` | Same, for bytes that are not a file |
| `board.add_note(text, ...)`, `add_group(...)`, `add_drawing(strokes, ...)` | New item IDs |
| `board.update(id, **properties)` | Updated snapshot |
| `board.duplicate(id, **properties)` | New ID; the image data is shared |
| `board.remove(id)` | Removes the item, its descendants and unused images |
| `board.copy()` | Independent copy of the board |
| `board.save(path, overwrite=False, format=None, refresh_preview=True)` | Atomic save; unedited boards are written byte for byte |
| `board.refresh_preview()` | Redraw the thumbnail and scene rectangle now |
| `board.to_dict()` | JSON-friendly summary |
| `board.export_assets(directory)` | Paths of the original embedded image files |
| `board.close()` | Releases the database and temporary files |

Use `with Board.load(...) as board:` to release resources automatically.

Update properties: `x`, `y`, `name`, `text` (notes only), `scale`, `rotation`,
`matrix`, `opacity`, `locked`, `z`, `parent_id`, `comment`.

`rotation` is in degrees and `scale` is an absolute uniform scale. `matrix` is
the four linear components `[m11, m12, m21, m22]`; use it for mirroring, shear
or non-uniform scale (the scale/rotation shortcut refuses those).
`parent_id=None` means a top-level item. Parent cycles are rejected.

`z` only orders an item among its siblings, like in PureRef. Siblings with equal
`z` are drawn by ID. `scene.drawing_order(board)` gives the global order.

## Images

`configure_image(id, crop=(x, y, width, height), flip_horizontal=True,
flip_vertical=None, grayscale=True, bilinear=False, frame=0, speed=1)` checks
every option before changing anything. Crop coordinates are pixels of the
original bitmap and keep referring to the same pixels before and after a flip.

`image_settings(id)` returns the same settings in plain terms plus
`pixel_size`, `embedded`, `cropped` and `playing`. `image_options(id)` returns
the raw native values.

Image formats and sizes are stored the way PureRef stores them on import:
`JPG`/`JPEG`/`TIF`/`TIFF` follow the file extension, and EXIF-rotated photos
keep their original bytes with the displayed width and height.

## Notes and groups

`style_note(id, background_color, text_color, width, height)` takes
`#AARRGGBB` colours and a fixed size, or -1 for automatic. A fixed height is a
minimum: PureRef grows a note to fit its text. Note text is HTML for PureRef; a
bare newline shows as a space (`text.html_from_plain` keeps line breaks).

`group_style(id)` / `style_group(id, color=..., locked_mode=...)` read and set a
group's background and selection lock.

## Sibling order

`ordered_children(parent_id)` sorts children by their stored sort key;
`reorder(ids)` needs every sibling exactly once. PureRef rewrites these keys
when it saves (see docs/NATIVE_WINDOWS.md); drawing order is decided by `z`.

## Drawings

```python
from pur_adapter.drawings import Stroke, SOLID, DASHED, ARROW, rasterize_drawing

board.add_drawing([Stroke((255, 200, 70, 255), 2, ((0, 0, 0), (1, 80, 0)), 0, (0, 0), ARROW)])
report = rasterize_drawing(board, drawing_id, scale=4)
```

Path elements are `(kind, x, y)` with kinds 0 move, 1 line, 2 curve, 3 curve
data. `rasterize_drawing` turns a drawing into an image in place, keeping its
ID, parent, opacity, lock, z and the world positions of its children.

## Whole-board tools

| Module | Functions |
|---|---|
| `render` | `render_scene(board, width, height)`, `render_thumbnail(board)`, `scene_rect(board)`, `item_world_bounds(board)` |
| `scene` | `drawing_order(board)`, `world_properties(board)` |
| `manifest` | `export_manifest(board, folder, extensions=None)`, `import_manifest(path_or_dict, strict=False)` |
| `sidecar` | `write_sidecar(board, board_path, entries, host=...)`, `read_sidecar(board, board_path)` |
| `operations` | `merge_boards`, `split_by_group`, `deduplicate_images`, `arrange_grid`, `search` |
| `assets` | `linked_images`, `relink`, `embed_linked`, `shrink_images` |
| `repair` | `repair(path, folder)`, `carve_images(data, folder)` |
| `compatibility` | `export_legacy(board, path)`, `simplify_board(board)` |
| `conformance` | `describe(board)` for the conformance suite |
| `pureref_app` | `find_pureref()`, `resave`, `render`, `import_images` through PureRef's own command line |

## Compatibility export

```python
from pur_adapter.compatibility import export_legacy, simplify_board

report = export_legacy(board, "simplified.pur")
```

Works on a copy. Parent transforms and opacity are composed before groups are
removed, z is renumbered in drawing order, pen strokes become PNG, GIFs use
their stored frame, rich text becomes plain text and image opacity is baked
into alpha. The report lists every change. Crops, image flags, perspective and
linked resources stop the export before anything is written.

## Errors

`InvalidPurFile`: malformed bytes, checksum or SQLite integrity failure.
`UnsupportedFormat`: unrecognised layout, schema, SQLite triggers/views, or an
unsupported conversion. `ValueError`: invalid property or manifest.
`KeyError`: missing item ID. `FileExistsError`: output exists without
`overwrite`.

The prototype is meant for local use. Do not expose its file-based API as an
unauthenticated network service.
