"""Versioned local geometry for the anonymous teaching-simulation hub."""

from __future__ import annotations

from .models import Scenario
from .spatial_models import (
    NormalizedPoint,
    SpatialAsset,
    SpatialCanvas,
    SpatialLayout,
    SpatialPath,
    SpatialZone,
)


ANONYMOUS_HUB_SCENARIO_ID = "SCN-TERMINAL-DISTURBANCE-01"
ANONYMOUS_HUB_ZONE_IDS = {"TRANSFER-DESK", "GATE-E01", "GATE-W03"}
ANONYMOUS_HUB_ASSET_SHA256 = (
    "0512a7f8e9001e0c987519df948d37c8dfb85348f87672b64eafc207f638bd44"
)


class SpatialLayoutNotAvailableError(Exception):
    def __init__(self, scenario_id: str, scenario_version: int) -> None:
        super().__init__(f"no spatial layout for {scenario_id} version {scenario_version}")
        self.scenario_id = scenario_id
        self.scenario_version = scenario_version


def build_spatial_layout(scenario: Scenario) -> SpatialLayout:
    """Return geometry only for the explicitly supported anonymous scenario."""
    scenario_zone_ids = {zone.zone_id for zone in scenario.zones}
    if (
        scenario.scenario_id != ANONYMOUS_HUB_SCENARIO_ID
        or scenario_zone_ids != ANONYMOUS_HUB_ZONE_IDS
    ):
        raise SpatialLayoutNotAvailableError(scenario.scenario_id, scenario.version)

    labels = {zone.zone_id: zone.name for zone in scenario.zones}
    anchors = {
        "TRANSFER-DESK": NormalizedPoint(x=0.5, y=350 / 620),
        "GATE-W03": NormalizedPoint(x=0.176, y=194 / 620),
        "GATE-E01": NormalizedPoint(x=0.824, y=194 / 620),
    }
    zones = [
        SpatialZone(
            zone_id="TRANSFER-DESK",
            label=labels["TRANSFER-DESK"],
            floor="L1",
            anchor=anchors["TRANSFER-DESK"],
            shape=_rectangle(0.414, 312 / 620, 0.586, 396 / 620),
        ),
        SpatialZone(
            zone_id="GATE-W03",
            label=labels["GATE-W03"],
            floor="L1",
            anchor=anchors["GATE-W03"],
            shape=_rectangle(0.112, 150 / 620, 0.240, 238 / 620),
        ),
        SpatialZone(
            zone_id="GATE-E01",
            label=labels["GATE-E01"],
            floor="L1",
            anchor=anchors["GATE-E01"],
            shape=_rectangle(0.760, 150 / 620, 0.888, 238 / 620),
        ),
    ]
    paths = [
        SpatialPath(
            path_id="PATH-TRANSFER-W03",
            from_zone_id="TRANSFER-DESK",
            to_zone_id="GATE-W03",
            points=[
                anchors["TRANSFER-DESK"],
                NormalizedPoint(x=0.314, y=300 / 620),
                anchors["GATE-W03"],
            ],
        ),
        SpatialPath(
            path_id="PATH-TRANSFER-E01",
            from_zone_id="TRANSFER-DESK",
            to_zone_id="GATE-E01",
            points=[
                anchors["TRANSFER-DESK"],
                NormalizedPoint(x=0.686, y=300 / 620),
                anchors["GATE-E01"],
            ],
        ),
    ]
    return SpatialLayout(
        layout_id="LAYOUT-ANON-HUB-V1",
        layout_version=1,
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        canvas=SpatialCanvas(width=1000, height=620),
        asset=SpatialAsset(
            public_path="/assets/anonymous-hub-layout.svg",
            integrity_sha256=ANONYMOUS_HUB_ASSET_SHA256,
        ),
        zones=zones,
        paths=paths,
    )


def _rectangle(left: float, top: float, right: float, bottom: float) -> list[NormalizedPoint]:
    return [
        NormalizedPoint(x=left, y=top),
        NormalizedPoint(x=right, y=top),
        NormalizedPoint(x=right, y=bottom),
        NormalizedPoint(x=left, y=bottom),
    ]
