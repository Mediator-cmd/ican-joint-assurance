import { describe, expect, it } from "vitest";

import {
  compareRuntimePlans,
  reduceRuntimeStreamEvent,
  runtimeStatusCounts,
  shouldReplaceRuntimeSnapshot,
} from "./runtime";
import type { Assignment, Plan, RuntimeSessionSnapshot, RuntimeStreamEvent } from "./types";

const snapshot = {
  session_id: "RUN-DEMO",
  revision: 2,
  status: "running",
  clock: { simulation_time: "2026-08-01T08:00:00+08:00", speed: 1 },
  tasks: [
    { task_id: "TASK-1", status: "pending" },
    { task_id: "TASK-2", status: "in_service" },
  ],
  resources: [],
  flights: [],
  events: [],
} as unknown as RuntimeSessionSnapshot;

function streamEvent(
  sequence: number,
  payload: RuntimeStreamEvent["payload"],
  streamId = "STREAM-A",
): RuntimeStreamEvent {
  return {
    stream_id: streamId,
    sequence,
    session_id: "RUN-DEMO",
    revision: 2,
    emitted_at: "2026-08-01T00:00:00Z",
    payload,
    sse_id: `${streamId}:${sequence}`,
  };
}

describe("reduceRuntimeStreamEvent", () => {
  it("replaces the complete snapshot atomically", () => {
    const next = { ...snapshot, revision: 3, status: "paused" } as RuntimeSessionSnapshot;
    const result = reduceRuntimeStreamEvent(
      snapshot,
      null,
      streamEvent(1, { event_type: "runtime.snapshot", snapshot: next }),
    );

    expect(result.accepted).toBe(true);
    expect(result.snapshot).toBe(next);
    expect(result.cursor).toEqual({ streamId: "STREAM-A", sequence: 1 });
  });

  it("updates only the backend clock for a tick", () => {
    const clock = {
      ...snapshot.clock,
      simulation_time: "2026-08-01T08:00:01+08:00",
    };
    const result = reduceRuntimeStreamEvent(
      snapshot,
      null,
      streamEvent(1, { event_type: "runtime.tick", clock }),
    );

    expect(result.snapshot?.clock).toBe(clock);
    expect(result.snapshot?.revision).toBe(snapshot.revision);
    expect(result.snapshot?.tasks).toBe(snapshot.tasks);
  });

  it("rejects duplicate sequence values without changing the snapshot", () => {
    const result = reduceRuntimeStreamEvent(
      snapshot,
      { streamId: "STREAM-A", sequence: 4 },
      streamEvent(4, { event_type: "heartbeat", server_time: "2026-08-01T00:00:00Z" }),
    );

    expect(result).toEqual({
      accepted: false,
      snapshot,
      cursor: { streamId: "STREAM-A", sequence: 4 },
    });
  });

  it("accepts a new stream with a reset sequence", () => {
    const result = reduceRuntimeStreamEvent(
      snapshot,
      { streamId: "STREAM-OLD", sequence: 80 },
      streamEvent(1, { event_type: "runtime.snapshot", snapshot }, "STREAM-NEW"),
    );

    expect(result.accepted).toBe(true);
    expect(result.cursor).toEqual({ streamId: "STREAM-NEW", sequence: 1 });
  });
});

describe("runtimeStatusCounts", () => {
  it("counts only backend-projected task states", () => {
    expect(runtimeStatusCounts(snapshot)).toEqual({ pending: 1, in_service: 1 });
  });
});

describe("shouldReplaceRuntimeSnapshot", () => {
  it("rejects an older revision even when its response arrives later", () => {
    const older = { ...snapshot, revision: 1 } as RuntimeSessionSnapshot;

    expect(shouldReplaceRuntimeSnapshot(snapshot, older)).toBe(false);
  });

  it("rejects a same-revision clock regression but accepts a reset revision", () => {
    const staleClock = {
      ...snapshot,
      clock: { ...snapshot.clock, simulation_time: "2026-08-01T07:59:59+08:00" },
    } as RuntimeSessionSnapshot;
    const reset = {
      ...snapshot,
      revision: 3,
      clock: { ...snapshot.clock, simulation_time: "2026-08-01T07:55:00+08:00" },
    } as RuntimeSessionSnapshot;

    expect(shouldReplaceRuntimeSnapshot(snapshot, staleClock)).toBe(false);
    expect(shouldReplaceRuntimeSnapshot(snapshot, reset)).toBe(true);
  });
});

function assignment(
  taskId: string,
  resourceId: string,
  start: string,
  destination = "GATE-E01",
): Assignment {
  const end = new Date(Date.parse(start) + 8 * 60_000).toISOString();
  return {
    assignment_id: `ASG-${taskId}-${resourceId}`,
    task_id: taskId,
    resource_id: resourceId,
    resource_type: "wheelchair",
    resource_start_zone_id: "TRANSFER-DESK",
    origin_zone_id: "TRANSFER-DESK",
    destination_zone_id: destination,
    travel_started_at: start,
    travel_ended_at: start,
    service_started_at: start,
    service_ended_at: end,
    reposition_minutes: 0,
    service_minutes: 8,
    wait_minutes: 0,
  };
}

describe("compareRuntimePlans", () => {
  it("reports assignment, resource, schedule and route changes from plan facts", () => {
    const unchanged = assignment("TASK-003", "WC-01", "2026-08-01T08:30:00+08:00");
    const active = {
      assignments: [
        assignment("TASK-001", "WC-01", "2026-08-01T08:00:00+08:00"),
        unchanged,
      ],
      unassigned_tasks: [{ task_id: "TASK-002", reason: "time_window", detail: "时间不足" }],
    } as unknown as Plan;
    const candidate = {
      assignments: [
        {
          ...assignment("TASK-001", "WC-02", "2026-08-01T08:10:00+08:00", "GATE-W03"),
          wait_minutes: 10,
        },
        assignment("TASK-002", "WC-01", "2026-08-01T08:20:00+08:00"),
        unchanged,
      ],
      unassigned_tasks: [],
    } as unknown as Plan;

    const comparison = compareRuntimePlans(active, candidate);

    expect(comparison.unchangedTaskIds).toEqual(["TASK-003"]);
    expect(comparison.changes).toEqual([
      expect.objectContaining({
        taskId: "TASK-001",
        changedFields: ["resource", "schedule", "route", "wait"],
      }),
      expect.objectContaining({
        taskId: "TASK-002",
        changedFields: ["assignment", "coordination"],
      }),
    ]);
  });
});
