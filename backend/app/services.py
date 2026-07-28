"""Application services that turn repository state into usable responses."""

from __future__ import annotations

from .api_models import (
    ScenarioListResponse,
    ScenarioOperationalSummary,
    ScenarioRecord,
    ScenarioSummary,
)
from .demo_export import SAFETY_NOTICE
from .models import ResourceStatus, Scenario
from .repository import InMemoryScenarioRepository, ScenarioStateSnapshot


class ScenarioService:
    def __init__(self, repository: InMemoryScenarioRepository) -> None:
        self.repository = repository

    def import_scenario(self, scenario: Scenario) -> ScenarioRecord:
        created = self.repository.create_scenario(scenario)
        return self.get_scenario(created.scenario_id)

    def list_scenarios(self, offset: int, limit: int) -> ScenarioListResponse:
        scenario_ids = self.repository.list_scenario_ids()
        selected_ids = scenario_ids[offset : offset + limit]
        items = [
            self._build_summary(self.repository.get_state_snapshot(scenario_id))
            for scenario_id in selected_ids
        ]
        return ScenarioListResponse(
            items=items,
            total=len(scenario_ids),
            offset=offset,
            limit=limit,
        )

    def get_scenario(self, scenario_id: str, version: int | None = None) -> ScenarioRecord:
        snapshot = self.repository.get_state_snapshot(scenario_id, version)
        return ScenarioRecord(
            summary=self._build_summary(snapshot),
            scenario=snapshot.scenario,
            current_version=snapshot.current_version,
            selected_version=snapshot.scenario.version,
            is_current_version=snapshot.scenario.version == snapshot.current_version,
            available_versions=list(snapshot.available_versions),
            pending_event_ids=list(snapshot.pending_event_ids),
            applied_event_ids=list(snapshot.applied_event_ids),
            safety_notice=SAFETY_NOTICE,
        )

    def _build_summary(self, snapshot: ScenarioStateSnapshot) -> ScenarioSummary:
        scenario = snapshot.scenario
        available_resources = [
            resource
            for resource in scenario.resources
            if resource.status is ResourceStatus.AVAILABLE
        ]
        available_types = {resource.resource_type for resource in available_resources}
        required_types = {task.required_resource_type for task in scenario.tasks}

        warnings: list[str] = []
        if not scenario.flights:
            warnings.append("场景没有航班，无法形成航班保障闭环")
        if not scenario.tasks:
            warnings.append("场景没有保障任务，请先补充任务")
        if not scenario.resources:
            warnings.append("场景没有保障资源，请先补充资源")
        elif not available_resources:
            warnings.append("场景资源均不可用，请先恢复至少一项资源")
        missing_types = sorted(resource_type.value for resource_type in required_types - available_types)
        if missing_types:
            warnings.append(f"缺少可用任务资源类型：{', '.join(missing_types)}")

        ready_for_planning = bool(scenario.tasks and required_types & available_types)
        if not ready_for_planning:
            recommended_action = "根据警告补全任务或可用资源后再规划"
        elif snapshot.pending_event_ids:
            recommended_action = "先生成当前版本 FIFO 基线，再选择待处理事件进行重规划"
        else:
            recommended_action = "生成 FIFO 与 CP-SAT 方案并比较关键指标"

        return ScenarioSummary(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            version=scenario.version,
            run_mode=scenario.run_mode,
            data_classification=scenario.data_classification,
            operational=ScenarioOperationalSummary(
                flight_count=len(scenario.flights),
                task_count=len(scenario.tasks),
                resource_count=len(scenario.resources),
                zone_count=len(scenario.zones),
                pending_event_count=len(snapshot.pending_event_ids),
                applied_event_count=len(snapshot.applied_event_ids),
                ready_for_planning=ready_for_planning,
                warnings=warnings,
                recommended_action=recommended_action,
            ),
        )
