from __future__ import annotations

from pathlib import Path

from backend.app.demo_export import build_demo_payload
from backend.app.scenario_loader import load_scenario


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "scenarios" / "terminal-disturbance-demo.json"


def test_demo_payload_contains_fifo_and_optimized_views() -> None:
    payload = build_demo_payload(load_scenario(SCENARIO_PATH))

    assert payload["project"]["data_classification"] == "synthetic"
    assert "仅供教学仿真" in payload["project"]["safety_notice"]
    assert payload["views"]["baseline"]["plan"]["status"] == "partial"
    assert payload["views"]["after_events_fifo"]["plan"]["status"] == "partial"
    assert payload["views"]["optimized"]["plan"]["status"] == "executable"
    assert payload["views"]["baseline"]["plan"]["violations"] == []
    assert payload["views"]["after_events_fifo"]["plan"]["violations"] == []
    assert payload["views"]["optimized"]["plan"]["violations"] == []
    assert payload["views"]["baseline"]["plan"]["metrics"]["critical_task_completion_rate_pct"] == 50
    assert payload["views"]["optimized"]["plan"]["metrics"]["critical_task_completion_rate_pct"] == 100
    assert len(payload["events"]) == 2
    assert len(payload["changes"]) == 6
