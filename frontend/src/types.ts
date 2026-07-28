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
