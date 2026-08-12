import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { SpatialQuestionResult } from "./RuntimeSpatialAssistant";
import type { RuntimeSpatialView, SpatialQuestionResponse } from "./types";


const view = {
  scenario_id: "SCN-TEST",
  scenario_version: 1,
  layout: {
    layout_id: "LAYOUT-TEST",
    layout_version: 1,
    scenario_id: "SCN-TEST",
    scenario_version: 1,
    projection: "normalized_cartesian",
    canvas: { width: 1000, height: 620 },
    asset: {},
    zones: [],
    paths: [],
    safety_notice: "safe",
  },
  overlay: {
    session_id: "RUN-TEST",
    revision: 4,
    simulation_time: "2026-08-01T08:04:00+08:00",
    layout_id: "LAYOUT-TEST",
    task_routes: [],
    resource_markers: [],
    event_markers: [],
  },
  coverage: {
    source_task_ids: ["TASK-004"],
    projected_active_task_ids: ["TASK-004"],
    changed_candidate_task_ids: [],
    projected_candidate_task_ids: [],
    source_resource_ids: ["WC-01"],
    projected_resource_ids: ["WC-01"],
    source_event_ids: ["EVT-001"],
    projected_event_ids: ["EVT-001"],
    complete: true,
  },
  facts: [],
  requires_human_confirmation: true,
  modifies_runtime: false,
  safety_notice: "safe",
} as unknown as RuntimeSpatialView;

const response = {
  question_id: "SPATIAL-Q-0123456789ABCDEF",
  basis: {
    session_id: "RUN-TEST",
    revision: 4,
    simulation_time: "2026-08-01T08:04:00+08:00",
    layout_id: "LAYOUT-TEST",
  },
  question: "task4现在在哪里？",
  selection: { task_id: "TASK-004", resource_id: null, event_id: null },
  trace: {
    source: "language_model",
    provider_attempted: true,
    model_label: "deepseek-test",
    fallback_reason: null,
  },
  answer: {
    status: "answered",
    statement: "TASK-004 从中转服务台前往东侧匿名登机口，由 WC-01 执行。",
    fact_ids: ["SPATIAL-FACT-TASK-004-ACTIVE"],
    matched_entity_ids: ["TASK-004"],
  },
  focus: {
    task_ids: ["TASK-004"],
    resource_ids: ["WC-01"],
    event_ids: [],
    zone_ids: ["TRANSFER-DESK", "GATE-E01"],
  },
  facts: [{
    fact_id: "SPATIAL-FACT-TASK-004-ACTIVE",
    category: "task",
    claim: "任务 TASK-004 当前路线从 TRANSFER-DESK 到 GATE-E01，由资源 WC-01 执行。",
    entity_ids: ["TASK-004", "WC-01", "TRANSFER-DESK", "GATE-E01"],
  }],
  unresolved_questions: ["是否采用候选仍需人工确认。"],
  requires_human_confirmation: true,
  modifies_runtime: false,
  safety_notice: "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。",
} as SpatialQuestionResponse;


describe("SpatialQuestionResult", () => {
  it("renders a question-specific answer, current citations and map focus objects", () => {
    const markup = renderToStaticMarkup(
      <SpatialQuestionResult
        response={response}
        view={view}
        onSelectTask={vi.fn()}
        onSelectResource={vi.fn()}
        onSelectEvent={vi.fn()}
      />,
    );

    expect(markup).toContain("task4现在在哪里");
    expect(markup).toContain("TASK-004 从中转服务台前往东侧匿名登机口");
    expect(markup).toContain("SPATIAL-FACT-TASK-004-ACTIVE");
    expect(markup).toContain("地图已聚焦");
    expect(markup).toContain("WC-01");
    expect(markup).toContain("GATE-E01");
    expect(markup).toContain("模型辅助回答 · deepseek-test");
    expect(markup).toContain("是否采用候选仍需人工确认");
    expect(markup).toContain("不构成真实机场运行");
  });
});
