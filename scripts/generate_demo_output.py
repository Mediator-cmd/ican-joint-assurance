"""Generate the M2 dashboard payload from the validated simulation scenario."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.demo_export import build_demo_payload  # noqa: E402
from backend.app.scenario_loader import ScenarioLoadError, load_scenario  # noqa: E402


DEFAULT_SCENARIO = PROJECT_ROOT / "data" / "scenarios" / "terminal-disturbance-demo.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "frontend" / "public" / "demo-output.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Generate dashboard data from a simulation scenario")
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        scenario = load_scenario(args.scenario)
    except ScenarioLoadError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    payload = build_demo_payload(scenario)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    baseline_plan = payload["views"]["baseline"]["plan"]
    updated_plan = payload["views"]["after_events_fifo"]["plan"]
    optimized_plan = payload["views"]["optimized"]["plan"]
    print(
        f"generated {args.output}: "
        f"baseline={baseline_plan['status']}, after_events={updated_plan['status']}, "
        f"optimized={optimized_plan['status']}, changes={len(payload['changes'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
