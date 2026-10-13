"""Run: python examples/api_demo.py demo-output"""
import argparse
from pathlib import Path
from pur_adapter import Board
from pur_adapter.compatibility import export_legacy

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
reference = Path(__file__).parent / "assets" / "reference.png"

with Board() as board:
    image_id = board.add_image(reference, x=100, y=100, name="Lighting reference")
    board.add_note("Check indirect lighting", x=300, y=80)
    board.save(args.output / "created.pur")

with Board.load(args.output / "created.pur") as board:
    board.update(image_id, x=160, rotation=25, scale=0.75)
    board.save(args.output / "edited.pur")
    report = export_legacy(board, args.output / "compatible.pur")
    print(report)
