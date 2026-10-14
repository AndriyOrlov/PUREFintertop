"""A one-page HTML report about a board, laid out like the HDRDisplayCalibrate
verification report: graded summary tiles, charts, a preview and a table.

    pur-adapter report board.pur report.html

The page is self-contained (inline SVG charts and an embedded preview), so it
can be opened offline or attached to an issue.
"""

import base64
import html
import io
import json
import tempfile
from collections import Counter
from pathlib import Path
from PIL import Image
from . import charts
from .images import open_as_displayed

MEGABYTE = 1024 * 1024
FORMAT_COLORS = {"PNG": "#60cdff", "JPG": "#ffc83d", "JPEG": "#ffc83d", "GIF": "#6ccb5f",
                 "WEBP": "#c58af9", "TIF": "#ff9f6b", "TIFF": "#ff9f6b", "BMP": "#ff6b6b"}


def _average_color(data):
    """The mean colour of an image, as #rrggbb (grey when it cannot be decoded)."""
    try:
        with open_as_displayed(data) as image:
            image.draft("RGB", (64, 64))
            tiny = image.convert("RGB").resize((1, 1), Image.Resampling.BOX)
            red, green, blue = tiny.getpixel((0, 0))
    except Exception:  # broken or unsupported picture: still report its size
        return charts.MUTED
    return f"#{red:02x}{green:02x}{blue:02x}"


def _megabytes(size):
    return f"{size / MEGABYTE:.1f} MB" if size >= MEGABYTE / 10 else f"{size / 1024:.0f} KB"


def collect(board):
    """Numbers for the report, gathered once."""
    from .assets import linked_images

    items = board.items
    kinds = Counter(item.kind for item in items)
    uses = Counter(item.image_id for item in items if item.kind == "image")
    images = []
    for row in board._conn.execute(
        "SELECT id, format, width, height, length(data) AS size, data IS NULL AS linked,"
        " source, checksum FROM images ORDER BY id"
    ).fetchall():
        data = board._conn.execute("SELECT data FROM images WHERE id=?", (row["id"],)).fetchone()[0]
        images.append({
            "id": row["id"], "format": (row["format"] or "?").upper(),
            "width": row["width"], "height": row["height"],
            "size": row["size"] or 0, "linked": bool(row["linked"]),
            "name": Path((row["source"] or "").replace("\\", "/")).name or f"image {row['id']}",
            "checksum": row["checksum"], "uses": uses.get(row["id"], 0),
            "color": _average_color(bytes(data)) if data is not None else charts.MUTED,
        })
    duplicates = Counter(image["checksum"] for image in images if not image["linked"])
    duplicate_bytes = sum(image["size"] for image in images
                          if duplicates[image["checksum"]] > 1) // 2
    groups = [item for item in items if item.kind == "group" and item.parent_id is None]
    children = Counter(item.parent_id for item in items)
    return {
        "items": items, "kinds": kinds, "images": images, "groups": groups,
        "children": children, "linked": linked_images(board),
        "duplicate_bytes": duplicate_bytes,
        "embedded_bytes": sum(image["size"] for image in images),
    }


def round_trip_check(board):
    """Export to the host JSON and back; list anything that did not survive."""
    from .conformance import describe
    from .manifest import export_manifest, import_manifest

    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder) / "package"
        export_manifest(board, package, scene_preview=False)
        restored, report = import_manifest(package / "board.json")
        with restored:
            before = json.loads(json.dumps(describe(board)))
            after = json.loads(json.dumps(describe(restored)))
    lost = []
    for old, new in zip(before["items"], after["items"]):
        for key in old:
            if old[key] != new.get(key):
                lost.append(f"item {old['id']}: {key}")
    if len(before["items"]) != len(after["items"]):
        lost.append("item count")
    return lost + [f"{entry['code']} ({entry.get('item_id')})" for entry in report["unresolved"]]


# --- HTML building blocks ----------------------------------------------------------

_STYLE = f"""
body {{ background: {charts.PAGE}; color: #ffffff; font-family: {charts.FONT};
       margin: 0; padding: 32px 40px; }}
h1 {{ font-size: 28px; font-weight: 600; margin: 0 0 4px; }}
h2 {{ font-size: 18px; font-weight: 600; margin: 28px 0 10px; }}
.sub, .caption {{ color: {charts.LABEL}; font-size: 13px; }}
.caption {{ margin: 18px 0 6px; }}
.card {{ background: {charts.CARD}; border-radius: 8px; padding: 18px 20px; margin-top: 16px; }}
.tiles {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.tile {{ background: {charts.CARD}; border-radius: 8px; padding: 10px 14px; min-width: 150px; }}
.tile .value {{ font-size: 22px; font-weight: 600; margin-top: 2px; }}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 1100px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
svg {{ max-width: 100%; height: auto; }}
table {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
th {{ color: {charts.LABEL}; font-weight: 400; text-align: left; padding: 4px 10px 4px 0; }}
td {{ padding: 3px 10px 3px 0; border-top: 1px solid rgba(255,255,255,0.06); }}
.swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }}
img.preview {{ width: 100%; border-radius: 6px; }}
"""


def _tile(label, value, color="#ffffff"):
    return (f'<div class="tile"><div class="sub">{html.escape(label)}</div>'
            f'<div class="value" style="color:{color}">{html.escape(value)}</div></div>')


def _caption(text):
    return f'<div class="caption">{html.escape(text)}</div>'


def _summary_tiles(board, data, lost):
    from .profiles import compatibility_status

    status = compatibility_status(board)["status"]
    kinds = data["kinds"]
    images = data["images"]
    largest = max((image["size"] for image in images), default=0)
    missing = [link for link in data["linked"] if not link["exists"]]
    tiles = [
        _tile("Format", f"{board.format} · {board.application_version or 'unrecorded'}",
              charts.GOOD if status == "tested_release" else charts.WARM),
        _tile("Items", f"{len(data['items'])}"),
        _tile("Images placed / stored", f"{kinds['image']} / {len(images)}"),
        _tile("Notes · groups · drawings", f"{kinds['note']} · {kinds['group']} · {kinds['drawing']}"),
        _tile("Embedded image data", _megabytes(data["embedded_bytes"])),
        _tile("Largest image", _megabytes(largest),
              charts.grade(largest / MEGABYTE, 5, 20)),
        _tile("Duplicate data", _megabytes(data["duplicate_bytes"]),
              charts.GOOD if data["duplicate_bytes"] == 0 else charts.WARM),
        _tile("Linked files missing", f"{len(missing)} of {len(data['linked'])}",
              charts.GOOD if not missing else charts.BAD),
        _tile("Host round trip", "lossless" if not lost else f"{len(lost)} differences",
              charts.GOOD if not lost else charts.BAD),
    ]
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _size_chart(images):
    ordered = sorted((image for image in images if not image["linked"]),
                     key=lambda image: image["size"], reverse=True)
    largest = ordered[0]["size"] if ordered else 0
    unit, divisor = ("MB", MEGABYTE) if largest >= MEGABYTE else ("KB", 1024)
    bars = [(image["size"] / divisor, image["color"]) for image in ordered]
    # Dashed guides at 1, 5 and 20 MB: comfortable, heavy, very heavy.
    thresholds = [(level * MEGABYTE / divisor, color)
                  for level, color in ((1, charts.GOOD), (5, charts.WARM), (20, charts.BAD))]
    return charts.bar_chart(
        bars, thresholds=thresholds,
        caption=f"{unit} per stored image, largest first (bar colour = image's average colour;"
                " dashed lines at 1, 5 and 20 MB)",
        width=1100, height=220, standalone=False,
    )


def _pixel_chart(images):
    stored = [image for image in images if image["width"] and image["height"]]
    reference = [
        charts.Series("Full HD", [(10, 1080), (1920, 1080), (1920, 10)], charts.MUTED, dashed=True),
        charts.Series("4K", [(10, 2160), (3840, 2160), (3840, 10)], charts.WARM, dashed=True),
    ]
    points = charts.Series("images", [(image["width"], image["height"]) for image in stored],
                           charts.ACCENT, markers=True, lines=False,
                           point_colors=[image["color"] for image in stored])
    largest = max([max(image["width"], image["height"]) for image in stored] + [4000])
    return charts.line_chart(reference + [points], x_label="width, px", y_label="height, px",
                             log_x=True, log_y=True, x_range=(10, largest * 1.3),
                             y_range=(10, largest * 1.3), width=540, height=320,
                             standalone=False)


def _format_chart(images):
    by_format = Counter()
    for image in images:
        by_format[image["format"]] += image["size"]
    parts = [(name, size, FORMAT_COLORS.get(name, charts.MUTED))
             for name, size in by_format.most_common() if size]
    return charts.share_bar(parts, width=540, standalone=False) if parts else ""


def _group_chart(data):
    groups = data["groups"]
    if not groups:
        return ""
    bars = [(data["children"][group.id], charts.ACCENT) for group in groups]
    return (_caption(f"Direct children of each top-level group ({len(groups)} groups)")
            + charts.bar_chart(bars, width=540, height=180, standalone=False))


def _preview(board):
    from .render import render_scene

    picture = render_scene(board, width=1600).convert("RGB")
    output = io.BytesIO()
    picture.save(output, format="JPEG", quality=88)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f'<img class="preview" alt="Approximate render" src="data:image/jpeg;base64,{encoded}">'


def _image_table(images, limit=200):
    rows = []
    for image in sorted(images, key=lambda image: image["size"], reverse=True)[:limit]:
        state = "linked" if image["linked"] else "embedded"
        rows.append(
            "<tr>"
            f'<td><span class="swatch" style="background:{image["color"]}"></span>'
            f'{html.escape(image["name"])}</td>'
            f'<td>{image["format"]}</td><td>{image["width"]} × {image["height"]}</td>'
            f'<td>{_megabytes(image["size"])}</td><td>{image["uses"]}</td><td>{state}</td>'
            "</tr>"
        )
    note = (f'<div class="sub">Showing the {limit} largest of {len(images)} images.</div>'
            if len(images) > limit else "")
    return ("<table><tr><th>Image</th><th>Format</th><th>Pixels</th><th>Size</th>"
            f"<th>Placed</th><th>Stored</th></tr>{''.join(rows)}</table>{note}")


def board_report(board, title="Board report", *, check_round_trip=True, preview=True):
    """Return the report as an HTML string."""
    data = collect(board)
    lost = round_trip_check(board) if check_round_trip else []
    sections = [
        f"<h1>{html.escape(title)}</h1>",
        '<div class="sub">What is on the board, how big it is, and whether it survives the '
        "exchange with a host application unchanged.</div>",
        f'<div class="card">{_summary_tiles(board, data, lost)}</div>',
        '<div class="card">',
        _caption("Image sizes"), _size_chart(data["images"]),
        '<div class="grid2"><div>', _caption("Pixel dimensions (log scale, dot colour = image)"),
        _pixel_chart(data["images"]), "</div><div>",
        _caption("Stored data by format"), _format_chart(data["images"]),
        _group_chart(data), "</div></div>", "</div>",
    ]
    if lost:
        items = "".join(f"<li>{html.escape(entry)}</li>" for entry in lost[:50])
        sections.append(f'<div class="card"><h2>Round-trip differences</h2><ul>{items}</ul></div>')
    if preview and data["items"]:
        sections.append(f'<div class="card"><h2>Preview</h2>{_preview(board)}'
                        '<div class="sub">Approximate render; fonts differ from PureRef.</div></div>')
    if data["images"]:
        sections.append(f'<div class="card"><h2>Images</h2>{_image_table(data["images"])}</div>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
            f"<body>{''.join(sections)}</body></html>")


def write_board_report(board, path, *, title=None, overwrite=False, **options):
    from .board import atomic_write

    page = board_report(board, title or f"Board report · {Path(path).stem}", **options)
    atomic_write(path, page.encode("utf-8"), overwrite)
    return Path(path)
