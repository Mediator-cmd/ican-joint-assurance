from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.models import Scenario
from backend.app.scenario_loader import load_scenario
from backend.app.spatial_layout import SpatialLayoutNotAvailableError, build_spatial_layout
from backend.app.spatial_services import find_spatial_route
from backend.app.spatial_models import SpatialAsset, SpatialLayout, SpatialPath


SCENARIO_PATH = "data/scenarios/terminal-disturbance-demo.json"


def test_anonymous_layout_is_local_hashed_and_graph_complete() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    layout = build_spatial_layout(scenario)

    assert layout.asset.public_path == "/assets/anonymous-hub-layout.svg"
    assert layout.asset.source_class == "original_local"
    assert len(layout.asset.integrity_sha256) == 64
    assert sha256(Path("frontend/public/assets/anonymous-hub-layout.svg").read_bytes()).hexdigest() == (
        layout.asset.integrity_sha256
    )
    assert {zone.zone_id for zone in layout.zones} == {
        "TRANSFER-DESK",
        "GATE-E01",
        "GATE-W03",
    }
    assert [leg.path_id for leg in find_spatial_route(layout, "GATE-W03", "GATE-E01")] == [
        "PATH-TRANSFER-W03",
        "PATH-TRANSFER-E01",
    ]


def test_layout_rejects_unknown_endpoints_and_remote_assets() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    layout = build_spatial_layout(scenario)
    invalid_path = SpatialPath(
        path_id="PATH-BAD",
        from_zone_id="UNKNOWN",
        to_zone_id="GATE-E01",
        points=layout.paths[0].points,
    )
    with pytest.raises(ValidationError):
        SpatialLayout.model_validate(
            {
                **layout.model_dump(mode="python"),
                "paths": [*layout.paths, invalid_path],
            }
        )
    with pytest.raises(ValidationError):
        SpatialAsset(
            public_path="https://example.invalid/layout.svg",
            integrity_sha256=layout.asset.integrity_sha256,
        )


def test_layout_is_not_guessed_for_another_scenario() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    altered = Scenario.model_validate(
        scenario.model_copy(update={"scenario_id": "SCN-OTHER"}).model_dump(mode="python")
    )
    with pytest.raises(SpatialLayoutNotAvailableError, match="no spatial layout"):
        build_spatial_layout(altered)
