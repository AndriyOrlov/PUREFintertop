"""Whole-board operations: merge, split, de-duplicate, arrange and search."""

import math
import re
from pathlib import Path
from . import qt
from .board import KIND_TABLES, NO_PARENT
from .render import item_world_bounds
from .text import plain_text

MERGE_GAP = 100  # scene units between the existing content and merged content


# --- Merge -----------------------------------------------------------------------


def _copy_row(source, target, table, row_id, changes):
    row = source._row(table, row_id)
    if row is None:
        return False
    target_columns = {r[1] for r in target._conn.execute(f'PRAGMA table_info("{table}")')}
    values = {key: row[key] for key in row.keys() if key in target_columns}
    values.update(changes)
    columns = ", ".join(f'"{column}"' for column in values)
    placeholders = ", ".join("?" for _ in values)
    target._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                         tuple(values.values()))
    return True


def merge_boards(target, source, *, offset=None):
    """Copy every item of `source` into `target`, keeping hierarchy and settings.

    Identical images are shared. By default the merged items are placed to the
    right of the existing content. Returns {source_id: new_id}.
    """
    if offset is None:
        offset = _offset_beside(target, source)
    dx, dy = offset
    image_map = {}
    item_map = {}
    with target._conn:
        for row in source._conn.execute("SELECT * FROM images ORDER BY id").fetchall():
            existing = target._conn.execute(
                "SELECT id FROM images WHERE checksum IS ? AND data IS ? AND source IS ?",
                (row["checksum"], row["data"], row["source"]),
            ).fetchone()
            if existing and row["data"] is not None:
                image_map[row["id"]] = existing[0]
                continue
            new_image_id = target._next_id("images")
            _copy_row(source, target, "images", row["id"], {"id": new_image_id})
            image_map[row["id"]] = new_image_id
        next_id = target._next_id()
        top_z = target._conn.execute("SELECT COALESCE(MAX(z), 0) FROM items WHERE parent=?",
                                     (NO_PARENT,)).fetchone()[0]
        for item in source.items:
            item_map[item.id] = next_id
            next_id += 1
        for item in source.items:
            changes = {"id": item_map[item.id]}
            if item.parent_id is None:
                matrix = source.transform_matrix(item.id)
                matrix[qt.TRANSLATE_X] += dx
                matrix[qt.TRANSLATE_Y] += dy
                changes.update(parent=NO_PARENT, transform=qt.transform(matrix),
                               z=top_z + item.z)  # merged content goes on top
            else:
                changes["parent"] = item_map[item.parent_id]
            _copy_row(source, target, "items", item.id, changes)
            for table in KIND_TABLES:
                extra = {"id": item_map[item.id]}
                if table == "items_images" and item.image_id is not None:
                    extra["image"] = image_map[item.image_id]
                _copy_row(source, target, table, item.id, extra)
    target._dirty = True
    return item_map


def _offset_beside(target, source):
    target_boxes = list(item_world_bounds(target).values())
    source_boxes = list(item_world_bounds(source).values())
    if not target_boxes or not source_boxes:
        return 0, 0
    target_right = max(box[2] for box in target_boxes)
    target_top = min(box[1] for box in target_boxes)
    source_left = min(box[0] for box in source_boxes)
    source_top = min(box[1] for box in source_boxes)
    return target_right + MERGE_GAP - source_left, target_top - source_top


# --- Split -------------------------------------------------------------------------


def _safe_file_name(name, fallback):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or fallback


def split_by_group(board, folder, *, overwrite=False):
    """Save every top-level group as its own board. Returns the written paths."""
    folder = Path(folder)
    groups = [item for item in board.items if item.kind == "group" and item.parent_id is None]
    roots = [item.id for item in board.items if item.parent_id is None]
    written = []
    used_names = set()
    for group in groups:
        with board.copy() as part:
            for root_id in roots:
                if root_id != group.id:
                    part.remove(root_id)
            base = _safe_file_name(group.name, f"group-{group.id}")
            name = base
            counter = 2
            while name.casefold() in used_names:
                name = f"{base} ({counter})"
                counter += 1
            used_names.add(name.casefold())
            written.append(part.save(folder / f"{name}.pur", overwrite=overwrite))
    return written


# --- De-duplicate ------------------------------------------------------------------


def deduplicate_images(board):
    """Store identical embedded images once. Returns how many copies were removed."""
    duplicates = board._conn.execute(
        "SELECT copy.id AS duplicate, MIN(keep.id) AS keep"
        " FROM images AS copy JOIN images AS keep"
        " ON keep.checksum = copy.checksum AND keep.data = copy.data AND keep.id < copy.id"
        " WHERE copy.data IS NOT NULL GROUP BY copy.id"
    ).fetchall()
    with board._conn:
        for row in duplicates:
            board._conn.execute("UPDATE items_images SET image=? WHERE image=?",
                                (row["keep"], row["duplicate"]))
            board._conn.execute("DELETE FROM images WHERE id=?", (row["duplicate"],))
    if duplicates:
        board._dirty = True
    return len(duplicates)


# --- Arrange -----------------------------------------------------------------------

SORT_KEYS = {
    "name": lambda item, box: item.name.casefold(),
    "height": lambda item, box: -(box[3] - box[1]),
    "none": lambda item, box: 0,
}


def arrange_grid(board, *, parent_id=None, gap=10, row_width=None, sort="name",
                 kinds=("image",)):
    """Lay out the children of `parent_id` (root by default) in rows.

    Items keep their size and rotation; only their positions change. Rows are
    filled up to `row_width` scene units (default: a roughly square layout).
    Returns the number of items moved.
    """
    if sort not in SORT_KEYS:
        raise ValueError(f"sort must be one of {', '.join(SORT_KEYS)}")
    bounds = item_world_bounds(board)
    children = [item for item in board.items
                if item.parent_id == parent_id and item.kind in kinds and item.id in bounds]
    if not children:
        return 0
    children.sort(key=lambda item: SORT_KEYS[sort](item, bounds[item.id]))
    sizes = {item.id: (bounds[item.id][2] - bounds[item.id][0],
                       bounds[item.id][3] - bounds[item.id][1]) for item in children}
    if row_width is None:
        total_area = sum((w + gap) * (h + gap) for w, h in sizes.values())
        widest = max(w for w, _ in sizes.values())
        row_width = max(widest, math.sqrt(total_area))
    origin_x = min(bounds[item.id][0] for item in children)
    origin_y = min(bounds[item.id][1] for item in children)
    parent_to_world = None
    if parent_id is not None:
        from .scene import world_properties

        parent_to_world = world_properties(board)[parent_id][0]
    cursor_x, cursor_y, row_height = 0.0, 0.0, 0.0
    for item in children:
        width, height = sizes[item.id]
        if cursor_x > 0 and cursor_x + width > row_width:
            cursor_x, cursor_y, row_height = 0.0, cursor_y + row_height + gap, 0.0
        target_left, target_top = origin_x + cursor_x, origin_y + cursor_y
        shift_x = target_left - bounds[item.id][0]
        shift_y = target_top - bounds[item.id][1]
        if parent_to_world is not None:
            # Convert the world-space shift into the parent's local units.
            inverse = qt.inverse(parent_to_world)
            shift_x, shift_y = (shift_x * inverse[qt.SCALE_X] + shift_y * inverse[qt.SHEAR_X],
                                shift_x * inverse[qt.SHEAR_Y] + shift_y * inverse[qt.SCALE_Y])
        board.update(item.id, x=item.x + shift_x, y=item.y + shift_y)
        cursor_x += width + gap
        row_height = max(row_height, height)
    return len(children)


# --- Search ------------------------------------------------------------------------


def search(board, query):
    """Items whose name, comment, note text or image file name contain `query`."""
    needle = query.casefold()
    found = []
    for item in board.items:
        fields = {"name": item.name, "comment": item.comment or ""}
        if item.kind == "note":
            fields["text"] = plain_text(item.text or "")
        if item.kind == "image" and item.image_id is not None:
            row = board._row("images", item.image_id)
            fields["source"] = (row["source"] or "") if row else ""
        for field, value in fields.items():
            position = value.casefold().find(needle)
            if position >= 0:
                start = max(0, position - 30)
                found.append({"item_id": item.id, "kind": item.kind, "field": field,
                              "snippet": value[start : position + len(query) + 30]})
                break
    return found

