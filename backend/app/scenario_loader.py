"""JSON loading boundary for simulation scenarios."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from pydantic import ValidationError

from .models import Scenario


class ScenarioLoadError(ValueError):
    """Raised when a scenario file cannot be parsed or validated."""


def load_scenario(path: str | Path) -> Scenario:
    """Load a UTF-8 JSON scenario and return a validated model."""

    scenario_path = Path(path)
    try:
        raw_text = scenario_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioLoadError(f"cannot read scenario {scenario_path}: {exc}") from exc

    try:
        payload = json.loads(raw_text)
    except JSONDecodeError as exc:
        raise ScenarioLoadError(
            f"invalid JSON in {scenario_path} at line {exc.lineno}, column {exc.colno}"
        ) from exc

    try:
        return Scenario.model_validate(payload)
    except ValidationError as exc:
        raise ScenarioLoadError(f"invalid scenario {scenario_path}: {exc}") from exc

