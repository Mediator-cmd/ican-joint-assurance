import type {
  Assignment,
  Plan,
  RuntimeSessionSnapshot,
  RuntimeStreamEvent,
  RuntimeStreamEventType,
} from "./types";

export type RuntimePlanChangeField =
  | "assignment"
  | "resource"
  | "schedule"
  | "route"
  | "wait"
  | "coordination";

export interface RuntimePlanTaskDelta {
  taskId: string;
  changedFields: RuntimePlanChangeField[];
  activeAssignment: Assignment | null;
  candidateAssignment: Assignment | null;
  activeUnassigned: Plan["unassigned_tasks"][number] | null;
  candidateUnassigned: Plan["unassigned_tasks"][number] | null;
}

export interface RuntimePlanComparison {
  changes: RuntimePlanTaskDelta[];
  unchangedTaskIds: string[];
}

export const RUNTIME_STREAM_EVENT_TYPES: readonly RuntimeStreamEventType[] = [
  "runtime.snapshot",
  "runtime.tick",
  "task.transition",
  "event.applied",
  "replan.started",
  "replan.ready",
  "replan.failed",
  "plan.accepted",
  "plan.rejected",
  "runtime.completed",
  "heartbeat",
];

export interface RuntimeStreamCursor {
  streamId: string;
  sequence: number;
}

export interface RuntimeStreamReduction {
  accepted: boolean;
  snapshot: RuntimeSessionSnapshot | null;
  cursor: RuntimeStreamCursor | null;
}

export function shouldReplaceRuntimeSnapshot(
  current: RuntimeSessionSnapshot | null,
  next: RuntimeSessionSnapshot,
): boolean {
  if (current === null || current.session_id !== next.session_id) return true;
  if (current.revision !== next.revision) return next.revision > current.revision;

  const currentTime = Date.parse(current.clock.simulation_time);
  const nextTime = Date.parse(next.clock.simulation_time);
  return !Number.isFinite(currentTime) || !Number.isFinite(nextTime) || nextTime >= currentTime;
}

export function isRuntimeStreamEvent(value: unknown): value is RuntimeStreamEvent {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (
    typeof record.stream_id !== "string"
    || typeof record.sequence !== "number"
    || typeof record.session_id !== "string"
    || typeof record.revision !== "number"
    || typeof record.payload !== "object"
    || record.payload === null
  ) {
    return false;
  }
  const eventType = (record.payload as Record<string, unknown>).event_type;
  return typeof eventType === "string"
    && RUNTIME_STREAM_EVENT_TYPES.includes(eventType as RuntimeStreamEventType);
}

export function reduceRuntimeStreamEvent(
  current: RuntimeSessionSnapshot | null,
  cursor: RuntimeStreamCursor | null,
  event: RuntimeStreamEvent,
): RuntimeStreamReduction {
  if (cursor?.streamId === event.stream_id && event.sequence <= cursor.sequence) {
    return { accepted: false, snapshot: current, cursor };
  }
  if (current !== null && event.session_id !== current.session_id) {
    return { accepted: false, snapshot: current, cursor };
  }

  let snapshot = current;
  if (event.payload.event_type === "runtime.snapshot") {
    snapshot = event.payload.snapshot;
  } else if (event.payload.event_type === "runtime.tick" && current !== null) {
    snapshot = { ...current, clock: event.payload.clock };
  }

  return {
    accepted: true,
    snapshot,
    cursor: { streamId: event.stream_id, sequence: event.sequence },
  };
}

export function runtimeStatusCounts(snapshot: RuntimeSessionSnapshot) {
  return snapshot.tasks.reduce<Record<string, number>>((counts, task) => {
    counts[task.status] = (counts[task.status] ?? 0) + 1;
    return counts;
  }, {});
}

export function compareRuntimePlans(active: Plan, candidate: Plan): RuntimePlanComparison {
  const activeAssignments = new Map(active.assignments.map((item) => [item.task_id, item]));
  const candidateAssignments = new Map(candidate.assignments.map((item) => [item.task_id, item]));
  const activeUnassigned = new Map(active.unassigned_tasks.map((item) => [item.task_id, item]));
  const candidateUnassigned = new Map(candidate.unassigned_tasks.map((item) => [item.task_id, item]));
  const taskIds = new Set([
    ...activeAssignments.keys(),
    ...candidateAssignments.keys(),
    ...activeUnassigned.keys(),
    ...candidateUnassigned.keys(),
  ]);
  const changes: RuntimePlanTaskDelta[] = [];
  const unchangedTaskIds: string[] = [];

  [...taskIds].sort().forEach((taskId) => {
    const before = activeAssignments.get(taskId) ?? null;
    const after = candidateAssignments.get(taskId) ?? null;
    const beforeUnassigned = activeUnassigned.get(taskId) ?? null;
    const afterUnassigned = candidateUnassigned.get(taskId) ?? null;
    const changedFields: RuntimePlanChangeField[] = [];

    if ((before === null) !== (after === null)) changedFields.push("assignment");
    if (before && after) {
      if (before.resource_id !== after.resource_id) changedFields.push("resource");
      if (
        before.service_started_at !== after.service_started_at
        || before.service_ended_at !== after.service_ended_at
      ) changedFields.push("schedule");
      if (
        before.origin_zone_id !== after.origin_zone_id
        || before.destination_zone_id !== after.destination_zone_id
      ) changedFields.push("route");
      if (before.wait_minutes !== after.wait_minutes) changedFields.push("wait");
    }
    if (
      beforeUnassigned?.reason !== afterUnassigned?.reason
      || beforeUnassigned?.detail !== afterUnassigned?.detail
    ) changedFields.push("coordination");

    if (changedFields.length === 0) {
      unchangedTaskIds.push(taskId);
      return;
    }
    changes.push({
      taskId,
      changedFields,
      activeAssignment: before,
      candidateAssignment: after,
      activeUnassigned: beforeUnassigned,
      candidateUnassigned: afterUnassigned,
    });
  });

  return { changes, unchangedTaskIds };
}
