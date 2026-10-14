"""The public `Board` API: open, read, edit and save PureRef boards.

A modern board is an SQLite database. Columns and tables the adapter does not
understand are kept as they are on ordinary edits, so a board can be opened and
saved without losing data written by a newer PureRef.
"""

from importlib.resources import files
from pathlib import Path
import copy
import hashlib
import io
import math
import os
import re
import shutil
import sqlite3
import tempfile
from PIL import Image
from .container import (
    InvalidPurFile,
    UnsupportedFormat,
    open_container_file,
    pack_container,
    unpack_container,
    write_container_file,
)
from . import qt
from .core import Item, finite
from .images import describe_image_file

DEFAULT_MAX_BYTES = 16 * 1024**3  # refuse files above 16 GiB unless asked
IN_MEMORY_LIMIT = 128 * 1024**2  # larger boards are unpacked to a temp file

LEGACY_MAGIC = b"\0\0\0\x08"  # QString length of "1.10" in UTF-16
MODERN_MAGIC = b"\0\0\0\x06"  # QString length of "2.1"

# Tables and the columns this adapter relies on. Extra columns are allowed.
NATIVE_SCHEMA = {
    "items": {"id", "name", "parent", "transform", "opacity", "locked", "z",
              "sort_order", "comment"},
    "images": {"id", "width", "height", "format", "source", "origin", "checksum",
               "data", "source_type"},
    "items_images": {"id", "image", "image_bounds", "image_transform", "flags",
                     "playback_frame", "playback_speed", "playback_state"},
    "items_notes": {"id", "text", "fixed_size", "background_color", "text_color",
                    "style"},
    "items_groups": {"id", "lock_mode", "background_color"},
    "items_drawings": {"id", "strokes"},
    "metadata": {"id", "thumbnail"},
}
KIND_TABLES = ("items_images", "items_notes", "items_groups", "items_drawings")
NO_PARENT = -1  # what PureRef stores in items.parent for a root item

# Bits of items_images.flags.
FLAG_BILINEAR = 1  # smooth scaling, on by default
FLAG_GRAYSCALE = 2

_ITEM_QUERY = """
    SELECT items.*,
           items_images.image AS image_ref,
           items_notes.text AS note_text,
           CASE
               WHEN items_images.id IS NOT NULL THEN 'image'
               WHEN items_notes.id IS NOT NULL THEN 'note'
               WHEN items_groups.id IS NOT NULL THEN 'group'
               WHEN items_drawings.id IS NOT NULL THEN 'drawing'
               ELSE 'unknown'
           END AS kind
    FROM items
    LEFT JOIN items_images ON items_images.id = items.id
    LEFT JOIN items_notes ON items_notes.id = items.id
    LEFT JOIN items_groups ON items_groups.id = items.id
    LEFT JOIN items_drawings ON items_drawings.id = items.id
"""

_UPDATABLE = {"x", "y", "scale", "rotation", "matrix", "name", "text", "opacity",
              "locked", "z", "parent_id", "comment"}
_NUMERIC = {"x", "y", "scale", "rotation", "opacity", "z"}


# --- Writing files safely ----------------------------------------------------------


def atomic_write_stream(path, write, overwrite=False):
    """Write a file through `write(stream)` so readers never see half a file.

    The data goes to a temporary file next to the target first. Without
    `overwrite`, an existing target is never replaced, even in a race.
    """
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(handle, "wb") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temp, path)
        else:
            os.link(temp, path)  # fails if the target appeared meanwhile
            os.unlink(temp)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def atomic_write(path, data, overwrite=False):
    atomic_write_stream(path, lambda stream: stream.write(data), overwrite)


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


class Board:
    """One PureRef board. Use it as a context manager to release resources."""

    def __init__(self, *, use_temp_file=False):
        """Start an empty modern board.

        With `use_temp_file=True` the database lives in a temporary file rather
        than in memory; use it when building boards with gigabytes of images.
        """
        empty = files("pur_adapter").joinpath("data/empty.pur").read_bytes()
        connection, version, thumbnail = unpack_container(empty)
        if not use_temp_file:
            self._attach(connection, version, thumbnail)
            return
        workdir = tempfile.TemporaryDirectory(prefix="pur-adapter-")
        database_path = os.path.join(workdir.name, "board.sqlite")
        on_disk = sqlite3.connect(database_path)
        connection.backup(on_disk)
        connection.close()
        self._attach(on_disk, version, thumbnail, workdir=workdir,
                     database_path=database_path)

    # --- Opening and closing -----------------------------------------------------

    def _attach(self, connection, application_version, thumbnail, *,
                original_bytes=None, original_path=None, workdir=None,
                database_path=None):
        """Take ownership of an open SQLite connection and check its tables."""
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA trusted_schema=OFF")
        self.application_version = application_version
        self._thumbnail = thumbnail
        self.format = "modern"
        self._legacy = None  # legacy records, only for 1.x boards
        self._raw = original_bytes  # returned unchanged by a no-op save
        self._original_path = original_path  # same, for file-backed boards
        self._workdir = workdir  # TemporaryDirectory of a file-backed board
        self._database_path = database_path
        self._dirty = False
        try:
            self._check_schema()
        except UnsupportedFormat:
            self.close()
            raise

    def _check_schema(self):
        tables = {
            row[0]
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not set(NATIVE_SCHEMA) <= tables:
            raise UnsupportedFormat("Missing native scene tables")
        for table, needed in NATIVE_SCHEMA.items():
            columns = {row[1] for row in self._conn.execute(f'PRAGMA table_info("{table}")')}
            if not needed <= columns:
                raise UnsupportedFormat(f"Unsupported native schema in {table}")

    @classmethod
    def load(cls, path, *, max_bytes=DEFAULT_MAX_BYTES, in_memory_limit=IN_MEMORY_LIMIT):
        """Open a .pur file. Boards above `in_memory_limit` use a temporary file."""
        path = Path(path)
        size = path.stat().st_size
        if size > max_bytes:
            raise InvalidPurFile("File exceeds configured input limit")
        with open(path, "rb") as stream:
            magic = stream.read(4)
        if magic == LEGACY_MAGIC:
            from .legacy import load_legacy

            return load_legacy(path, path.read_bytes())
        if magic != MODERN_MAGIC:
            raise UnsupportedFormat("Unrecognized PureRef format")
        board = cls.__new__(cls)
        if size <= in_memory_limit:
            data = path.read_bytes()
            connection, version, thumbnail = unpack_container(data)
            board._attach(connection, version, thumbnail, original_bytes=data)
            return board
        workdir = tempfile.TemporaryDirectory(prefix="pur-adapter-")
        try:
            connection, version, thumbnail, database_path = open_container_file(
                path, workdir.name
            )
        except Exception:
            workdir.cleanup()
            raise
        board._attach(connection, version, thumbnail, original_path=path,
                      workdir=workdir, database_path=database_path)
        return board

    def copy(self):
        """Return an independent copy of this board (original left untouched)."""
        clone = Board.__new__(Board)
        if self._database_path is None:
            connection = sqlite3.connect(":memory:")
            self._conn.commit()
            self._conn.backup(connection)
            clone._attach(connection, self.application_version, self._thumbnail)
        else:
            workdir = tempfile.TemporaryDirectory(prefix="pur-adapter-")
            database_path = os.path.join(workdir.name, "board.sqlite")
            connection = sqlite3.connect(database_path)
            self._conn.commit()
            self._conn.backup(connection)
            clone._attach(connection, self.application_version, self._thumbnail,
                          workdir=workdir, database_path=database_path)
        clone.format = self.format
        clone._legacy = copy.deepcopy(self._legacy)
        return clone

    def close(self):
        self._conn.close()
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Reading -------------------------------------------------------------------

    @property
    def items(self):
        """Snapshots of every item, in ID order."""
        rows = self._conn.execute(_ITEM_QUERY + " ORDER BY items.id")
        return [self._item_from_row(row) for row in rows]

    def get(self, item_id):
        """Snapshot of one item. A missing ID raises KeyError."""
        if not _is_integer(item_id):
            raise ValueError("item_id must be an integer")
        row = self._conn.execute(_ITEM_QUERY + " WHERE items.id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._item_from_row(row)

    @staticmethod
    def _item_from_row(row):
        matrix = qt.read_transform(row["transform"])
        return Item(
            id=row["id"],
            kind=row["kind"],
            name=row["name"] or "",
            x=matrix[qt.TRANSLATE_X],
            y=matrix[qt.TRANSLATE_Y],
            matrix=(
                matrix[qt.SCALE_X],
                matrix[qt.SHEAR_Y],
                matrix[qt.SHEAR_X],
                matrix[qt.SCALE_Y],
            ),
            parent_id=None if row["parent"] == NO_PARENT else row["parent"],
            opacity=row["opacity"],
            locked=bool(row["locked"]),
            z=row["z"],
            text=row["note_text"] if row["kind"] == "note" else None,
            image_id=row["image_ref"] if row["kind"] == "image" else None,
            comment=row["comment"],
        )

    def transform_matrix(self, item_id):
        """All nine local transform numbers, including perspective terms.

        `Item.matrix` only shows the 2x2 part; use this for lossless reads.
        """
        row = self._conn.execute("SELECT transform FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return qt.read_transform(row[0])

    def _row(self, table, row_id):
        return self._conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()

    def _require_kind(self, item_id, kind):
        item = self.get(item_id)
        if item.kind != kind:
            raise ValueError(f"Expected a{'n' if kind[0] in 'aeiou' else ''} {kind} item")
        return item

    # --- Adding items --------------------------------------------------------------

    def _next_id(self, table="items"):
        return self._conn.execute(f"SELECT COALESCE(MAX(id), -1) + 1 FROM {table}").fetchone()[0]

    def _add_item(self, name, x, y, parent_id=None):
        """Insert the shared `items` row; the caller adds the kind-specific row."""
        finite(x=x, y=y)
        if not isinstance(name, str):
            raise ValueError("name must be text")
        if parent_id is not None:
            self.get(parent_id)
        item_id = self._next_id()
        self._conn.execute(
            "INSERT INTO items (id, name, parent, transform, opacity, locked, z,"
            " sort_order, comment) VALUES (?, ?, ?, ?, 1, 0, ?, ?, NULL)",
            (
                item_id,
                name,
                NO_PARENT if parent_id is None else parent_id,
                qt.transform(qt.affine(x, y)),
                float(item_id + 1),  # new items go on top, like in PureRef
                qt.sort_order(item_id),
            ),
        )
        self._dirty = True
        return item_id

    def add_image(self, path, *, x=0, y=0, name=None, parent_id=None):
        """Embed an image file; the item is centred on (x, y)."""
        path = Path(path)
        data = path.read_bytes()
        return self.add_image_bytes(data, path.stem if name is None else name,
                                    x=x, y=y, parent_id=parent_id,
                                    source=str(path.resolve()), file_suffix=path.suffix)

    def add_image_bytes(self, data, name, *, x=0, y=0, parent_id=None, source="",
                        file_suffix=""):
        """Embed image bytes that did not come from a file on disk."""
        # Displayed size and format spelling match what PureRef itself stores.
        width, height, image_format = describe_image_file(data, file_suffix)
        checksum = hashlib.md5(data).hexdigest()
        existing = self._conn.execute(
            "SELECT id FROM images WHERE checksum=? AND data=?", (checksum, data)
        ).fetchone()
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            if existing:
                image_id = existing[0]  # identical file: share it, as PureRef does
            else:
                image_id = self._next_id("images")
                self._conn.execute(
                    "INSERT INTO images (id, width, height, format, source, origin,"
                    " checksum, data, source_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (image_id, width, height, image_format, source, source, checksum, data),
                )
            self._conn.execute(
                "INSERT INTO items_images (id, image, image_bounds, image_transform,"
                " flags, playback_frame, playback_speed, playback_state)"
                " VALUES (?, ?, ?, ?, ?, 0, 1, 0)",
                (
                    item_id,
                    image_id,
                    qt.image_bounds(width, height),
                    qt.transform(qt.affine(-width / 2, -height / 2)),
                    FLAG_BILINEAR,
                ),
            )
        return item_id

    def add_note(self, text, *, x=0, y=0, name="Note", parent_id=None):
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            self._conn.execute(
                "INSERT INTO items_notes (id, text, fixed_size, background_color,"
                " text_color, style) VALUES (?, ?, ?, '', NULL, 0)",
                (item_id, text, qt.size(-1, -1)),  # -1, -1 = automatic size
            )
        return item_id

    @staticmethod
    def _color(value):
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{8}", value):
            raise ValueError("color must be #AARRGGBB")
        return value.lower()

    def add_group(self, name="Group", *, x=0, y=0, parent_id=None,
                  color="#ff202020", locked_mode=True):
        color = self._color(color)
        if not isinstance(locked_mode, bool):
            raise ValueError("locked_mode must be boolean")
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            self._conn.execute(
                "INSERT INTO items_groups (id, lock_mode, background_color) VALUES (?, ?, ?)",
                (item_id, locked_mode, color),
            )
        return item_id

    def add_drawing(self, strokes, *, x=0, y=0, name="Drawing", parent_id=None):
        from .drawings import encode_strokes

        payload = encode_strokes(strokes)
        with self._conn:
            item_id = self._add_item(name, x, y, parent_id)
            self._conn.execute(
                "INSERT INTO items_drawings (id, strokes) VALUES (?, ?)",
                (item_id, payload.decode("latin1")),
            )
        return item_id

    def drawing_strokes(self, item_id):
        from .drawings import parse_strokes

        self._require_kind(item_id, "drawing")
        return parse_strokes(self._row("items_drawings", item_id)["strokes"])

    # --- Editing common properties ---------------------------------------------------

    def update(self, item_id, **properties):
        """Change position, size, rotation, name, text, opacity, lock, z, parent
        or comment of one item. Everything is checked before anything changes."""
        item = self.get(item_id)
        self._check_update(item, properties)
        matrix = self._updated_matrix(self.transform_matrix(item_id), properties)
        column_updates = {
            "parent" if key == "parent_id" else key: value
            for key, value in properties.items()
            if key in ("name", "opacity", "locked", "z", "parent_id", "comment")
        }
        if column_updates.get("parent", 0) is None:
            column_updates["parent"] = NO_PARENT
        column_updates["transform"] = qt.transform(matrix)
        assignments = ", ".join(f"{column}=?" for column in column_updates)
        with self._conn:
            self._conn.execute(
                f"UPDATE items SET {assignments} WHERE id=?",
                (*column_updates.values(), item_id),
            )
            if "text" in properties:
                self._conn.execute(
                    "UPDATE items_notes SET text=? WHERE id=?", (properties["text"], item_id)
                )
        self._dirty = True
        return self.get(item_id)

    def _check_update(self, item, properties):
        unknown = set(properties) - _UPDATABLE
        if unknown:
            raise ValueError("Unknown properties: " + ", ".join(sorted(unknown)))
        finite(**{key: value for key, value in properties.items() if key in _NUMERIC})
        if "scale" in properties and properties["scale"] <= 0:
            raise ValueError("scale must be positive")
        if "opacity" in properties and not 0 <= properties["opacity"] <= 1:
            raise ValueError("opacity must be 0..1")
        for key in ("name", "text"):
            if key in properties and not isinstance(properties[key], str):
                raise ValueError(f"{key} must be text")
        if "text" in properties and item.kind != "note":
            raise ValueError("text is only supported for notes")
        comment = properties.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise ValueError("comment must be text or None")
        if "locked" in properties and not isinstance(properties["locked"], bool):
            raise ValueError("locked must be boolean")
        if "parent_id" in properties:
            self._check_new_parent(item.id, properties["parent_id"])

    def _check_new_parent(self, item_id, parent_id):
        ancestors = {item_id}
        while parent_id is not None:
            if parent_id in ancestors:
                raise ValueError("Parent cycle")
            ancestors.add(parent_id)
            parent_id = self.get(parent_id).parent_id

    @staticmethod
    def _updated_matrix(matrix, properties):
        """Return the local matrix after applying x/y/scale/rotation/matrix."""
        matrix = list(matrix)
        linear = (qt.SCALE_X, qt.SHEAR_Y, qt.SHEAR_X, qt.SCALE_Y)
        if "matrix" in properties:
            if "scale" in properties or "rotation" in properties:
                raise ValueError("Use matrix or scale/rotation")
            values = properties["matrix"]
            if not isinstance(values, (list, tuple)):
                raise ValueError("matrix must be a list or tuple of four numbers")
            if len(values) != 4:
                raise ValueError("matrix must contain four numbers")
            finite(**{str(index): value for index, value in enumerate(values)})
            for position, value in zip(linear, values):
                matrix[position] = value
        elif "scale" in properties or "rotation" in properties:
            scale_x, shear_y, shear_x, scale_y = (matrix[position] for position in linear)
            # Never flatten perspective, mirroring or shear implicitly.
            if qt.has_perspective(matrix, tolerance=0) or scale_x * scale_y - shear_y * shear_x <= 0:
                raise ValueError("Use matrix for a reflected or perspective transform")
            axes_are_perpendicular = math.isclose(
                scale_x * shear_x + shear_y * scale_y, 0, abs_tol=1e-8
            )
            axes_have_same_length = math.isclose(
                math.hypot(scale_x, shear_y), math.hypot(shear_x, scale_y)
            )
            if not (axes_are_perpendicular and axes_have_same_length):
                raise ValueError("Use matrix for a sheared or nonuniform transform")
            new = qt.affine(
                scale=properties.get("scale", math.hypot(scale_x, shear_y)),
                rotation=properties.get("rotation", math.degrees(math.atan2(shear_y, scale_x))),
            )
            for position in linear:
                matrix[position] = new[position]
        matrix[qt.TRANSLATE_X] = properties.get("x", matrix[qt.TRANSLATE_X])
        matrix[qt.TRANSLATE_Y] = properties.get("y", matrix[qt.TRANSLATE_Y])
        return matrix

    # --- Notes ---------------------------------------------------------------------

    def style_note(self, item_id, *, background_color="#ff202020",
                   text_color="#ffffffff", width=-1, height=-1):
        """Set note colours (#AARRGGBB) and a fixed size, or -1 for automatic."""
        self._require_kind(item_id, "note")
        background_color = self._color(background_color)
        text_color = self._color(text_color)
        finite(width=width, height=height)
        if any(value != -1 and value <= 0 for value in (width, height)):
            raise ValueError("Note dimensions must be positive or -1 for automatic")
        with self._conn:
            self._conn.execute(
                "UPDATE items_notes SET fixed_size=?, background_color=?, text_color=?"
                " WHERE id=?",
                (qt.size(width, height), background_color, text_color, item_id),
            )
        self._dirty = True

    def note_style(self, item_id):
        self._require_kind(item_id, "note")
        row = self._row("items_notes", item_id)
        try:
            width, height = qt.read_size(row["fixed_size"])
        except ValueError as exc:
            raise UnsupportedFormat(str(exc)) from exc
        return dict(
            width=width,
            height=height,
            background_color=row["background_color"],
            text_color=row["text_color"],
            style=row["style"],
        )

    # --- Groups --------------------------------------------------------------------

    def group_style(self, item_id):
        """Background colour (#AARRGGBB or '') and selection lock mode of a group."""
        self._require_kind(item_id, "group")
        row = self._row("items_groups", item_id)
        return dict(background_color=row["background_color"] or "",
                    locked_mode=bool(row["lock_mode"]))

    def style_group(self, item_id, *, color=None, locked_mode=None):
        self._require_kind(item_id, "group")
        updates = {}
        if color is not None:
            updates["background_color"] = self._color(color)
        if locked_mode is not None:
            if not isinstance(locked_mode, bool):
                raise ValueError("locked_mode must be boolean")
            updates["lock_mode"] = locked_mode
        if updates:
            assignments = ", ".join(f"{column}=?" for column in updates)
            with self._conn:
                self._conn.execute(f"UPDATE items_groups SET {assignments} WHERE id=?",
                                   (*updates.values(), item_id))
            self._dirty = True

    # --- Sibling order -------------------------------------------------------------

    def _children_rows(self, parent_id):
        return self._conn.execute(
            "SELECT id, sort_order FROM items WHERE parent=?",
            (NO_PARENT if parent_id is None else parent_id,),
        ).fetchall()

    def ordered_children(self, parent_id=None):
        """Children sorted by their stored sort key (ties by ID).

        PureRef rewrites these keys when it saves; drawing order is decided by
        z (see scene.drawing_order), not by this list.
        """
        rows = self._children_rows(parent_id)
        keyed = sorted(rows, key=lambda row: (qt.read_sort_order(row["sort_order"]), row["id"]))
        return [row["id"] for row in keyed]

    def reorder(self, item_ids):
        """Set the sibling order; every child of that parent must be listed once."""
        ids = list(item_ids)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Provide each sibling exactly once")
        parent = self.get(ids[0]).parent_id
        if any(self.get(item_id).parent_id != parent for item_id in ids):
            raise ValueError("Ordering cannot mix parents")
        if set(ids) != {row["id"] for row in self._children_rows(parent)}:
            raise ValueError("Provide each sibling exactly once")
        with self._conn:
            self._conn.executemany(
                "UPDATE items SET sort_order=? WHERE id=?",
                [(qt.sort_order(position), item_id) for position, item_id in enumerate(ids)],
            )
        self._dirty = True

    # --- Image options -------------------------------------------------------------

    def image_options(self, item_id):
        """Raw native image settings (bounds as hex, transform as nine numbers)."""
        self._require_kind(item_id, "image")
        row = dict(self._row("items_images", item_id))
        row["image_transform"] = qt.read_transform(row["image_transform"])
        row["image_bounds"] = qt.binary(row["image_bounds"]).hex()
        return row

    def image_settings(self, item_id):
        """Image settings in plain terms, for hosts such as FrameRef.

        `crop` is (x, y, width, height) in bitmap pixels, or None when the
        stored bounds are not a simple rectangle (see `image_options`).
        """
        item = self._require_kind(item_id, "image")
        row = self._row("items_images", item_id)
        image_matrix = qt.read_transform(row["image_transform"])
        asset = self._row("images", item.image_id) if item.image_id is not None else None
        crop = self._crop_in_bitmap_pixels(row["image_bounds"], image_matrix)
        full_image = asset is not None and crop == (0, 0, asset["width"], asset["height"])
        axis_aligned = not (image_matrix[qt.SHEAR_Y] or image_matrix[qt.SHEAR_X])
        return dict(
            pixel_size=(asset["width"], asset["height"]) if asset else None,
            embedded=bool(asset and asset["data"] is not None),
            crop=crop,
            cropped=crop is None or not full_image,
            flip_horizontal=axis_aligned and image_matrix[qt.SCALE_X] < 0,
            flip_vertical=axis_aligned and image_matrix[qt.SCALE_Y] < 0,
            grayscale=bool(row["flags"] & FLAG_GRAYSCALE),
            bilinear=bool(row["flags"] & FLAG_BILINEAR),
            frame=row["playback_frame"],
            speed=row["playback_speed"],
            playing=bool(row["playback_state"]),
        )

    def configure_image(self, item_id, *, crop=None, flip_horizontal=None,
                        flip_vertical=None, grayscale=None, bilinear=None,
                        frame=None, speed=None):
        """Change crop, mirroring, filters and animation settings of an image.

        `crop` is (x, y, width, height) in pixels of the original bitmap. It
        keeps referring to the same pixels before and after a flip.
        """
        item = self._require_kind(item_id, "image")
        asset = self._row("images", item.image_id)
        row = self._row("items_images", item_id)
        updates = {}
        if crop is not None:
            self._check_crop(crop, asset)
        image_matrix = qt.read_transform(row["image_transform"])
        # PureRef stores the crop in item space, after the flip. Keep the crop
        # attached to the same bitmap pixels, whatever order options are set in.
        crop_before_flip = self._crop_in_bitmap_pixels(row["image_bounds"], image_matrix)
        flipping = flip_horizontal is not None or flip_vertical is not None
        if flipping:
            self._apply_flips(image_matrix, flip_horizontal, flip_vertical)
            updates["image_transform"] = qt.transform(image_matrix)
        if crop is None and flipping:
            crop = crop_before_flip  # same bitmap pixels, now seen mirrored
        if crop is not None:
            bitmap_to_item = image_matrix
            updates["image_bounds"] = qt.path_rect(*self._map_rect(crop, bitmap_to_item))
        flags = row["flags"]
        for value, bit in ((grayscale, FLAG_GRAYSCALE), (bilinear, FLAG_BILINEAR)):
            if value is not None:
                if not isinstance(value, bool):
                    raise ValueError("image flags must be boolean")
                flags = flags | bit if value else flags & ~bit
        if grayscale is not None or bilinear is not None:
            updates["flags"] = flags
        if frame is not None:
            self._check_frame(frame, asset)
            updates["playback_frame"] = frame
        if speed is not None:
            finite(speed=speed)
            if speed <= 0:
                raise ValueError("speed must be positive")
            updates["playback_speed"] = speed
        if updates:
            assignments = ", ".join(f"{column}=?" for column in updates)
            with self._conn:
                self._conn.execute(
                    f"UPDATE items_images SET {assignments} WHERE id=?",
                    (*updates.values(), item_id),
                )
            self._dirty = True

    @staticmethod
    def _check_crop(crop, asset):
        if not isinstance(crop, (tuple, list)) or len(crop) != 4:
            raise ValueError("crop must contain x, y, width, height")
        x, y, width, height = crop
        finite(x=x, y=y, width=width, height=height)
        inside = (
            x >= 0 and y >= 0 and width > 0 and height > 0
            and x + width <= asset["width"] and y + height <= asset["height"]
        )
        if not inside:
            raise ValueError("Crop must fit inside the bitmap")

    @staticmethod
    def _apply_flips(image_matrix, flip_horizontal, flip_vertical):
        """Mirror the bitmap-to-item matrix in place around the item centre."""
        axis_aligned = (
            all(abs(image_matrix[position]) <= 1e-12
                for position in (qt.SHEAR_Y, qt.PERSPECTIVE_X, qt.SHEAR_X, qt.PERSPECTIVE_Y))
            and image_matrix[qt.PERSPECTIVE_W] == 1
            and image_matrix[qt.SCALE_X] != 0
            and image_matrix[qt.SCALE_Y] != 0
        )
        if not axis_aligned:
            raise UnsupportedFormat("Flip requires an axis-aligned image transform")
        horizontal = (flip_horizontal, qt.SCALE_X, (qt.SCALE_X, qt.SHEAR_X, qt.TRANSLATE_X))
        vertical = (flip_vertical, qt.SCALE_Y, (qt.SHEAR_Y, qt.SCALE_Y, qt.TRANSLATE_Y))
        for wanted, scale_position, positions in (horizontal, vertical):
            if wanted is None:
                continue
            if not isinstance(wanted, bool):
                raise ValueError("flip must be boolean")
            currently_flipped = image_matrix[scale_position] < 0
            if currently_flipped != wanted:
                for position in positions:
                    image_matrix[position] *= -1

    @staticmethod
    def _check_frame(frame, asset):
        if not _is_integer(frame) or frame < 0:
            raise ValueError("frame must be a nonnegative integer")
        if asset["data"] is None:
            raise UnsupportedFormat("Frame selection requires an embedded image")
        with Image.open(io.BytesIO(asset["data"])) as image:
            if frame >= getattr(image, "n_frames", 1):
                raise ValueError("frame is outside the animation")

    @staticmethod
    def _map_rect(rect, matrix):
        """Map an (x, y, width, height) rectangle through an axis-aligned matrix."""
        if matrix[qt.SHEAR_Y] or matrix[qt.SHEAR_X] or qt.has_perspective(matrix, 0):
            raise UnsupportedFormat("Crop requires an axis-aligned image transform")
        x, y, width, height = rect
        corners = [qt.map_point(matrix, corner_x, corner_y)
                   for corner_x, corner_y in ((x, y), (x + width, y + height))]
        left, right = sorted(point[0] for point in corners)
        top, bottom = sorted(point[1] for point in corners)
        return left, top, right - left, bottom - top

    def _crop_in_bitmap_pixels(self, image_bounds, image_matrix):
        """Return the visible part of the bitmap as (x, y, width, height).

        Returns None when the stored bounds are not a simple rectangle.
        """
        bounds = qt.read_path_rect(image_bounds)
        if bounds is None:
            return None
        try:
            return self._map_rect(bounds, qt.inverse(image_matrix))
        except (UnsupportedFormat, ValueError):
            return None

    # --- Copying and removing ------------------------------------------------------

    def duplicate(self, item_id, **properties):
        """Copy one item (not its children); the copy shares the image data."""
        self.get(item_id)
        new_id = self._next_id()
        with self._conn:
            for table in ("items",) + KIND_TABLES:
                row = self._row(table, item_id)
                if row is None:
                    continue
                values = dict(row)
                values["id"] = new_id
                if table == "items":
                    values["sort_order"] = qt.sort_order(new_id)
                    values["z"] = float(new_id + 1)
                columns = ", ".join(f'"{column}"' for column in values)
                placeholders = ", ".join("?" for _ in values)
                self._conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    tuple(values.values()),
                )
            if properties:
                self.update(new_id, **properties)
        if self._legacy and item_id in self._legacy["items"]:
            # Keep legacy-only details such as the crop on the copy as well.
            self._legacy["items"][new_id] = copy.deepcopy(self._legacy["items"][item_id])
        self._dirty = True
        return new_id

    def remove(self, item_id):
        """Remove an item with all its descendants, and images nobody uses any more."""
        self.get(item_id)
        ids = [
            row[0]
            for row in self._conn.execute(
                "WITH RECURSIVE branch(id) AS ("
                " SELECT id FROM items WHERE id=?"
                " UNION SELECT items.id FROM items JOIN branch ON items.parent = branch.id"
                ") SELECT id FROM branch",
                (item_id,),
            )
        ]
        with self._conn:
            for removed_id in ids:
                for table in KIND_TABLES + ("items",):
                    self._conn.execute(f"DELETE FROM {table} WHERE id=?", (removed_id,))
            self._delete_unused_images()
        if self._legacy:
            # IDs are reused by later additions; a new item must not inherit an
            # old legacy record (and with it the old image's crop).
            for removed_id in ids:
                self._legacy["items"].pop(removed_id, None)
        self._dirty = True

    def _delete_unused_images(self):
        # NOT EXISTS (unlike NOT IN) still works when some image reference is NULL.
        self._conn.execute(
            "DELETE FROM images WHERE NOT EXISTS"
            " (SELECT 1 FROM items_images WHERE items_images.image = images.id)"
        )

    # --- Saving --------------------------------------------------------------------

    def refresh_preview(self):
        """Redraw the 256x256 thumbnail and store the scene rectangle.

        PureRef shows the thumbnail in its file list. Rendering is approximate
        (see render.py); a picture that cannot be decoded is drawn as empty.
        """
        from .render import render_thumbnail, scene_rect

        rectangle = scene_rect(self)
        thumbnail = render_thumbnail(self)
        metadata_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(metadata)")}
        with self._conn:
            if rectangle is not None and "scene_rect" in metadata_columns:
                self._conn.execute("UPDATE metadata SET scene_rect=?", (qt.rect(*rectangle),))
            self._conn.execute("UPDATE metadata SET thumbnail=?", (thumbnail,))
        self._thumbnail = thumbnail
        self._dirty = True

    def save(self, path, *, overwrite=False, format=None, refresh_preview=True):
        """Save atomically. An unedited board is written back byte for byte.

        An edited modern board also gets a new thumbnail and scene rectangle,
        unless `refresh_preview=False`.
        """
        target = format or self.format
        if target == "modern" and self._dirty and refresh_preview:
            try:
                self.refresh_preview()
            except (OSError, ValueError, KeyError):
                pass  # keep the previous thumbnail rather than fail the save
        if target not in ("modern", "legacy"):
            raise ValueError("format must be modern or legacy")
        unchanged = target == self.format and not self._dirty
        if unchanged and self._raw is not None:
            atomic_write(path, self._raw, overwrite)
        elif unchanged and self._original_path is not None:
            atomic_write_stream(
                path,
                lambda stream: _copy_file_into(self._original_path, stream),
                overwrite,
            )
        elif target == "legacy":
            from .legacy import dump_legacy

            atomic_write(path, dump_legacy(self), overwrite)
        else:
            version = self.application_version if self.format == "modern" else None
            if self._database_path is None:
                atomic_write(path, pack_container(self._conn, version, self._thumbnail), overwrite)
            else:
                atomic_write_stream(
                    path,
                    lambda stream: write_container_file(
                        self._conn, self._database_path, version, self._thumbnail, stream
                    ),
                    overwrite,
                )
        return Path(path)

    # --- Exporting -----------------------------------------------------------------

    def export_assets(self, directory):
        """Write every embedded image's original bytes; return the file paths."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        written = []
        for row in self._conn.execute("SELECT id, format, data FROM images ORDER BY id"):
            if row["data"] is None:
                continue  # linked data is not implicitly read from disk
            path = directory / f'image-{row["id"]}.{asset_suffix(row["format"])}'
            atomic_write(path, bytes(row["data"]))
            written.append(str(path))
        return written

    def to_dict(self):
        """A JSON-friendly summary of the whole board."""
        from dataclasses import asdict
        from .profiles import compatibility_status

        image_fields = ("id", "width", "height", "format", "source", "origin", "checksum")
        return {
            "schema_version": 1,
            "format": self.format,
            "application_version": self.application_version,
            "compatibility": compatibility_status(self),
            "items": [asdict(item) for item in self.items],
            "images": [
                {field: row[field] for field in image_fields}
                | {"embedded": row["data"] is not None}
                for row in self._conn.execute("SELECT * FROM images ORDER BY id")
            ],
        }


def asset_suffix(image_format):
    """File extension for an `images.format` value (unsafe values become 'bin')."""
    suffix = (image_format or "bin").lower()
    return suffix if suffix.isalnum() else "bin"


def _copy_file_into(path, stream):
    with open(path, "rb") as source:
        shutil.copyfileobj(source, stream, 4 * 1024 * 1024)
