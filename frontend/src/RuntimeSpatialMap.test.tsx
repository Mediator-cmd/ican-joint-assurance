import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import RuntimeSpatialMap from "./RuntimeSpatialMap";
import { spatialRoutePolylinePoints } from "./spatial";
import type { RuntimeSpatialView, SpatialLayout } from "./types";


const layout = {
  layout_id: "LAYOUT-TEST",
  layout_version: 1,
  scenario_id: "SCN-TEST",
  scenario_version: 1,
  projection: "normalized_cartesian",
  canvas: { width: 1000, height: 620 },
  asset: {
    kind: "svg",
    source_class: "original_local",
    public_path: `/assets/anonymous-hub-layout.svg?v=${"0".repeat(64)}`,
    license_id: "project-original",
    integrity_sha256: "0".repeat(64),
    safety_classification: "anonymous_training_simulation",
  },
  zones: [
    { zone_id: "A", label: "区域 A", floor: "L1", anchor: { x: 0.1, y: 0.2 }, shape: [] },
    { zone_id: "B", label: "区域 B", floor: "L1", anchor: { x: 0.9, y: 0.8 }, shape: [] },
  ],
  paths: [{
    path_id: "PATH-A-B",
    from_zone_id: "A",
    to_zone_id: "B",
    points: [{ x: 0.1, y: 0.2 }, { x: 0.9, y: 0.8 }],
    direction: "bidirectional",
    accessible: true,
  }],
  safety_notice: "safe",
} as SpatialLayout;

const view = {
  scenario_id: "SCN-TEST",
  scenario_version: 1,
  layout,
  overlay: {
    session_id: "RUN-TEST",
    revision: 4,
    simulation_time: "2026-08-01T08:04:00+08:00",
    layout_id: "LAYOUT-TEST",
    task_routes: [
      {
        route_id: "ROUTE-ACTIVE-TASK-001",
        task_id: "TASK-001",
        route_kind: "active",
        plan_id: "PLAN-ACTIVE",
        task_status: "affected",
        assignment_state: "assigned",
        change_kind: "current",
        resource_id: "WC-01",
        origin_zone_id: "A",
        destination_zone_id: "B",
        legs: [{ path_id: "PATH-A-B", from_zone_id: "A", to_zone_id: "B", traversal: "forward" }],
        demand_only: false,
      },
      {
        route_id: "ROUTE-CANDIDATE-TASK-001",
        task_id: "TASK-001",
        route_kind: "candidate",
        plan_id: "PLAN-CANDIDATE",
        task_status: "affected",
        assignment_state: "assigned",
        change_kind: "resource_changed",
        resource_id: "WC-02",
        origin_zone_id: "A",
        destination_zone_id: "B",
        legs: [{ path_id: "PATH-A-B", from_zone_id: "A", to_zone_id: "B", traversal: "forward" }],
        demand_only: false,
      },
    ],
    resource_markers: [{
      resource_id: "WC-01",
      status: "moving",
      current_task_id: "TASK-001",
      next_task_id: null,
      from_zone_id: "A",
      to_zone_id: "B",
      progress_pct: 50,
      position: { x: 0.5, y: 0.5 },
      route_legs: [{ path_id: "PATH-A-B", from_zone_id: "A", to_zone_id: "B", traversal: "forward" }],
    }],
    event_markers: [{
      event_id: "EVT-001",
      event_type: "gate_change",
      flight_id: "FL-001",
      status: "awaiting_confirmation",
      detail: "匿名登机口调整",
      primary_zone_id: "B",
      position: { x: 0.9, y: 0.8 },
      previous_zone_id: "A",
      new_zone_id: "B",
      route_legs: [{ path_id: "PATH-A-B", from_zone_id: "A", to_zone_id: "B", traversal: "forward" }],
    }],
  },
  coverage: {
    source_task_ids: ["TASK-001"],
    projected_active_task_ids: ["TASK-001"],
    changed_candidate_task_ids: ["TASK-001"],
    projected_candidate_task_ids: ["TASK-001"],
    source_resource_ids: ["WC-01"],
    projected_resource_ids: ["WC-01"],
    source_event_ids: ["EVT-001"],
    projected_event_ids: ["EVT-001"],
    complete: true,
  },
  facts: [],
  requires_human_confirmation: true,
  modifies_runtime: false,
  safety_notice: "仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。",
} as RuntimeSpatialView;

describe("RuntimeSpatialMap", () => {
  it("renders one active route, one changed candidate, one resource and one event", () => {
    const markup = renderToStaticMarkup(
      <RuntimeSpatialMap
        view={view}
        status="ready"
        error={null}
        expectedRevision={4}
        selectedTaskId="TASK-001"
        selectedResourceId="WC-01"
        selectedEventId="EVT-001"
        onSelectTask={vi.fn()}
        onSelectResource={vi.fn()}
        onSelectEvent={vi.fn()}
        onOpenTask={vi.fn()}
        onOpenResource={vi.fn()}
        onOpenEvent={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(markup.match(/data-route-id=/g)).toHaveLength(2);
    expect(markup.match(/data-resource-id=/g)).toHaveLength(1);
    expect(markup.match(/data-event-id=/g)).toHaveLength(1);
    expect(markup).toContain(`/assets/anonymous-hub-layout.svg?v=${"0".repeat(64)}`);
    expect(markup).toContain("一一对应完整");
    expect(markup).toContain("当前方案始终为实线");
    expect(markup).toContain("候选只显示真实变化");
    expect(markup).toContain("采用前不替换当前路线");
  });

  it("reverses only registered geometry when a backend leg is reversed", () => {
    expect(spatialRoutePolylinePoints(layout, [{
      path_id: "PATH-A-B",
      from_zone_id: "B",
      to_zone_id: "A",
      traversal: "reverse",
    }])).toBe("900.00,496.00 100.00,124.00");
  });

  it("does not render a stale spatial view for another revision", () => {
    const markup = renderToStaticMarkup(
      <RuntimeSpatialMap
        view={view}
        status="loading"
        error={null}
        expectedRevision={5}
        selectedTaskId=""
        selectedResourceId=""
        selectedEventId=""
        onSelectTask={vi.fn()}
        onSelectResource={vi.fn()}
        onSelectEvent={vi.fn()}
        onOpenTask={vi.fn()}
        onOpenResource={vi.fn()}
        onOpenEvent={vi.fn()}
        onRetry={vi.fn()}
      />,
    );

    expect(markup).toContain("正在绑定权威空间事实");
    expect(markup).not.toContain("data-route-id");
  });

  it("adds static AI focus without replacing current or candidate route semantics", () => {
    const markup = renderToStaticMarkup(
      <RuntimeSpatialMap
        view={view}
        status="ready"
        error={null}
        expectedRevision={4}
        selectedTaskId=""
        selectedResourceId=""
        selectedEventId=""
        onSelectTask={vi.fn()}
        onSelectResource={vi.fn()}
        onSelectEvent={vi.fn()}
        onOpenTask={vi.fn()}
        onOpenResource={vi.fn()}
        onOpenEvent={vi.fn()}
        onRetry={vi.fn()}
        assistantFocus={{
          task_ids: ["TASK-001"],
          resource_ids: ["WC-01"],
          event_ids: ["EVT-001"],
          zone_ids: ["B"],
        }}
      />,
    );

    expect(markup).toContain("spatial-task-route active attention assistant-focus");
    expect(markup).toContain("spatial-task-route candidate assistant-focus");
    expect(markup).toContain("spatial-resource-marker moving assistant-focus");
    expect(markup).toContain("spatial-event-marker awaiting_confirmation assistant-focus");
    expect(markup).toContain("spatial-zone-label assistant-focus");
    expect(markup).toContain("当前方案始终为实线");
    expect(markup).toContain("采用前不替换当前路线");
  });
});
