"""Finite deterministic planning objectives exposed by M5."""

from __future__ import annotations

from enum import Enum


class PlanningObjectiveProfile(str, Enum):
    BALANCED = "balanced"
    CRITICAL_FIRST = "critical_first"
    MINIMUM_WAIT = "minimum_wait"
    MINIMUM_CHANGE = "minimum_change"


OBJECTIVE_DISPLAY_NAMES = {
    PlanningObjectiveProfile.BALANCED: "均衡保障",
    PlanningObjectiveProfile.CRITICAL_FIRST: "关键任务优先",
    PlanningObjectiveProfile.MINIMUM_WAIT: "最小等待",
    PlanningObjectiveProfile.MINIMUM_CHANGE: "最小变更",
}


def cp_sat_algorithm_name(profile: PlanningObjectiveProfile) -> str:
    if profile is PlanningObjectiveProfile.BALANCED:
        return "cp_sat_priority_v1"
    return f"cp_sat_{profile.value}_v1"
