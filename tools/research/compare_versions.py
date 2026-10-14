"""Produce inspectable measurements from the four committed native fixtures."""

import hashlib
import io
import json
from pathlib import Path
import struct
from PIL import Image
from pur_adapter import Board

ROOT = Path(__file__).resolve().parents[2]


def read_qstring(data, offset):
    length = struct.unpack_from(">I", data, offset)[0]
    return (
        data[offset + 4 : offset + 4 + length].decode("utf-16-be"),
        offset + 4 + length,
    )


def inspect(release):
    path = ROOT / f"fixtures/native-{release}.pur"
    data = path.read_bytes()
    identifier, offset = read_qstring(data, 0)
    result = dict(
        release=release,
        format_identifier=identifier,
        bytes=len(data),
        file_sha256=hashlib.sha256(data).hexdigest(),
    )
    if identifier == "1.10":
        app, _ = read_qstring(data, 24)
        result["header"] = dict(
            bytes=224,
            application_version=app,
            total_items=struct.unpack_from(">H", data, 12)[0],
            image_count=struct.unpack_from(">H", data, 14)[0],
            reference_table_offset=struct.unpack_from(">Q", data, 16)[0],
            checksum_payload_offset=108,
        )
    else:
        flags, size = struct.unpack_from(">IQ", data, offset)
        app, offset = read_qstring(data, offset + 12)
        checksum, offset = read_qstring(data, offset)
        checksum_end = offset
        thumb = struct.unpack_from(">I", data, offset)[0]
        header = offset + 4 + thumb
        result["header"] = dict(
            flags=flags,
            sqlite_size=size,
            application_version=app,
            bytes=header,
            checksum_payload_offset=checksum_end,
            thumbnail_bytes=thumb,
            relocated_bytes=header,
            integrity_valid=checksum == hashlib.md5(data[checksum_end:]).hexdigest(),
        )
    with Board.load(path) as board:
        result["items"] = [
            dict(
                kind=item.kind,
                name=item.name,
                x=item.x,
                y=item.y,
                matrix=list(item.matrix),
                parent_id=item.parent_id,
                z=item.z,
            )
            for item in board.items
        ]
        assets = []
        for row in board._conn.execute("SELECT * FROM images ORDER BY id"):
            with Image.open(io.BytesIO(row["data"])) as image:
                assets.append(
                    dict(
                        width=image.width,
                        height=image.height,
                        encoded_sha256=hashlib.sha256(row["data"]).hexdigest(),
                        rgba_sha256=hashlib.sha256(
                            image.convert("RGBA").tobytes()
                        ).hexdigest(),
                    )
                )
        result["assets"] = assets
        if board.format == "modern":
            result["schema"] = {
                row[0]: row[1]
                for row in board._conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            }
    return result


if __name__ == "__main__":
    records = [inspect(release) for release in ("1.10.4", "1.11.1", "2.0.3", "2.1.3")]
    report = dict(
        records=records,
        observed_modern_schemas_equal=records[2]["schema"] == records[3]["schema"],
        first_image_pixels_equal=len(
            {record["assets"][0]["rgba_sha256"] for record in records}
        )
        == 1,
    )
    print(json.dumps(report, indent=2))
