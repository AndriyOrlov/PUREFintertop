"""Reproducible 1000-image board and a guarded shuffle/restore acceptance case."""

import hashlib
import json
from pathlib import Path
import time
from PIL import Image, ImageDraw, ImageFont
from .board import Board, atomic_write
from .drawings import Stroke
from .layout import capture_layout, restore_layout, shuffle_images


def make_card(number, path):
    image = Image.new("RGB", (128, 84), tuple(number * factor % 180 + 35 for factor in (67, 43, 97)))
    draw = ImageDraw.Draw(image)
    draw.rectangle((3, 3, 124, 80), outline="white", width=2)
    draw.rectangle((10, 18, 118, 65), fill=(15, 20, 30))
    draw.text((64, 41), str(number), font=ImageFont.load_default(size=32), fill="white", anchor="mm")
    for bit in range(10):
        draw.rectangle((10 + bit * 11, 71, 17 + bit * 11, 77), fill="white" if number & (1 << bit) else "black")
    if number == 1000:
        other = image.copy()
        ImageDraw.Draw(other).rectangle((3, 3, 124, 80), outline="yellow", width=3)
        image.save(path, save_all=True, append_images=[other], duration=500, loop=0)
    else:
        image.save(path, optimize=True)


def group_markup():
    def stroke(color, width, elements, style=0):
        return Stroke(color, width, tuple(elements), 0, (0, 0), style)
    k = 0.5522847498 * 20
    ellipse = [(0, 1540, 95), (2, 1540, 95+k), (3, 1520+k, 115), (3, 1520, 115),
               (2, 1520-k, 115), (3, 1500, 95+k), (3, 1500, 95),
               (2, 1500, 95-k), (3, 1520-k, 75), (3, 1520, 75),
               (2, 1520+k, 75), (3, 1540, 95-k), (3, 1540, 95)]
    return [
        stroke((60, 205, 160, 255), 3, [(0, 0, 55), (1, 1560, 55)]),
        stroke((240, 185, 65, 255), 2, [(0, 1450, 75), (1, 1485, 75), (1, 1485, 110), (1, 1450, 110), (1, 1450, 75)]),
        stroke((80, 165, 230, 255), 2, ellipse),
        stroke((230, 95, 180, 255), 2, [(0, 1440, 125), (1, 1560, 125)]),
        stroke((160, 110, 245, 255), 2, [(0, 1150, 125), (1, 1350, 125)], 1),
        stroke((240, 80, 85, 255), 2, [(0, 1100, 90), (1, 1350, 90)], 2),
    ]


def build_numbered_board(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    assets = directory / "assets"
    assets.mkdir(exist_ok=True)
    board = Board()
    try:
        for index in range(10):
            first, last = index * 100 + 1, (index + 1) * 100
            color = f"#ff{24+2*index:02x}{32+3*index:02x}{44+4*index:02x}"
            group = board.add_group(f"Group {index+1:02d}: {first:04d}-{last:04d}",
                                    x=index % 5 * 1750, y=index // 5 * 1800,
                                    color=color, locked_mode=index % 2 == 0)
            board.update(group, comment=f"References {first}-{last}; keep captions and drawings with their images.")
            heading = board.add_note(f'<html><p align="center"><span style="font-size:32px;color:#ffffff;font-weight:700;">GROUP {index+1:02d} · {first}-{last}</span></p></html>',
                                     x=780, y=25, name=f"Heading {index+1:02d}", parent_id=group)
            board.style_note(heading, background_color=color, width=1560, height=50)
            note = board.add_note('<html><p><span style="font-size:16px;color:#ffffff;"><b>100 references</b> · <i>check captions and geometry</i> · <a href="https://www.pureref.com">PureRef</a></span></p></html>',
                                  x=690, y=105, name=f"Annotation {index+1:02d}", parent_id=group)
            board.style_note(note, background_color=color, width=1380, height=70)
            board.add_drawing(group_markup(), name=f"Markup {index+1:02d}", parent_id=group)
            for number in range(first, last+1):
                path = assets / f"{number:04d}.{'gif' if number == 1000 else 'png'}"
                make_card(number, path)
                slot = number - first
                item = board.add_image(path, x=70 + slot % 10 * 160,
                                       y=205 + slot // 10 * 150, name=f"Image {number:04d}", parent_id=group)
                caption = board.add_note(f'<html><p align="center" style="font-size:14px;color:#ffffff;">{number} · Reference {number}</p></html>',
                                         x=0, y=70, name=f"Caption {number:04d}", parent_id=item)
                board.style_note(caption, background_color=color, width=145, height=36)
                if number % 100 == 1:
                    board.add_drawing([Stroke((250, 215, 70, 255), 2,
                                              ((0, -58, -34), (1, -40, -34), (1, -40, -20)), 0, (0, 0), 0)],
                                      name=f"Image mark {number:04d}", parent_id=item)
                if number % 100 == 0:
                    board.update(item, opacity=0.85, locked=True)
            board.reorder(board.ordered_children(group))
        board.save(directory / "ordered.pur")
        return board
    except Exception:
        board.close()
        raise


def verify_numbered_board(board):
    items = board.items
    images = [item for item in items if item.kind == "image"]
    groups = [item for item in items if item.kind == "group"]
    by_name = {item.name: item for item in items}
    if len(images) != 1000 or len(groups) != 10 or {item.name for item in images} != {f"Image {n:04d}" for n in range(1, 1001)}:
        raise AssertionError("Expected exactly 1000 numbered images in ten groups")
    counts = []
    for index in range(10):
        first, last = index*100+1, (index+1)*100
        group = by_name[f"Group {index+1:02d}: {first:04d}-{last:04d}"]
        children = [item for item in images if item.parent_id == group.id]
        if {item.name for item in children} != {f"Image {n:04d}" for n in range(first, last+1)}:
            raise AssertionError("Group membership changed")
        for number in range(first, last+1):
            image = by_name[f"Image {number:04d}"]
            slot = number-first
            if (image.x, image.y) != (70+slot%10*160, 205+slot//10*150):
                raise AssertionError("Image position changed")
            caption = by_name[f"Caption {number:04d}"]
            if caption.parent_id != image.id or f">{number} · Reference {number}</p>" not in caption.text or (caption.x, caption.y) != (0, 70):
                raise AssertionError("Caption detached, changed or misplaced")
        counts.append(len(children))
    unique = {hashlib.sha256(row[0]).hexdigest() for row in board._conn.execute("SELECT data FROM images")}
    if len(unique) != 1000:
        raise AssertionError("Expected 1000 distinct embedded resources")
    return dict(images=1000, unique_assets=1000, captions=1000, group_counts=counts,
                drawings=sum(item.kind == "drawing" for item in items),
                notes=sum(item.kind == "note" for item in items), items=len(items))


def run_scenario(directory, seed=20261014):
    directory = Path(directory)
    started = time.monotonic()
    with build_numbered_board(directory) as board:
        counts = verify_numbered_board(board)
        original = hashlib.sha256((directory / "ordered.pur").read_bytes()).hexdigest()
        layout = capture_layout(board)
        atomic_write(directory / "layout.json", json.dumps(layout, indent=2).encode())
        shuffled = shuffle_images(board, seed=seed)
        board.save(directory / "shuffled.pur")
    with Board.load(directory / "shuffled.pur") as board:
        restore_layout(board, layout)
        if capture_layout(board) != layout:
            raise AssertionError("Layout did not restore exactly")
        verify_numbered_board(board)
        board.save(directory / "restored.pur")
    unchanged = hashlib.sha256((directory / "ordered.pur").read_bytes()).hexdigest() == original
    report = dict(**counts, **{key: value for key, value in shuffled.items() if key != "images"},
                  exact_layout_restored=True, content_unchanged=True,
                  original_file_unchanged=unchanged, original_sha256=original,
                  seconds=round(time.monotonic()-started, 3))
    atomic_write(directory / "result.json", json.dumps(report, indent=2).encode())
    return report
