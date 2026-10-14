"""Build the conformance suite in conformance/.

    python tools/build_conformance.py [--native]

For every case this writes `board.pur` and `expected.json` (see
pur_adapter.conformance.describe). With --native, an installed PureRef also
opens and resaves each board; the resaved file is kept as
`native-resaved.pur` and must describe exactly the same scene.
"""

import argparse
import io
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw
from pur_adapter import Board, qt
from pur_adapter.conformance import describe
from pur_adapter.container import pack_container
from pur_adapter.drawings import ARROW, DASHED, SOLID, Stroke
from pur_adapter import pureref_app

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "conformance"


def test_card(folder, name, size, color, label):
    """A small picture whose corners differ, so flips and crops are visible."""
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] // 3, size[1] // 3), fill=(255, 255, 255))
    draw.text((4, size[1] - 14), label, fill=(0, 0, 0))
    path = folder / name
    image.save(path)
    return path


def image_options(folder):
    board = Board()
    plain = board.add_image(test_card(folder, "plain.png", (120, 80), (200, 60, 60), "plain"))
    cropped = board.add_image(test_card(folder, "crop.png", (120, 80), (60, 160, 60), "crop"),
                              x=150)
    board.configure_image(cropped, crop=(10, 5, 70, 50), flip_horizontal=True)
    filtered = board.add_image(test_card(folder, "filter.png", (120, 80), (60, 60, 200), "gray"),
                               x=300)
    board.configure_image(filtered, grayscale=True, bilinear=False, flip_vertical=True)
    frames = [Image.new("RGB", (60, 60), color) for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(folder / "anim.gif", save_all=True, append_images=frames[1:], duration=200)
    animated = board.add_image(folder / "anim.gif", x=450)
    board.configure_image(animated, frame=2, speed=0.5)
    board.update(plain, opacity=0.5, locked=True)
    return board


def groups_and_notes(folder):
    board = Board()
    outer = board.add_group("Внешняя група · Outer", color="#ff2a3140", locked_mode=False)
    inner = board.add_group("Inner", x=40, y=30, parent_id=outer, color="#ff401a1a")
    image = board.add_image(test_card(folder, "ref.png", (100, 70), (180, 120, 40), "ref"),
                            parent_id=inner, name="Референс 🏛️")
    board.update(image, comment="Перевірити світло / check lighting")
    caption = board.add_note("<p>Caption <b>bold</b> &amp; <i>italic</i></p>", y=60,
                             parent_id=image)
    board.style_note(caption, background_color="#ff102030", text_color="#ffeeeeee",
                     width=160, height=40)
    loose = board.add_note("Plain note", x=300, parent_id=outer)
    board.reorder([loose, inner])  # sibling order differs from creation order
    board.update(outer, z=5)
    return board


def pen_styles(folder):
    board = Board()
    k = 0.5522847498 * 30  # cubic approximation of a circle of radius 30
    circle = ((0, 30, 0), (2, 30, k), (3, k, 30), (3, 0, 30), (2, -k, 30), (3, -30, k),
              (3, -30, 0), (2, -30, -k), (3, -k, -30), (3, 0, -30), (2, k, -30),
              (3, 30, -k), (3, 30, 0))
    strokes = [
        Stroke((240, 80, 80, 255), 3, ((0, 0, 0), (1, 120, 0)), 0, (0, 0), SOLID),
        Stroke((80, 200, 120, 200), 2, ((0, 0, 30), (1, 120, 30)), 0, (0, 0), DASHED),
        Stroke((80, 140, 240, 255), 4, ((0, 0, 60), (1, 120, 90)), 0, (0, 0), ARROW),
        Stroke((250, 220, 60, 255), 2, circle, 0, (0, 0), SOLID),
    ]
    board.add_drawing(strokes, name="Pens")
    return board


def transforms(folder):
    board = Board()
    card = test_card(folder, "t.png", (80, 60), (120, 120, 120), "t")
    rotated = board.add_image(card, name="rotated")
    board.update(rotated, rotation=30, scale=1.5)
    sheared = board.add_image(card, name="sheared", x=200)
    board.update(sheared, matrix=[1, 0.25, 0.4, 1.2])
    mirrored = board.add_image(card, name="mirrored", x=400)
    board.update(mirrored, matrix=[-1, 0, 0, 1])
    perspective = board.add_image(card, name="perspective", x=600)
    matrix = board.transform_matrix(perspective)
    matrix[qt.PERSPECTIVE_X] = 0.0005
    with board._conn:
        board._conn.execute("UPDATE items SET transform=? WHERE id=?",
                            (qt.transform(matrix), perspective))
    return board


def exif_photo(folder):
    photo = Image.new("RGB", (90, 30), (200, 90, 40))
    photo.paste((40, 40, 220), (0, 0, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # shown rotated 90 degrees clockwise
    photo.save(folder / "phone.jpg", exif=exif, quality=90)
    board = Board()
    board.add_image(folder / "phone.jpg", name="phone photo")
    return board


GENERATED = {
    "image-options": image_options,
    "groups-and-notes": groups_and_notes,
    "pen-styles": pen_styles,
    "transforms": transforms,
    "exif-photo": exif_photo,
}
NATIVE_FIXTURES = {
    "native-1.10.4": "native-1.10.4.pur",
    "native-1.11.1": "native-1.11.1.pur",
    "native-2.0.3": "native-2.0.3.pur",
    "native-2.1.3": "native-2.1.3.pur",
    "native-drawing-2.1.3": "native-drawing-2.1.3.pur",
}


def invalid_cases(folder):
    """Damaged or unsupported files and the error each must produce."""
    good = (ROOT / "fixtures/native-2.1.3.pur").read_bytes()
    cases = {}
    cases["truncated.pur"] = (good[: len(good) // 2], "InvalidPurFile")
    flipped = bytearray(good)
    flipped[-100] ^= 0x01
    cases["bad-checksum.pur"] = (bytes(flipped), "InvalidPurFile")
    future = b"\0\0\0\x06" + "2.2".encode("utf-16-be") + good[10:]
    cases["future-format.pur"] = (future, "UnsupportedFormat")
    cases["not-a-board.pur"] = (b"\x89PNG\r\n\x1a\n" + bytes(64), "UnsupportedFormat")
    with Board() as board:
        board._conn.execute("CREATE TRIGGER t AFTER UPDATE ON items BEGIN SELECT 1; END")
        cases["sqlite-trigger.pur"] = (pack_container(board._conn, "2.1.3", board._thumbnail),
                                       "UnsupportedFormat")
    legacy = bytearray((ROOT / "fixtures/native-1.11.1.pur").read_bytes())
    legacy[60] = ord("0") if legacy[60] != ord("0") else ord("1")
    cases["legacy-bad-checksum.pur"] = (bytes(legacy), "InvalidPurFile")
    return cases


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def comparable(description):
    return json.loads(json.dumps(description))


def main():
    options = argparse.ArgumentParser()
    options.add_argument("--native", action="store_true",
                         help="also resave every case with the installed PureRef")
    args = options.parse_args()
    if SUITE.exists():
        shutil.rmtree(SUITE / "cases", ignore_errors=True)
        shutil.rmtree(SUITE / "invalid", ignore_errors=True)
    (SUITE / "cases").mkdir(parents=True, exist_ok=True)
    (SUITE / "invalid").mkdir(parents=True, exist_ok=True)
    native_results = {}
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        for name, build in GENERATED.items():
            case = SUITE / "cases" / name
            case.mkdir()
            with build(scratch) as board:
                board.save(case / "board.pur")
        for name, fixture in NATIVE_FIXTURES.items():
            case = SUITE / "cases" / name
            case.mkdir()
            shutil.copyfile(ROOT / "fixtures" / fixture, case / "board.pur")
        for case in sorted((SUITE / "cases").iterdir()):
            with Board.load(case / "board.pur") as board:
                expected = describe(board)
            write_json(case / "expected.json", expected)
            if args.native and case.name in GENERATED:
                log = pureref_app.resave(case / "board.pur", case / "native-resaved.pur")
                with Board.load(case / "native-resaved.pur") as resaved:
                    native = comparable(describe(resaved))
                # PureRef renumbers z (keeping the drawing order) and rewrites
                # sort keys on save; everything else must survive unchanged.
                differences, renumbered = [], set()
                ours = comparable(expected)
                if native["drawing_order"] != ours["drawing_order"]:
                    differences.append("drawing_order")
                for before, after in zip(ours["items"], native["items"]):
                    for key in before:
                        if before[key] == after.get(key):
                            continue
                        if key in ("z", "order_index"):
                            renumbered.add(key)
                        else:
                            differences.append(f"item {before['id']}: {key}")
                if len(ours["items"]) != len(native["items"]):
                    differences.append("item count")
                native_results[case.name] = {
                    "pureref": "2.1.3",
                    "warnings": pureref_app.warnings_in(log),
                    "scene_unchanged_by_native_resave": not differences,
                    "differences": differences,
                    "renumbered_by_pureref": sorted(renumbered),
                }
    expected_errors = {}
    for name, (data, error) in invalid_cases(SUITE).items():
        (SUITE / "invalid" / name).write_bytes(data)
        expected_errors[name] = error
    write_json(SUITE / "invalid" / "expected-errors.json", expected_errors)
    if native_results:
        write_json(SUITE / "native-resave-results.json", native_results)
    print(json.dumps(native_results or {"cases": len(list((SUITE / 'cases').iterdir()))},
                     indent=2, ensure_ascii=False))
    return 0 if all(result["scene_unchanged_by_native_resave"]
                    for result in native_results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
