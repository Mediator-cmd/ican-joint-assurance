"""Run M6-3 offline benchmarks and write the strict JSON evidence artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.scale_benchmark import (  # noqa: E402
    DEFAULT_MEASURED_RUNS,
    DEFAULT_WARMUP_RUNS,
    run_scale_benchmark_suite,
)


DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "m6-benchmark-report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run paired FIFO and bounded planning benchmarks on frozen synthetic "
            "M6 scenarios."
        )
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=DEFAULT_WARMUP_RUNS,
        help="Warmups per planner and tier (default: 1; allowed: 0-10).",
    )
    parser.add_argument(
        "--measured-runs",
        type=int,
        default=DEFAULT_MEASURED_RUNS,
        help="Measured paired samples per tier (default: 5; allowed: 1-20).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON evidence path.",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    suite = run_scale_benchmark_suite(
        warmup_runs=args.warmup_runs,
        measured_runs=args.measured_runs,
    )
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        suite.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(f"{serialized}\n", encoding="utf-8", newline="\n")
    temporary_path.replace(output_path)

    print(f"Wrote {output_path}")
    for report in suite.reports:
        print(
            f"{report.profile.tier.value}: "
            f"bounded_p50={report.p50_seconds:.6f}s "
            f"bounded_p95={report.p95_seconds:.6f}s "
            f"bounded_max={report.max_seconds:.6f}s "
            f"target_met={str(report.target_met).lower()} "
            f"fallback_used={str(report.fallback_used).lower()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
