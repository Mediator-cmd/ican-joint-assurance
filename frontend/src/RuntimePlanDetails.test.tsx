import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RuntimePlanDetails from "./RuntimePlanDetails";
import type { Assignment, Plan, Scenario } from "./types";

function assignment(index: number, resourceId = "WC-01"): Assignment {
  const taskId = `TASK-${String(index).padStart(3, "0")}`;
  return {
    assignment_id: `ASG-${String(index).padStart(3, "0")}`,
    task_id: taskId,
    resource_id: resourceId,
    resource_type: "wheelchair",
    resource_start_zone_id: "ZONE-01",
    origin_zone_id: "ZONE-01",
    destination_zone_id: "ZONE-02",
    travel_started_at: "2026-08-01T08:00:00+08:00",
    travel_ended_at: "2026-08-01T08:05:00+08:00",
    service_started_at: "2026-08-01T08:05:00+08:00",
    service_ended_at: "2026-08-01T08:15:00+08:00",
    reposition_minutes: 5,
    service_minutes: 10,
    wait_minutes: 0,
  };
}

function plan(planId: string, assignments: Assignment[]): Plan {
  return {
    plan_id: planId,
    scenario_id: "SCN-M6-UI",
    scenario_version: 1,
    algorithm: "rolling_cp_sat_v1",
    generated_at: "2026-08-01T08:00:00+08:00",
    status: "executable",
    assignments,
    unassigned_tasks: [],
    violations: [],
    metrics: {
      total_tasks: assignments.length,
      assigned_tasks: assignments.length,
      unassigned_tasks: 0,
      average_wait_minutes: 0,
      max_wait_minutes: 0,
      task_completion_rate_pct: 100,
      critical_task_completion_rate_pct: 100,
      overall_resource_utilization_pct: 50,
      resource_metrics: [],
    },
  };
}

const scenario = {
  tasks: [],
  flights: [],
  zones: [],
} as unknown as Scenario;

describe("RuntimePlanDetails bounded data windows", () => {
  it("renders only the first 25 plan tasks while preserving the total", () => {
    const assignments = Array.from({ length: 30 }, (_, index) => assignment(index + 1));
    const markup = renderToStaticMarkup(
      <RuntimePlanDetails
        activePlan={plan("PLAN-ACTIVE", assignments)}
        candidatePlan={null}
        scenario={scenario}
        eventDetails={[]}
        frozenTaskCount={0}
      />,
    );

    expect(markup.match(/class="plan-assignment-row"/g)).toHaveLength(25);
    expect(markup).toContain("TASK-025");
    expect(markup).not.toContain("TASK-026");
    expect(markup).toContain("1–25");
    expect(markup).toContain("共 30 项");
  });

  it("renders only the first 20 candidate changes while preserving the total", () => {
    const activeAssignments = Array.from({ length: 30 }, (_, index) => assignment(index + 1));
    const candidateAssignments = activeAssignments.map((item) => ({
      ...item,
      assignment_id: `${item.assignment_id}-C`,
      resource_id: "WC-02",
    }));
    const markup = renderToStaticMarkup(
      <RuntimePlanDetails
        activePlan={plan("PLAN-ACTIVE", activeAssignments)}
        candidatePlan={plan("PLAN-CANDIDATE", candidateAssignments)}
        scenario={scenario}
        eventDetails={["匿名教学扰动"]}
        frozenTaskCount={4}
      />,
    );

    expect(markup.match(/<p><span>当前<\/span>/g)).toHaveLength(20);
    expect(markup).toContain("30 项调整");
    expect(markup).toContain("1–20");
    expect(markup).toContain("共 30 项");
  });
});
