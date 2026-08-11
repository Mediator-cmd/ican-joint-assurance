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
  objective_profile?: PlanningObjectiveProfile;
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

export type PlanningObjectiveProfile =
  | "balanced"
  | "critical_first"
  | "minimum_wait"
  | "minimum_change";

export type AssistanceMode = "auto" | "deterministic_only";
export type AssistanceSource = "language_model" | "deterministic_rules";
export type AssistanceFallbackReason =
  | "model_not_configured"
  | "model_timeout"
  | "provider_error"
  | "invalid_model_output"
  | "question_not_grounded";

export interface AssistanceTrace {
  source: AssistanceSource;
  provider_attempted: boolean;
  model_label: string | null;
  fallback_reason: AssistanceFallbackReason | null;
}

export type EventDraftStatus = "ready_for_review" | "needs_clarification" | "unsupported";
export type EventDraftField =
  | "event_type"
  | "flight_id"
  | "occurred_at"
  | "delay_minutes"
  | "previous_gate_id"
  | "new_gate_id";
export type EvidenceOrigin = "user_text" | "authoritative_context" | "deterministic_derivation";

export interface EventDraftBasis {
  scenario_id: string;
  scenario_version: number;
  reference_time: string;
  runtime_session_id: string | null;
  runtime_revision: number | null;
}

export interface ExtractedFieldEvidence {
  field: EventDraftField;
  normalized_value: string;
  origin: EvidenceOrigin;
  source_quote: string | null;
}

export interface ClarificationOption {
  value: string;
  label: string;
}

export interface ClarificationQuestion {
  field: EventDraftField;
  question: string;
  reason: string;
  options: ClarificationOption[];
}

export interface ObjectiveRecommendation {
  profile: PlanningObjectiveProfile;
  display_name: string;
  rationale: string;
  source_quote: string | null;
  requires_human_confirmation: true;
  applied_to_planner: false;
}

export interface FlightEventDraft {
  event_id: string;
  event_type: "delay" | "gate_change";
  flight_id: string;
  occurred_at: string;
  delay_minutes: number | null;
  previous_gate_id: string | null;
  new_gate_id: string | null;
  note: string | null;
}

export interface EventDraftResponse {
  draft_id: string;
  basis: EventDraftBasis;
  status: EventDraftStatus;
  trace: AssistanceTrace;
  event: FlightEventDraft | null;
  evidence: ExtractedFieldEvidence[];
  missing_fields: EventDraftField[];
  clarification_questions: ClarificationQuestion[];
  objective_recommendation: ObjectiveRecommendation | null;
  warnings: string[];
  requires_human_confirmation: true;
  applies_automatically: false;
  safety_notice: string;
}

export type ExplanationFocus = "summary" | "tradeoffs" | "task_changes" | "manual_handling";

export interface ExplanationEvidence {
  evidence_id: string;
  kind: string;
  entity_ids: string[];
  field: string;
  value: string;
  statement: string;
}

export interface ExplanationClaim {
  statement: string;
  evidence_ids: string[];
}

export type QuestionAnswerStatus = "not_asked" | "answered" | "insufficient_evidence";

export interface QuestionAnswer {
  status: QuestionAnswerStatus;
  statement: string;
  evidence_ids: string[];
  matched_entity_ids: string[];
}

export interface PlanExplanationResponse {
  explanation_id: string;
  context: {
    scope: "runtime_plan";
    session_id: string;
    revision: number;
    plan_id: string;
    baseline_plan_id: string | null;
  };
  trace: AssistanceTrace;
  focus: ExplanationFocus;
  question: string | null;
  question_answer: QuestionAnswer;
  summary: ExplanationClaim;
  tradeoffs: ExplanationClaim[];
  task_changes: ExplanationClaim[];
  manual_handling: ExplanationClaim[];
  recommended_next_step: ExplanationClaim;
  evidence: ExplanationEvidence[];
  unresolved_questions: string[];
  requires_human_confirmation: true;
  modifies_plan: false;
  safety_notice: string;
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
  event_type: "delay" | "gate_change";
  flight_id: string;
  detail: string;
  note: string | null;
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

export interface RuntimeCollectionSummary {
  task_total: number;
  task_active: number;
  task_attention: number;
  task_completed: number;
  task_locked: number;
  resource_total: number;
  resource_active: number;
  event_total: number;
  event_open: number;
  event_pending: number;
  flight_total: number;
}

export interface RuntimeSessionSnapshot {
  session_id: string;
  scenario_id: string;
  initial_scenario_version: number;
  current_scenario_version: number;
  initial_plan_id: string;
  active_plan_id: string;
  candidate_plan_id: string | null;
  active_plan_detail: Plan | null;
  candidate_plan_detail: Plan | null;
  objective_profile: PlanningObjectiveProfile;
  status: RuntimeStatus;
  status_label: string;
  revision: number;
  clock: RuntimeClockSnapshot;
  tasks: TaskRuntimeProjection[];
  resources: ResourceRuntimeProjection[];
  flights: FlightRuntimeProjection[];
  events: EventRuntimeProjection[];
  collection_summary: RuntimeCollectionSummary;
  guidance: RuntimeGuidance;
  failure: RuntimeFailure | null;
  created_at: string;
  updated_at: string;
  storage_scope: "sqlite";
  safety_notice: string;
}

export interface NormalizedPoint {
  x: number;
  y: number;
}

export interface SpatialCanvas {
  width: number;
  height: number;
}

export interface SpatialAsset {
  kind: "svg";
  source_class: "original_local";
  public_path: string;
  license_id: "project-original";
  integrity_sha256: string;
  safety_classification: "anonymous_training_simulation";
}

export interface SpatialZone {
  zone_id: string;
  label: string;
  floor: string;
  anchor: NormalizedPoint;
  shape: NormalizedPoint[];
}

export interface SpatialPath {
  path_id: string;
  from_zone_id: string;
  to_zone_id: string;
  points: NormalizedPoint[];
  direction: "bidirectional";
  accessible: boolean;
}

export interface SpatialLayout {
  layout_id: string;
  layout_version: number;
  scenario_id: string;
  scenario_version: number;
  projection: "normalized_cartesian";
  canvas: SpatialCanvas;
  asset: SpatialAsset;
  zones: SpatialZone[];
  paths: SpatialPath[];
  safety_notice: string;
}

export type SpatialRouteKind = "active" | "candidate";
export type SpatialAssignmentState = "assigned" | "unassigned";
export type SpatialRouteChange =
  | "current"
  | "assignment_added"
  | "assignment_removed"
  | "resource_changed"
  | "route_changed"
  | "schedule_changed";

export interface SpatialRouteLeg {
  path_id: string;
  from_zone_id: string;
  to_zone_id: string;
  traversal: "forward" | "reverse";
}

export interface SpatialTaskRoute {
  route_id: string;
  task_id: string;
  route_kind: SpatialRouteKind;
  plan_id: string;
  task_status: TaskRuntimeStatus;
  assignment_state: SpatialAssignmentState;
  change_kind: SpatialRouteChange;
  resource_id: string | null;
  origin_zone_id: string;
  destination_zone_id: string;
  legs: SpatialRouteLeg[];
  demand_only: boolean;
}

export interface SpatialResourceMarker {
  resource_id: string;
  status: ResourceRuntimeStatus;
  current_task_id: string | null;
  next_task_id: string | null;
  from_zone_id: string;
  to_zone_id: string | null;
  progress_pct: number;
  position: NormalizedPoint;
  route_legs: SpatialRouteLeg[];
}

export interface SpatialEventMarker {
  event_id: string;
  event_type: "delay" | "gate_change";
  flight_id: string;
  status: RuntimeEventStatus;
  detail: string;
  primary_zone_id: string;
  position: NormalizedPoint;
  previous_zone_id: string | null;
  new_zone_id: string | null;
  route_legs: SpatialRouteLeg[];
}

export interface RuntimeSpatialOverlay {
  session_id: string;
  revision: number;
  simulation_time: string;
  layout_id: string;
  task_routes: SpatialTaskRoute[];
  resource_markers: SpatialResourceMarker[];
  event_markers: SpatialEventMarker[];
}

export interface SpatialCoverage {
  source_task_ids: string[];
  projected_active_task_ids: string[];
  changed_candidate_task_ids: string[];
  projected_candidate_task_ids: string[];
  source_resource_ids: string[];
  projected_resource_ids: string[];
  source_event_ids: string[];
  projected_event_ids: string[];
  complete: true;
}

export type SpatialFactCategory = "context" | "coverage" | "task" | "resource" | "event" | "plan";

export interface SpatialFact {
  fact_id: string;
  category: SpatialFactCategory;
  claim: string;
  entity_ids: string[];
}

export interface RuntimeSpatialView {
  scenario_id: string;
  scenario_version: number;
  layout: SpatialLayout;
  overlay: RuntimeSpatialOverlay;
  coverage: SpatialCoverage;
  facts: SpatialFact[];
  requires_human_confirmation: true;
  modifies_runtime: false;
  safety_notice: string;
}

export type RuntimeSpatialStatus = "loading" | "ready" | "unavailable" | "error";

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
