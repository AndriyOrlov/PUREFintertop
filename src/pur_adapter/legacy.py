"""PureRef 1.x ("1.10" layout) boards, through the MIT-licensed Fyor codec.

Legacy files only know images and notes (notes may hang under images or other
notes). Anything a legacy file cannot hold is rejected with UnsupportedFormat;
use `compatibility.export_legacy` to convert it explicitly with a report.

Header fields used here (byte offsets):

    4..12   "1.10" in UTF-16BE          12  item count (uint16)
    14      image count (uint16)          16  start of the reference table (uint64)
    24      release text length (12)      28  release, e.g. "1.11.1"
    44..108 MD5 of bytes 108.. as UTF-16BE hex text
"""

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

HEADER_SIZE = 224
REFERENCE_ENTRY_SIZE = 20  # item id (uint32) + start and end offsets (2 x uint64)
LINKED_IMAGE_MARKER = b"\xff" * 4  # stands in for PNG data of a linked file
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
OPACITY_SCALE = 65535  # legacy note opacity is a 16-bit integer


def _check_legacy_header(data):
    """Validate the header and reference table before the codec parses anything."""
    if len(data) < HEADER_SIZE or data[4:12] != "1.10".encode("utf-16-be"):
        raise UnsupportedFormat("Only the legacy 1.10 layout is supported")
    if struct.unpack_from(">I", data, 24)[0] != 12:
        raise UnsupportedFormat("Unrecognized legacy release field length")
    try:
        expected = data[44:108].decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise InvalidPurFile("Invalid legacy checksum text") from exc
    if expected != hashlib.md5(data[108:]).hexdigest():
        raise InvalidPurFile("Legacy checksum mismatch")
    item_count, image_count = struct.unpack_from(">HH", data, 12)
    table_start = struct.unpack_from(">Q", data, 16)[0]
    if (
        image_count > item_count
        or table_start < HEADER_SIZE
        or table_start + REFERENCE_ENTRY_SIZE * image_count != len(data)
    ):
        raise InvalidPurFile("Invalid legacy reference table")
    for entry in range(image_count):
        start, end = struct.unpack_from(">QQ", data, table_start + entry * REFERENCE_ENTRY_SIZE + 4)
        if not HEADER_SIZE <= start <= end <= table_start:
            raise InvalidPurFile("Invalid legacy image range")
    return data[28:40].decode("utf-16-be").strip("\0") or None


def parse_with_codec(data):
    """Run the vendored codec on exactly the bytes that were validated."""
    parsed = PurFile()
    with tempfile.TemporaryDirectory() as folder:
        copy_path = Path(folder) / "board.pur"
        copy_path.write_bytes(data)
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # the codec prints
                parsed.read(str(copy_path))
        except (ValueError, IndexError, struct.error, UnicodeDecodeError) as exc:
            raise InvalidPurFile(f"Unsupported or malformed legacy scene: {exc}") from exc
    return parsed


def load_legacy(path, data):
    """Turn a 1.x file into a Board that remembers the original legacy records."""
    application_version = _check_legacy_header(data)
    board = build_board_from_codec(parse_with_codec(data), application_version)
    board._raw = data
    board._dirty = False
    return board


def build_board_from_codec(parsed, application_version):
    """Create a Board from records parsed by the legacy codec."""
    from .board import Board, FLAG_BILINEAR

    board = Board()
    board._legacy = {"file": parsed, "items": {}}

    def add_note_record(record, parent_id=None):
        item_id = board.add_note(record.text, x=record.x, y=record.y, parent_id=parent_id)
        board.update(item_id, matrix=record.matrix, z=record.zLayer,
                     opacity=record.opacity / OPACITY_SCALE)
        board._legacy["items"][item_id] = record
        for child in record.textChildren:
            add_note_record(child, item_id)

    with board._conn:
        for asset in parsed.images:
            image_bytes = bytes(asset.pngBinary)
            linked = image_bytes == LINKED_IMAGE_MARKER
            width = height = 0
            if not linked:
                try:
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        width, height = image.size
                except Exception as exc:
                    raise InvalidPurFile("Invalid embedded legacy image") from exc
            image_id = board._next_id("images")
            origin = asset.transforms[0].source if asset.transforms else ""
            board._conn.execute(
                "INSERT INTO images (id, width, height, checksum, format, source, origin,"
                " data, source_type) VALUES (?, ?, ?, ?, 'PNG', ?, ?, ?, ?)",
                (
                    image_id,
                    width,
                    height,
                    hashlib.md5(image_bytes).hexdigest(),
                    origin,
                    origin,
                    None if linked else image_bytes,
                    0 if linked else 1,
                ),
            )
            for record in asset.transforms:
                item_id = board._add_item(record.name, record.x, record.y)
                board.update(item_id, matrix=record.matrix, z=record.zLayer)
                board._conn.execute(
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
                board._legacy["items"][item_id] = record
                for child in record.textChildren:
                    add_note_record(child, item_id)
        for record in parsed.text:
            add_note_record(record)
    board.application_version = application_version
    board.format = "legacy"
    return board


def _reject_what_legacy_cannot_hold(board, items):
    if any(item.kind not in ("image", "note") for item in items):
        raise UnsupportedFormat("Legacy output cannot preserve groups or drawings; use modern")
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
            options = board._row("items_images", item.id)
            asset = board._row("images", item.image_id)
            width, height = asset["width"], asset["height"]
            has_options = (
                options["flags"] != 1
                or options["playback_frame"] != 0
                or qt.binary(options["image_bounds"]) != qt.binary(qt.image_bounds(width, height))
                or qt.read_transform(options["image_transform"]) != qt.affine(-width / 2, -height / 2)
            )
            if has_options:
                raise UnsupportedFormat("Image options need explicit compatibility export")
        if item.locked:
            raise UnsupportedFormat("Legacy cannot retain item locks; use compatibility export")
        if item.kind == "image" and item.opacity != 1:
            raise UnsupportedFormat(
                "Image opacity must be baked before legacy output; use compatibility export"
            )


def _as_png(image_bytes):
    if image_bytes.startswith(PNG_SIGNATURE):
        return image_bytes
    with open_as_displayed(image_bytes) as image:
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()


def dump_legacy(board):
    """Return legacy 1.10 file bytes for a board that only uses legacy features."""
    items = board.items
    _reject_what_legacy_cannot_hold(board, items)
    original = board._legacy
    pur_file = PurFile()
    if original:
        source = original["file"]
        pur_file.canvas = copy.deepcopy(source.canvas)
        pur_file.zoom = source.zoom
        pur_file.xCanvas = source.xCanvas
        pur_file.yCanvas = source.yCanvas
        pur_file.folderLocation = source.folderLocation
    records = {}
    assets = {}
    for item in items:
        old_record = original["items"].get(item.id) if original else None
        if item.kind == "image":
            image_row = board._row("images", item.image_id)
            if image_row["data"] is None:
                raise UnsupportedFormat("Editing legacy linked images is not supported")
            if isinstance(old_record, PurGraphicsImageItem):
                record = copy.deepcopy(old_record)  # keeps the legacy crop of this item
            else:
                record = PurGraphicsImageItem()
                record.reset_crop(image_row["width"], image_row["height"])
            record.source = image_row["source"] or "Embedded"
            record.name = item.name
            if item.image_id not in assets:
                asset = PurImage()
                asset.pngBinary = _as_png(bytes(image_row["data"]))
                assets[item.image_id] = asset
                pur_file.images.append(asset)
            assets[item.image_id].transforms.append(record)
        else:
            if isinstance(old_record, PurGraphicsTextItem):
                record = copy.deepcopy(old_record)
            else:
                record = PurGraphicsTextItem()
            record.text = item.text
            record.opacity = round(item.opacity * OPACITY_SCALE)
        record.x, record.y = item.x, item.y
        record.matrix = list(item.matrix)
        record.zLayer = item.z
        record.textChildren = []
        records[item.id] = record
    for item in items:
        if item.kind != "note":
            continue
        if item.parent_id is None:
            pur_file.text.append(records[item.id])
        else:
            records[item.parent_id].textChildren.append(records[item.id])
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "output.pur"
        pur_file.write(str(path))
        return path.read_bytes()
