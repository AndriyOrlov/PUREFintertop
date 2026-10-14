"""Legacy 1.10 bridge using the MIT Fyor codec; modern-only items are rejected."""

import contextlib
import copy
import hashlib
import io
from pathlib import Path
import struct
import tempfile
from PIL import Image
from ._vendor.purformat.purformat import PurFile
from ._vendor.purformat.items import PurImage, PurGraphicsImageItem, PurGraphicsTextItem
from .container import InvalidPurFile, UnsupportedFormat
from . import qt
from .images import open_as_displayed


def load_legacy(path, data):
    from .board import Board

    if len(data) < 224 or data[4:12] != "1.10".encode("utf-16-be"):
        raise UnsupportedFormat("Only the legacy 1.10 layout is supported")
    if struct.unpack_from(">I", data, 24)[0] != 12:
        raise UnsupportedFormat("Unrecognized legacy release field length")
    application_version = data[28:40].decode("utf-16-be").strip("\0") or None
    try:
        expected = data[44:108].decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise InvalidPurFile("Invalid legacy checksum text") from exc
    if expected != hashlib.md5(data[108:]).hexdigest():
        raise InvalidPurFile("Legacy checksum mismatch")
    count, images = struct.unpack_from(">HH", data, 12)
    refs = struct.unpack_from(">Q", data, 16)[0]
    if images > count or refs < 224 or refs + 20 * images != len(data):
        raise InvalidPurFile("Invalid legacy reference table")
    for n in range(images):
        start, end = struct.unpack_from(">QQ", data, refs + n * 20 + 4)
        if not 224 <= start <= end <= refs:
            raise InvalidPurFile("Invalid legacy image range")
    parsed = PurFile()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            parsed.read(str(path))
    except (ValueError, IndexError, struct.error, UnicodeDecodeError) as exc:
        raise InvalidPurFile(f"Unsupported or malformed legacy scene: {exc}") from exc
    b = Board()
    b._legacy = {"file": parsed, "items": {}}

    def add_text(node, parent=None):
        i = b.add_note(node.text, x=node.x, y=node.y, parent_id=parent)
        b.update(i, matrix=node.matrix, z=node.zLayer, opacity=node.opacity / 65535)
        b._legacy["items"][i] = node
        for child in node.textChildren:
            add_text(child, i)

    with b._conn:
        for asset in parsed.images:
            blob = bytes(asset.pngBinary)
            linked = blob == b"\xff" * 4
            width = height = 0
            if not linked:
                try:
                    with Image.open(io.BytesIO(blob)) as image:
                        width, height = image.size
                except Exception as exc:
                    raise InvalidPurFile("Invalid embedded legacy image") from exc
            asset_id = b._next_id("images")
            origin = asset.transforms[0].source if asset.transforms else ""
            b._conn.execute(
                "INSERT INTO images (id,width,height,checksum,format,source,origin,data,source_type) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    asset_id,
                    width,
                    height,
                    hashlib.md5(blob).hexdigest(),
                    "PNG",
                    origin,
                    origin,
                    None if linked else blob,
                    0 if linked else 1,
                ),
            )
            for node in asset.transforms:
                i = b._add_item(node.name, node.x, node.y)
                b.update(i, matrix=node.matrix, z=node.zLayer)
                b._conn.execute(
                    "INSERT INTO items_images (id,image,playback_speed,playback_frame,playback_state,flags,image_bounds,image_transform) VALUES (?,?,1,0,0,1,?,?)",
                    (
                        i,
                        asset_id,
                        qt.image_bounds(width, height),
                        qt.transform(qt.affine(-width / 2, -height / 2)),
                    ),
                )
                b._legacy["items"][i] = node
                for child in node.textChildren:
                    add_text(child, i)
        for node in parsed.text:
            add_text(node)
    b.application_version = application_version
    b.format = "legacy"
    b._raw = data
    b._dirty = False
    return b


def dump_legacy(board):
    items = board.items
    if any(item.kind not in ("image", "note") for item in items):
        raise UnsupportedFormat(
            "Legacy output cannot preserve groups or drawings; use modern"
        )
    if any(item.kind == "image" and item.parent_id is not None for item in items):
        raise UnsupportedFormat("Legacy cannot parent image items")
    for item in items:
        if item.comment:
            raise UnsupportedFormat("Comments need explicit compatibility export")
        if qt.has_perspective(board.transform_matrix(item.id)):
            # Legacy records only hold a 2x2 matrix and a position.
            raise UnsupportedFormat(
                f"Item {item.id}: legacy output cannot keep a perspective transform"
            )
        if board.format == "modern" and item.kind == "image":
            row = board._conn.execute(
                "SELECT * FROM items_images WHERE id=?", (item.id,)
            ).fetchone()
            asset = board._conn.execute(
                "SELECT width,height FROM images WHERE id=?", (item.image_id,)
            ).fetchone()
            if (
                row["flags"] != 1
                or row["playback_frame"] != 0
                or qt.binary(row["image_bounds"]) != qt.binary(qt.image_bounds(*asset))
                or qt.read_transform(row["image_transform"])
                != qt.affine(-asset[0] / 2, -asset[1] / 2)
            ):
                raise UnsupportedFormat(
                    "Image options need explicit compatibility export"
                )
        if item.locked:
            raise UnsupportedFormat(
                "Legacy cannot retain item locks; use compatibility export"
            )
        if item.kind == "image" and item.opacity != 1:
            raise UnsupportedFormat(
                "Image opacity must be baked before legacy output; use compatibility export"
            )
    native = board._legacy
    p = PurFile()
    if native:
        original = native["file"]
        p.canvas = copy.deepcopy(original.canvas)
        p.zoom = original.zoom
        p.xCanvas = original.xCanvas
        p.yCanvas = original.yCanvas
        p.folderLocation = original.folderLocation
    nodes = {}
    assets = {}
    for item in items:
        old = native["items"].get(item.id) if native else None
        if item.kind == "image":
            row = board._conn.execute(
                "SELECT * FROM images WHERE id=?", (item.image_id,)
            ).fetchone()
            if row["data"] is None:
                raise UnsupportedFormat("Editing legacy linked images is not supported")
            if isinstance(old, PurGraphicsImageItem):
                node = copy.deepcopy(old)  # keeps the legacy crop of this item
            else:
                node = PurGraphicsImageItem()
                node.reset_crop(row["width"], row["height"])
            node.source = row["source"] or "Embedded"
            node.name = item.name
            if item.image_id not in assets:
                asset = PurImage()
                blob = bytes(row["data"])
                if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
                    with open_as_displayed(blob) as image:
                        target = io.BytesIO()
                        image.convert("RGBA").save(target, format="PNG")
                        blob = target.getvalue()
                asset.pngBinary = blob
                assets[item.image_id] = asset
                p.images.append(asset)
            assets[item.image_id].transforms.append(node)
        else:
            node = (
                copy.deepcopy(old)
                if isinstance(old, PurGraphicsTextItem)
                else PurGraphicsTextItem()
            )
            node.text = item.text
            node.opacity = round(item.opacity * 65535)
        node.x, node.y = item.x, item.y
        node.matrix = list(item.matrix)
        node.zLayer = item.z
        node.textChildren = []
        nodes[item.id] = node
    for item in items:
        if item.kind != "note":
            continue
        if item.parent_id is None:
            p.text.append(nodes[item.id])
        else:
            nodes[item.parent_id].textChildren.append(nodes[item.id])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "output.pur"
        p.write(str(path))
        return path.read_bytes()
