import { describe, expect, it, vi } from "vitest";

import {
  createRuntimeEventDraft,
  createRuntimePlanExplanation,
  DemoPayloadLoadError,
  findOrCreateRuntimeSession,
  getRuntimeSpatialView,
  loadDemoPayload,
  postRuntimeAction,
  submitRuntimeEventDraft,
} from "./api";
import type { Fetcher } from "./api";
import type {
  DemoPayload,
  EventDraftResponse,
  RuntimeSessionSnapshot,
  RuntimeSpatialView,
} from "./types";

const demoPayload = {
  project: {},
  events: [],
  changes: [],
  views: {
    baseline: { scenario: {}, plan: {} },
    after_events_fifo: { scenario: {}, plan: {} },
    optimized: { scenario: {}, plan: {} },
  },
} as unknown as DemoPayload;

const runtimeDemoPayload = {
  ...demoPayload,
  views: {
    ...demoPayload.views,
    baseline: {
      scenario: {
        scenario_id: "SCN-DEMO",
        version: 1,
        tasks: [],
      },
      plan: {},
    },
  },
} as unknown as DemoPayload;

const runtimeSnapshot = {
  session_id: "RUN-DEMO",
  scenario_id: "SCN-DEMO",
  initial_scenario_version: 1,
  initial_plan_id: "PLAN-DEMO-V1-CP-SAT",
  active_plan_id: "PLAN-DEMO-V1-CP-SAT",
  candidate_plan_id: null,
  revision: 1,
  status: "ready",
  clock: {},
  tasks: [],
  resources: [],
  flights: [],
  events: [],
  collection_summary: {
    task_total: 0,
    task_active: 0,
    task_attention: 0,
    task_completed: 0,
    task_locked: 0,
    resource_total: 0,
    resource_active: 0,
    event_total: 0,
    event_open: 0,
    event_pending: 0,
    flight_total: 0,
  },
} as unknown as RuntimeSessionSnapshot;

const eventDraft = {
  draft_id: "DRAFT-0123456789ABCDEF",
  basis: {
    scenario_id: "SCN-DEMO",
    scenario_version: 1,
    reference_time: "2026-08-01T08:00:00+08:00",
    runtime_session_id: "RUN-DEMO",
    runtime_revision: 3,
  },
  status: "ready_for_review",
  trace: {
    source: "deterministic_rules",
    provider_attempted: false,
    model_label: null,
    fallback_reason: "model_not_configured",
  },
  event: {
    event_id: "EVT-NEW-DELAY",
    event_type: "delay",
    flight_id: "FL-SIM330",
    occurred_at: "2026-08-01T08:25:00+08:00",
    delay_minutes: 15,
    previous_gate_id: null,
    new_gate_id: null,
    note: null,
  },
  evidence: [{
    field: "flight_id",
    normalized_value: "FL-SIM330",
    origin: "user_text",
    source_quote: "SIM330",
  }],
  missing_fields: [],
  clarification_questions: [],
  objective_recommendation: null,
  warnings: [],
  requires_human_confirmation: true,
  applies_automatically: false,
  safety_notice: "safe",
} as EventDraftResponse;

const planExplanation = {
  explanation_id: "EXPL-0123456789ABCDEF",
  context: {
    scope: "runtime_plan",
    session_id: "RUN-DEMO",
    revision: 3,
    plan_id: "PLAN-CANDIDATE",
    baseline_plan_id: "PLAN-ACTIVE",
  },
  trace: {
    source: "deterministic_rules",
    provider_attempted: false,
    model_label: null,
    fallback_reason: null,
  },
  focus: "task_changes",
  question: null,
  question_answer: {
    status: "not_asked",
    statement: "未提出补充问题。",
    evidence_ids: [],
    matched_entity_ids: [],
  },
  summary: { statement: "候选方案覆盖全部任务。", evidence_ids: ["FACT-METRIC-1"] },
  tradeoffs: [{ statement: "当前指标用于说明覆盖取舍。", evidence_ids: ["FACT-METRIC-1"] }],
  task_changes: [{ statement: "候选方案安排数量已经变化。", evidence_ids: ["FACT-METRIC-1"] }],
  manual_handling: [{ statement: "人员需要复核候选任务书。", evidence_ids: ["FACT-METRIC-1"] }],
  recommended_next_step: { statement: "请人工复核后决定。", evidence_ids: ["FACT-METRIC-1"] },
  evidence: [{
    evidence_id: "FACT-METRIC-1",
    kind: "plan_metric",
    entity_ids: ["PLAN-CANDIDATE"],
    field: "assigned_tasks",
    value: "10",
    statement: "已安排 10 项任务。",
  }],
  unresolved_questions: [],
  requires_human_confirmation: true,
  modifies_plan: false,
  safety_notice: "safe",
};

const spatialView = {
  scenario_id: "SCN-DEMO",
  scenario_version: 1,
  layout: {
    layout_id: "LAYOUT-DEMO",
    scenario_id: "SCN-DEMO",
    scenario_version: 1,
    canvas: { width: 1000, height: 620 },
    asset: { public_path: "/assets/anonymous-hub-layout.svg" },
    zones: [{ zone_id: "ZONE-A", anchor: { x: 0.5, y: 0.5 } }],
    paths: [],
  },
  overlay: {
    session_id: "RUN-DEMO",
    revision: 3,
    simulation_time: "2026-08-01T08:00:00+08:00",
    layout_id: "LAYOUT-DEMO",
    task_routes: [],
    resource_markers: [],
    event_markers: [],
  },
  coverage: {
    source_task_ids: [],
    projected_active_task_ids: [],
    changed_candidate_task_ids: [],
    projected_candidate_task_ids: [],
    source_resource_ids: [],
    projected_resource_ids: [],
    source_event_ids: [],
    projected_event_ids: [],
    complete: true,
  },
  facts: [],
  requires_human_confirmation: true,
  modifies_runtime: false,
  safety_notice: "safe",
} as unknown as RuntimeSpatialView;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("loadDemoPayload", () => {
  it("returns API data without requesting the static fallback", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(demoPayload));

    const result = await loadDemoPayload({ fetcher, cacheBust: "api-success" });

    expect(result).toEqual({ payload: demoPayload, source: "api" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/demo");
  });

  it("returns static data when the API request fails", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse(demoPayload));

    const result = await loadDemoPayload({ fetcher, cacheBust: "fallback" });

    expect(result).toEqual({ payload: demoPayload, source: "static_fallback" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]?.[0]).toContain("demo-output.json?reload=fallback");
  });

  it("throws one safe error when both data sources fail", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockRejectedValueOnce(new Error("private network detail"))
      .mockResolvedValueOnce(jsonResponse({}, 404));

    const loading = loadDemoPayload({ fetcher, cacheBust: "double-failure" });

    await expect(loading).rejects.toMatchObject({
      name: "DemoPayloadLoadError",
      message: "数据服务暂不可用，本地演示数据也未能载入。请确认项目服务已启动后重试。",
      failures: [
        { source: "api", reason: "network_error" },
        { source: "static_fallback", reason: "http_error", status: 404 },
      ],
    });
    await expect(loading).rejects.not.toThrow("private network detail");
  });

  it("runs the complete chain again and recovers on retry", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse({}, 404))
      .mockResolvedValueOnce(jsonResponse(demoPayload));

    await expect(
      loadDemoPayload({ fetcher, cacheBust: "first-attempt" }),
    ).rejects.toBeInstanceOf(DemoPayloadLoadError);

    const retryResult = await loadDemoPayload({ fetcher, cacheBust: "retry" });

    expect(retryResult.source).toBe("api");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/demo",
      expect.stringContaining("demo-output.json?reload=first-attempt"),
      "/api/v1/demo",
    ]);
  });
});

describe("findOrCreateRuntimeSession", () => {
  it("finds the newest matching SQLite session without creating another", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({
        items: [{ plan_id: "PLAN-DEMO-V1-CP-SAT" }],
        total: 1,
        storage_scope: "process_memory",
      }))
      .mockResolvedValueOnce(jsonResponse({
        items: [{ session_id: "RUN-DEMO" }],
        total: 1,
        offset: 0,
        limit: 100,
        storage_scope: "sqlite",
        safety_notice: "safe",
      }))
      .mockResolvedValueOnce(jsonResponse(runtimeSnapshot));

    const result = await findOrCreateRuntimeSession(runtimeDemoPayload, { fetcher });

    expect(result.session_id).toBe("RUN-DEMO");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });

  it("creates one optimized plan and one session when neither exists", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, storage_scope: "process_memory" }))
      .mockResolvedValueOnce(jsonResponse({
        plan: { plan_id: "PLAN-DEMO-V1-CP-SAT" },
        guidance: {},
        storage_scope: "process_memory",
        safety_notice: "safe",
      }, 201))
      .mockResolvedValueOnce(jsonResponse({
        items: [],
        total: 0,
        offset: 0,
        limit: 100,
        storage_scope: "sqlite",
        safety_notice: "safe",
      }))
      .mockResolvedValueOnce(jsonResponse(runtimeSnapshot, 201));

    const result = await findOrCreateRuntimeSession(runtimeDemoPayload, { fetcher });

    expect(result.session_id).toBe("RUN-DEMO");
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(fetcher.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(fetcher.mock.calls[3]?.[1]?.method).toBe("POST");
    expect(fetcher.mock.calls[3]?.[1]?.body).toContain('"active_plan_id":"PLAN-DEMO-V1-CP-SAT"');
  });

  it("does not reuse an older session whose task or event catalog is stale", async () => {
    const expandedPayload = {
      ...runtimeDemoPayload,
      events: [{ event_id: "EVT-NEW" }],
      views: {
        ...runtimeDemoPayload.views,
        baseline: {
          ...runtimeDemoPayload.views.baseline,
          scenario: {
            scenario_id: "SCN-DEMO",
            version: 1,
            tasks: [{ task_id: "TASK-NEW" }],
          },
        },
      },
    } as unknown as DemoPayload;
    const expandedSnapshot = {
      ...runtimeSnapshot,
      session_id: "RUN-NEW",
      tasks: [{ task_id: "TASK-NEW" }],
      events: [{ event_id: "EVT-NEW" }],
    } as RuntimeSessionSnapshot;
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({
        items: [{ plan_id: "PLAN-DEMO-V1-CP-SAT" }],
        total: 1,
        storage_scope: "process_memory",
      }))
      .mockResolvedValueOnce(jsonResponse({
        items: [{ session_id: "RUN-OLD" }],
        total: 1,
        offset: 0,
        limit: 100,
        storage_scope: "sqlite",
        safety_notice: "safe",
      }))
      .mockResolvedValueOnce(jsonResponse({ ...runtimeSnapshot, session_id: "RUN-OLD" }))
      .mockResolvedValueOnce(jsonResponse(expandedSnapshot, 201));

    const result = await findOrCreateRuntimeSession(expandedPayload, { fetcher });

    expect(result.session_id).toBe("RUN-NEW");
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(fetcher.mock.calls[3]?.[1]?.method).toBe("POST");
  });
});

describe("runtime API errors", () => {
  it("binds the spatial read to the expected runtime revision", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(spatialView));

    const result = await getRuntimeSpatialView("RUN-DEMO", 3, { fetcher });

    expect(result.overlay.revision).toBe(3);
    expect(fetcher.mock.calls[0]?.[0]).toBe(
      "/api/v1/runtime-sessions/RUN-DEMO/spatial?expected_revision=3",
    );
  });

  it("rejects a spatial response whose one-to-one coverage is incomplete", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      ...spatialView,
      coverage: {
        ...spatialView.coverage,
        source_task_ids: ["TASK-MISSING"],
      },
    }));

    await expect(getRuntimeSpatialView("RUN-DEMO", 3, { fetcher }))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("returns the safe structured 409 error needed for refresh-before-retry", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      error: {
        code: "runtime_revision_conflict",
        message: "运行状态已更新到修订 4；请刷新当前会话后重试",
      },
    }, 409));

    const request = postRuntimeAction(
      "RUN-DEMO",
      "pause",
      { expected_revision: 3 },
      { fetcher },
    );

    await expect(request).rejects.toMatchObject({
      status: 409,
      code: "runtime_revision_conflict",
    });
  });

  it("rejects a runtime snapshot with a non-integer collection summary", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      ...runtimeSnapshot,
      collection_summary: {
        ...runtimeSnapshot.collection_summary,
        task_total: 0.5,
      },
    }));

    await expect(postRuntimeAction(
      "RUN-DEMO",
      "pause",
      { expected_revision: 3 },
      { fetcher },
    )).rejects.toMatchObject({ code: "invalid_response" });
  });
});

describe("runtime assistant API", () => {
  it("binds an event draft request to the current runtime revision", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(eventDraft));

    const result = await createRuntimeEventDraft(
      "RUN-DEMO",
      3,
      "SIM330 于 08:25 确认延误 15 分钟",
      "deterministic_only",
      { fetcher },
    );

    expect(result.draft_id).toBe("DRAFT-0123456789ABCDEF");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      context: { scope: "runtime", session_id: "RUN-DEMO", expected_revision: 3 },
      text: "SIM330 于 08:25 确认延误 15 分钟",
      assistance_mode: "deterministic_only",
    });
  });

  it("rejects an incomplete event draft response", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      ...eventDraft,
      basis: { ...eventDraft.basis, runtime_revision: null },
    }));

    await expect(createRuntimeEventDraft(
      "RUN-DEMO",
      3,
      "SIM330 延误",
      "auto",
      { fetcher },
    )).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("submits only a reviewed event with a confirmed planning objective", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(runtimeSnapshot));

    await submitRuntimeEventDraft(eventDraft, "minimum_change", { fetcher });

    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      scope: "runtime",
      draft: eventDraft,
      confirm_event: true,
      objective_profile: "minimum_change",
      confirm_objective: true,
    });
  });

  it("requests a candidate explanation against the active-plan baseline", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(planExplanation));

    const result = await createRuntimePlanExplanation(
      "RUN-DEMO",
      3,
      "PLAN-CANDIDATE",
      "PLAN-ACTIVE",
      "task_changes",
      "  ",
      "auto",
      { fetcher },
    );

    expect(result.explanation_id).toBe("EXPL-0123456789ABCDEF");
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      context: {
        scope: "runtime_plan",
        session_id: "RUN-DEMO",
        revision: 3,
        plan_id: "PLAN-CANDIDATE",
        baseline_plan_id: "PLAN-ACTIVE",
      },
      focus: "task_changes",
      assistance_mode: "auto",
    });
  });

  it("rejects explanation claims that cite absent authority facts", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      ...planExplanation,
      summary: { statement: "无依据结论", evidence_ids: ["FACT-MISSING"] },
    }));

    await expect(createRuntimePlanExplanation(
      "RUN-DEMO",
      3,
      "PLAN-CANDIDATE",
      "PLAN-ACTIVE",
      "summary",
      "",
      "deterministic_only",
      { fetcher },
    )).rejects.toMatchObject({ code: "invalid_response" });
  });
});
