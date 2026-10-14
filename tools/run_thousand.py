"""Run from a checkout after installation, or with PYTHONPATH=src."""

import argparse
import json
from pur_adapter.stress import run_scenario

parser = argparse.ArgumentParser()
parser.add_argument(
    "output", help="New output directory; existing board files are never overwritten"
)
parser.add_argument("--seed", type=int, default=20261014)
args = parser.parse_args()
print(json.dumps(run_scenario(args.output, args.seed), indent=2))
