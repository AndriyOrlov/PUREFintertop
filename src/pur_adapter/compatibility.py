"""Explicitly simplify a board for PureRef 1.x and other image-only tools."""

import hashlib
from html.parser import HTMLParser
import io
from pathlib import Path
import re
from .board import Board
from .container import UnsupportedFormat
from .drawings import rasterize_drawing
from . import qt
from .images import open_as_displayed
from .scene import drawing_order, world_properties  # world_properties: old import path


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "style", "script"):
            self.skip += 1
        if not self.skip and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("head", "style", "script"):
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in ("p", "li", "div"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


# Notes PureRef saves as rich text start with a doctype, <html> or <p>.
# A plain note that merely mentions "<paint>" must stay untouched.
_RICH_TEXT_START = re.compile(r"\s*<(!doctype|html|qt|p)[\s>]", re.IGNORECASE)


def is_rich_text(text):
    return bool(_RICH_TEXT_START.match(text))


def plain_text(text):
    if not is_rich_text(text):
        return text
    parser = PlainText()
    parser.feed(text)
    return "".join(parser.parts).strip()


def clone_board(board):
    """Kept for older imports; use `board.copy()`."""
    return board.copy()


def bake_image(board, item, opacity, changes):
    asset = board._conn.execute(
        "SELECT * FROM images WHERE id=?", (item.image_id,)
    ).fetchone()
    native = board._conn.execute(
        "SELECT * FROM items_images WHERE id=?", (item.id,)
    ).fetchone()
    if asset["data"] is None:
        raise UnsupportedFormat(f"Item {item.id}: linked image must be embedded first")
    if board.format == "modern":
        expected_bounds = qt.image_bounds(asset["width"], asset["height"])
        expected_matrix = qt.affine(-asset["width"] / 2, -asset["height"] / 2)
        actual_matrix = qt.read_transform(native["image_transform"])
        if qt.binary(native["image_bounds"]) != qt.binary(expected_bounds) or any(
            abs(a - b) > 1e-8 for a, b in zip(actual_matrix, expected_matrix)
        ):
            raise UnsupportedFormat(
                f"Item {item.id}: cropped/transformed image needs a pre-rendered PNG; export stopped"
            )
        if native["flags"] != 1:
            raise UnsupportedFormat(
                f'Item {item.id}: unsupported image flags {native["flags"]}'
            )
    blob = bytes(asset["data"])
    with open_as_displayed(blob) as image:
        frames = getattr(image, "n_frames", 1)
        frame = min(max(0, native["playback_frame"] or 0), frames - 1)
        if frames > 1:
            image.seek(frame)
            changes.append(
                {
                    "item_id": item.id,
                    "code": "animation_frame_selected",
                    "frame": frame,
                    "frames": frames,
                }
            )
        needs_png = (
            not blob.startswith(b"\x89PNG\r\n\x1a\n") or opacity != 1 or frames > 1
        )
        if not needs_png:
            return
        pixels = image.convert("RGBA")
        if opacity != 1:
            alpha = pixels.getchannel("A").point(
                [round(value * opacity) for value in range(256)]
            )
            pixels.putalpha(alpha)
            changes.append(
                {"item_id": item.id, "code": "opacity_baked", "opacity": opacity}
            )
        output = io.BytesIO()
        pixels.save(output, format="PNG", icc_profile=image.info.get("icc_profile"))
    png = output.getvalue()
    new_image_id = board._next_id("images")
    board._conn.execute(
        "INSERT INTO images (id,width,height,checksum,format,source,origin,data,source_type) VALUES (?,?,?,?,?,?,?,?,1)",
        (
            new_image_id,
            asset["width"],
            asset["height"],
            hashlib.md5(png).hexdigest(),
            "PNG",
            asset["source"],
            asset["origin"],
            png,
        ),
    )
    board._conn.execute(
        "UPDATE items_images SET image=?,playback_frame=0,playback_state=0,playback_speed=1 WHERE id=?",
        (new_image_id, item.id),
    )
    if frames == 1 and asset["format"].upper() != "PNG":
        changes.append(
            {
                "item_id": item.id,
                "code": "image_converted_to_png",
                "from": asset["format"],
            }
        )


def simplify_board(board, *, drawing_scale=4):
    """Return a separate flat image/note board and an explicit conversion report."""
    simple = clone_board(board)
    changes = []
    try:
        for item in simple.items:
            if item.comment:
                simple.add_note(
                    item.comment,
                    x=0,
                    y=120,
                    name="Comment: " + item.name,
                    parent_id=item.id,
                )
                simple.update(item.id, comment=None)
                changes.append({"item_id": item.id, "code": "comment_to_note"})
        for item in simple.items:
            if item.kind == "drawing":
                details = rasterize_drawing(simple, item.id, scale=drawing_scale)
                changes.append(
                    {"item_id": item.id, "code": "drawing_rasterized", **details}
                )
            elif item.kind == "unknown":
                raise UnsupportedFormat(f"Unsupported item {item.id}")
        world = world_properties(simple)
        # z only orders siblings, so a flat board needs one global order.
        # Number the items in the order PureRef draws them.
        flat_z = {
            item_id: float(position + 1)
            for position, item_id in enumerate(drawing_order(simple))
        }
        renumbered_children = 0
        with simple._conn:
            for item in simple.items:
                matrix, opacity = world[item.id]
                if qt.has_perspective(matrix):
                    raise UnsupportedFormat(
                        f"Item {item.id}: perspective requires rasterization before legacy export"
                    )
                if item.kind == "group":
                    changes.append(
                        {
                            "item_id": item.id,
                            "code": "group_flattened",
                            "name": item.name,
                        }
                    )
                    simple._conn.execute(
                        "DELETE FROM items_groups WHERE id=?", (item.id,)
                    )
                    simple._conn.execute("DELETE FROM items WHERE id=?", (item.id,))
                    continue
                if item.kind == "image":
                    bake_image(simple, item, opacity, changes)
                    opacity = 1
                elif item.kind == "note":
                    text = plain_text(item.text)
                    if text != item.text:
                        changes.append(
                            {"item_id": item.id, "code": "rich_text_to_plain"}
                        )
                    simple._conn.execute(
                        "UPDATE items_notes SET text=? WHERE id=?", (text, item.id)
                    )
                if item.locked:
                    changes.append({"item_id": item.id, "code": "item_lock_discarded"})
                if item.parent_id is not None and flat_z[item.id] != item.z:
                    renumbered_children += 1
                simple._conn.execute(
                    "UPDATE items SET transform=?,parent=-1,opacity=?,locked=0,z=? "
                    "WHERE id=?",
                    (qt.transform(matrix), opacity, flat_z[item.id], item.id),
                )
        if renumbered_children:
            changes.append(
                {"code": "stacking_flattened", "renumbered_children": renumbered_children}
            )
        simple._dirty = True
        return simple, {
            "schema_version": 1,
            "target_format": "legacy-1.10",
            "changes": changes,
            "unresolved": [],
        }
    except Exception:
        simple.close()
        raise


def export_legacy(board, path, *, overwrite=False, drawing_scale=4):
    simple, report = simplify_board(board, drawing_scale=drawing_scale)
    with simple:
        simple.save(path, format="legacy", overwrite=overwrite)
    report["output"] = str(Path(path))
    return report
