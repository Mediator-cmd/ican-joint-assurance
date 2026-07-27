"""Validate a scenario JSON file and print a compact summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.scenario_loader import ScenarioLoadError, load_scenario  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Validate a 联保智调 simulation scenario")
    parser.add_argument("path", type=Path, help="path to a UTF-8 JSON scenario")
    args = parser.parse_args()

    try:
        scenario = load_scenario(args.path)
    except ScenarioLoadError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    summary = {
        "scenario_id": scenario.scenario_id,
        "name": scenario.name,
        "run_mode": scenario.run_mode.value,
        "data_classification": scenario.data_classification.value,
        "zones": len(scenario.zones),
        "flights": len(scenario.flights),
        "events": len(scenario.events),
        "tasks": len(scenario.tasks),
        "resources": len(scenario.resources),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
