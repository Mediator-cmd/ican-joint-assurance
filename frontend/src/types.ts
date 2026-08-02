export type ViewKey = "baseline" | "after_events_fifo" | "optimized";

export interface ProjectInfo {
  name: string;
  subtitle: string;
  safety_notice: string;
  data_classification: "synthetic" | "anonymized_replay";
}

export interface DemoEvent {
  event_id: string;
  event_type: "delay" | "gate_change";
  occurred_at: string;
  title: string;
  detail: string;
  note: string | null;
}

export interface ScenarioChange {
  entity_type: "flight" | "task";
  entity_id: string;
  field: string;
  before: string;
  after: string;
}

export interface Flight {
  flight_id: string;
  display_code: string;
  scheduled_departure: string;
  boarding_starts_at: string;
  gate_id: string;
  status: string;
}

export interface ServiceTask {
  task_id: string;
  flight_id: string;
  task_type: string;
  passenger_group: string;
  origin_zone_id: string;
  destination_zone_id: string;
  release_at: string;
  deadline_at: string;
  duration_minutes: number;
  party_size: number;
  priority: number;
  required_resource_type: string;
  locked: boolean;
}

export interface Resource {
  resource_id: string;
  resource_type: string;
  home_zone_id: string;
  current_zone_id: string;
  status: string;
  capacity: number;
  available_from: string;
  available_to: string;
}

export interface Zone {
  zone_id: string;
  name: string;
  travel_minutes: Record<string, number>;
}

export interface Scenario {
  scenario_id: string;
  name: string;
  version: number;
  run_mode: string;
  data_classification: string;
  window_start: string;
  window_end: string;
  zones: Zone[];
  flights: Flight[];
  tasks: ServiceTask[];
  resources: Resource[];
}

export interface Assignment {
  assignment_id: string;
  task_id: string;
  resource_id: string;
  resource_type: string;
  resource_start_zone_id: string;
  origin_zone_id: string;
  destination_zone_id: string;
  travel_started_at: string;
  travel_ended_at: string;
  service_started_at: string;
  service_ended_at: string;
  reposition_minutes: number;
  service_minutes: number;
  wait_minutes: number;
}

export interface ResourceMetric {
  resource_id: string;
  resource_type: string;
  busy_minutes: number;
  available_minutes: number;
  utilization_pct: number;
}

export interface PlanMetrics {
  total_tasks: number;
  assigned_tasks: number;
  unassigned_tasks: number;
  average_wait_minutes: number;
  max_wait_minutes: number;
  task_completion_rate_pct: number;
  critical_task_completion_rate_pct: number;
  overall_resource_utilization_pct: number;
  resource_metrics: ResourceMetric[];
}

export interface Plan {
  plan_id: string;
  scenario_id: string;
  scenario_version: number;
  algorithm: string;
  generated_at: string;
  status: "executable" | "partial" | "invalid";
  assignments: Assignment[];
  unassigned_tasks: Array<{ task_id: string; reason: string; detail: string }>;
  violations: Array<{ code: string; message: string }>;
  metrics: PlanMetrics;
}

export interface DemoView {
  label: string;
  scenario: Scenario;
  plan: Plan;
}

export interface DemoPayload {
  project: ProjectInfo;
  events: DemoEvent[];
  changes: ScenarioChange[];
  views: Record<ViewKey, DemoView>;
}

export type DemoDataSource = "api" | "static_fallback";

export type DemoLoadFailureReason = "network_error" | "http_error" | "invalid_response";

export interface DemoLoadFailure {
  source: DemoDataSource;
  reason: DemoLoadFailureReason;
  status?: number;
}

export interface DemoLoadResult {
  payload: DemoPayload;
  source: DemoDataSource;
}

export type SimulationSpeed = 1 | 5 | 15;

export type RuntimeStatus =
  | "ready"
  | "running"
  | "paused"
  | "replanning"
  | "awaiting_confirmation"
  | "completed"
  | "failed";

export type TaskRuntimeStatus =
  | "pending"
  | "en_route"
  | "waiting"
  | "in_service"
  | "completed"
  | "unassigned"
  | "affected";

export type ResourceRuntimeStatus =
  | "idle"
  | "moving"
  | "waiting"
  | "serving"
  | "unavailable";

export type RuntimeEventStatus =
  | "pending"
  | "triggered"
  | "applied"
  | "replanning"
  | "awaiting_confirmation"
  | "resolved"
  | "failed";

export interface RuntimeClockSnapshot {
  simulation_time: string;
  server_time: string;
  speed: SimulationSpeed;
  is_advancing: boolean;
  next_boundary_at: string | null;
}

export interface TaskRuntimeProjection {
  task_id: string;
  status: TaskRuntimeStatus;
  assignment_id: string | null;
  resource_id: string | null;
  current_zone_id: string | null;
  next_transition_at: string | null;
  affected_by_event_ids: string[];
  status_label: string;
  is_locked: boolean;
}

export interface RuntimePosition {
  from_zone_id: string;
  to_zone_id: string | null;
  progress_pct: number;
}

export interface ResourceRuntimeProjection {
  resource_id: string;
  status: ResourceRuntimeStatus;
  position: RuntimePosition;
  current_task_id: string | null;
  next_task_id: string | null;
  next_available_at: string | null;
  status_label: string;
}

export type FlightRuntimeStatus = "scheduled" | "boarding" | "delayed" | "departed";

export interface FlightRuntimeProjection {
  flight_id: string;
  status: FlightRuntimeStatus;
  estimated_departure: string;
  gate_id: string;
  last_event_id: string | null;
}

export interface EventRuntimeProjection {
  event_id: string;
  status: RuntimeEventStatus;
  occurred_at: string;
  applied_at: string | null;
  scenario_version_after: number | null;
  candidate_plan_id: string | null;
  failure_code: string | null;
  status_label: string;
}

export interface RuntimeGuidance {
  headline: string;
  detail: string;
  action_required: boolean;
  recommended_action: string;
}

export interface RuntimeFailure {
  code: string;
  message: string;
  recoverable: boolean;
}

export interface RuntimeSessionSnapshot {
  session_id: string;
  scenario_id: string;
  initial_scenario_version: number;
  current_scenario_version: number;
  initial_plan_id: string;
  active_plan_id: string;
  candidate_plan_id: string | null;
  status: RuntimeStatus;
  status_label: string;
  revision: number;
  clock: RuntimeClockSnapshot;
  tasks: TaskRuntimeProjection[];
  resources: ResourceRuntimeProjection[];
  flights: FlightRuntimeProjection[];
  events: EventRuntimeProjection[];
  guidance: RuntimeGuidance;
  failure: RuntimeFailure | null;
  created_at: string;
  updated_at: string;
  storage_scope: "sqlite";
  safety_notice: string;
}

export interface RuntimeSessionSummary {
  session_id: string;
  scenario_id: string;
  current_scenario_version: number;
  active_plan_id: string;
  status: RuntimeStatus;
  status_label: string;
  revision: number;
  simulation_time: string;
  speed: SimulationSpeed;
  action_required: boolean;
  updated_at: string;
}

export interface RuntimeSessionListResponse {
  items: RuntimeSessionSummary[];
  total: number;
  offset: number;
  limit: number;
  storage_scope: "sqlite";
  safety_notice: string;
}

export interface PlanSummary {
  plan_id: string;
  scenario_id: string;
  scenario_version: number;
  algorithm: "fifo" | "cp_sat";
  display_name: string;
  status: "executable" | "partial" | "invalid";
  status_label: string;
  assigned_tasks: number;
  total_tasks: number;
  urgent_task_completion_rate_pct: number;
  average_wait_minutes: number;
  needs_manual_handling: number;
  constraint_conflicts: number;
  result_summary: string;
  tradeoff_summary: string;
  recommended_action: string;
}

export interface PlanListResponse {
  items: PlanSummary[];
  total: number;
  storage_scope: "process_memory";
}

export interface PlanRecord {
  plan: Plan;
  guidance: {
    display_name: string;
    status_label: string;
    result_summary: string;
    tradeoff_summary: string;
    recommended_action: string;
    calculation_basis: string[];
    requires_human_confirmation: true;
  };
  storage_scope: "process_memory";
  safety_notice: string;
}

export type RuntimeStreamEventType =
  | "runtime.snapshot"
  | "runtime.tick"
  | "task.transition"
  | "event.applied"
  | "replan.started"
  | "replan.ready"
  | "replan.failed"
  | "plan.accepted"
  | "plan.rejected"
  | "runtime.completed"
  | "heartbeat";

export type RuntimeStreamPayload =
  | { event_type: "runtime.snapshot"; snapshot: RuntimeSessionSnapshot }
  | { event_type: "runtime.tick"; clock: RuntimeClockSnapshot }
  | {
      event_type: "task.transition";
      task_id: string;
      previous_status: TaskRuntimeStatus;
      current_status: TaskRuntimeStatus;
      transition_at: string;
    }
  | {
      event_type: "event.applied";
      event_ids: string[];
      scenario_version_before: number;
      scenario_version_after: number;
    }
  | {
      event_type: "replan.started";
      trigger: "automatic_event" | "manual" | "resource_change" | "plan_invalid";
      event_ids: string[];
    }
  | {
      event_type: "replan.ready";
      active_plan_id: string;
      candidate_plan_id: string;
      affected_task_ids: string[];
      violation_count: 0;
    }
  | {
      event_type: "replan.failed";
      error_code: string;
      message: string;
      fallback_available: boolean;
    }
  | { event_type: "plan.accepted"; previous_plan_id: string; active_plan_id: string }
  | { event_type: "plan.rejected"; active_plan_id: string; rejected_plan_id: string }
  | {
      event_type: "runtime.completed";
      completed_at: string;
      completed_task_count: number;
      unassigned_task_count: number;
    }
  | { event_type: "heartbeat"; server_time: string };

export interface RuntimeStreamEvent {
  stream_id: string;
  sequence: number;
  session_id: string;
  revision: number;
  emitted_at: string;
  payload: RuntimeStreamPayload;
  sse_id: string;
}

export type RuntimeConnectionStatus =
  | "connecting"
  | "live"
  | "recovering"
  | "polling"
  | "offline_readonly";
