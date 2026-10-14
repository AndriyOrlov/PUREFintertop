"""A canonical, language-neutral description of a board for conformance tests.

Another implementation (for example a C++ codec inside FrameRef) can read the
files in `conformance/cases/*/board.pur`, produce the same JSON as
`describe(board)` and compare it with `expected.json`. Numbers should be
compared with a tolerance of 1e-9; everything else exactly.
"""

import hashlib
from .manifest import strokes_to_json
from .scene import drawing_order

DESCRIPTION_VERSION = 1


def _image_details(board, item):
    settings = board.image_settings(item.id)
    asset = board._row("images", item.image_id) if item.image_id is not None else None
    data = bytes(asset["data"]) if asset is not None and asset["data"] is not None else None
    return {
        "image_sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
        "embedded": data is not None,
        "format": asset["format"] if asset is not None else None,
        "pixel_size": list(settings["pixel_size"]) if settings["pixel_size"] else None,
        "crop": list(settings["crop"]) if settings["crop"] else None,
        "flip_horizontal": settings["flip_horizontal"],
        "flip_vertical": settings["flip_vertical"],
        "grayscale": settings["grayscale"],
        "bilinear": settings["bilinear"],
        "frame": settings["frame"],
        "speed": settings["speed"],
    }


def describe(board):
    """Everything the adapter understands about a board, in a stable order."""
    ranks = {}
    for parent_id in {item.parent_id for item in board.items}:
        try:
            ranks.update({item_id: index for index, item_id in
                          enumerate(board.ordered_children(parent_id))})
        except ValueError:
            pass
    items = []
    for item in board.items:
        entry = {
            "id": item.id,
            "kind": item.kind,
            "name": item.name,
            "parent_id": item.parent_id,
            "matrix": board.transform_matrix(item.id),
            "opacity": item.opacity,
            "locked": item.locked,
            "z": item.z,
            "comment": item.comment,
            "order_index": ranks.get(item.id),
        }
        if item.kind == "image":
            entry["image"] = _image_details(board, item)
        elif item.kind == "note":
            entry["text"] = item.text
            entry["style"] = board.note_style(item.id)
        elif item.kind == "group":
            entry["style"] = board.group_style(item.id)
        elif item.kind == "drawing":
            entry["strokes"] = strokes_to_json(board.drawing_strokes(item.id))
        items.append(entry)
    return {
        "description_version": DESCRIPTION_VERSION,
        "format": board.format,
        "items": items,
        "drawing_order": drawing_order(board),
    }
