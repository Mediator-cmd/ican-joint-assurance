import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Route,
  ShieldCheck,
  UsersRound,
} from "lucide-react";
import { useState } from "react";

import PaginationControls from "./PaginationControls";
import { paginateItems } from "./pagination";
import { compareRuntimePlans } from "./runtime";
import type { RuntimePlanChangeField } from "./runtime";
import type { Assignment, Plan, Scenario } from "./types";

const PLAN_TASK_PAGE_SIZE = 25;
const PLAN_CHANGE_PAGE_SIZE = 20;

type PlanTaskEntry =
  | { kind: "assignment"; assignment: Assignment }
  | { kind: "unassigned"; task: Plan["unassigned_tasks"][number] };

const taskTypeLabels: Record<string, string> = {
  wheelchair_transfer: "轮椅转运",
  escort: "陪同引导",
  shuttle_transfer: "摆渡转运",
  boarding_assistance: "登机协助",
};

const resourceTypeLabels: Record<string, string> = {
  shuttle_bus: "摆渡车辆",
  wheelchair: "轮椅设备",
  service_agent: "保障人员",
};

const unassignedReasonLabels: Record<string, string> = {
  no_compatible_resource: "没有匹配资源",
  resource_unavailable: "资源不可用",
  no_route: "缺少可行路线",
  time_window: "无法满足时间窗",
  priority_tradeoff: "优先级取舍",
};

const changeFieldLabels: Record<RuntimePlanChangeField, string> = {
  assignment: "安排状态变化",
  resource: "执行资源变化",
  schedule: "服务时间变化",
  route: "保障路线变化",
  wait: "等待时间变化",
  coordination: "人工协调原因变化",
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function runtimePlanAlgorithmLabel(algorithm: string): string {
  if (algorithm === "rolling_cp_sat_v1") return "滚动约束优化";
  if (algorithm === "cp_sat_priority_v1" || algorithm === "cp_sat") {
    return "CP-SAT 约束优化";
  }
  if (algorithm === "fifo_baseline_v1" || algorithm === "fifo") {
    return "FIFO 规则方案";
  }
  return "确定性调度方案";
}

export function runtimePlanFacts(plan: Plan): string {
  return [
    `${plan.metrics.assigned_tasks}/${plan.metrics.total_tasks} 项任务已安排`,
    `关键任务 ${plan.metrics.critical_task_completion_rate_pct.toFixed(0)}%`,
    `硬约束 ${plan.violations.length}`,
  ].join(" · ");
}

function AssignmentList({ entries, scenario }: { entries: PlanTaskEntry[]; scenario: Scenario }) {
  const taskMeta = new Map(scenario.tasks.map((task) => [task.task_id, task]));
  const flightMeta = new Map(scenario.flights.map((flight) => [flight.flight_id, flight]));
  const zoneNames = new Map(scenario.zones.map((zone) => [zone.zone_id, zone.name]));

  return (
    <div className="plan-assignment-list">
      {entries.map((entry) => {
        if (entry.kind === "unassigned") {
          const task = entry.task;
          return (
            <article className="plan-assignment-row unassigned" key={`unassigned-${task.task_id}`}>
              <div className="plan-task-identity"><strong>{task.task_id}</strong><span>转人工协调</span></div>
              <div className="plan-unassigned-copy"><span>{unassignedReasonLabels[task.reason] ?? "暂不可安排"}</span><strong>{task.detail}</strong></div>
            </article>
          );
        }
        const assignment = entry.assignment;
        const task = taskMeta.get(assignment.task_id);
        const flight = flightMeta.get(task?.flight_id ?? "");
        return (
          <article className="plan-assignment-row" key={assignment.assignment_id}>
            <div className="plan-task-identity">
              <strong>{assignment.task_id}</strong>
              <span>{taskTypeLabels[task?.task_type ?? ""] ?? "保障任务"} · {flight?.display_code ?? task?.flight_id ?? "待定航班"}</span>
            </div>
            <div><span>执行资源</span><strong>{resourceTypeLabels[assignment.resource_type] ?? assignment.resource_type} · {assignment.resource_id}</strong></div>
            <div><span>服务时段</span><strong>{formatTime(assignment.service_started_at)}–{formatTime(assignment.service_ended_at)}</strong></div>
            <div><span>保障路线</span><strong>{zoneNames.get(assignment.origin_zone_id) ?? assignment.origin_zone_id} → {zoneNames.get(assignment.destination_zone_id) ?? assignment.destination_zone_id}</strong></div>
          </article>
        );
      })}
    </div>
  );
}

function PlanColumn({
  label,
  plan,
  scenario,
  purpose,
  candidate = false,
}: {
  label: string;
  plan: Plan;
  scenario: Scenario;
  purpose: string;
  candidate?: boolean;
}) {
  const [taskPage, setTaskPage] = useState(1);
  const entries: PlanTaskEntry[] = [
    ...plan.assignments.map((assignment): PlanTaskEntry => ({ kind: "assignment", assignment })),
    ...plan.unassigned_tasks.map((task): PlanTaskEntry => ({ kind: "unassigned", task })),
  ];
  const taskWindow = paginateItems(entries, taskPage, PLAN_TASK_PAGE_SIZE);

  return (
    <section className={`plan-detail-column${candidate ? " candidate" : ""}`}>
      <header>
        <div><span>{label}</span><h3>{runtimePlanAlgorithmLabel(plan.algorithm)}</h3></div>
        <span className="constraint-proof"><ShieldCheck size={16} />硬约束 {plan.violations.length}</span>
      </header>
      <p className="plan-purpose">{purpose}</p>
      <code>{plan.plan_id}</code>
      <div className="plan-metric-strip">
        <div><UsersRound size={17} /><span>已安排</span><strong>{plan.metrics.assigned_tasks}/{plan.metrics.total_tasks}</strong></div>
        <div><Clock3 size={17} /><span>平均等待</span><strong>{plan.metrics.average_wait_minutes.toFixed(1)} 分钟</strong></div>
        <div><Route size={17} /><span>资源利用</span><strong>{plan.metrics.overall_resource_utilization_pct.toFixed(1)}%</strong></div>
        <div><CheckCircle2 size={17} /><span>关键任务</span><strong>{plan.metrics.critical_task_completion_rate_pct.toFixed(0)}%</strong></div>
      </div>
      <div className="plan-list-heading"><strong>具体执行内容</strong><span>{plan.assignments.length} 项执行 · {plan.unassigned_tasks.length} 项协调</span></div>
      <AssignmentList entries={taskWindow.items} scenario={scenario} />
      <PaginationControls
        label={`${label}任务书`}
        total={entries.length}
        page={taskWindow.page}
        pageSize={PLAN_TASK_PAGE_SIZE}
        onPageChange={setTaskPage}
      />
    </section>
  );
}

function assignmentSummary(assignment: Assignment | null): string {
  if (!assignment) return "人工协调";
  return `${assignment.resource_id} · ${formatTime(assignment.service_started_at)}–${formatTime(assignment.service_ended_at)} · ${assignment.origin_zone_id} → ${assignment.destination_zone_id}`;
}

export default function RuntimePlanDetails({
  activePlan,
  candidatePlan,
  scenario,
  eventDetails,
  frozenTaskCount,
}: {
  activePlan: Plan | null | undefined;
  candidatePlan: Plan | null | undefined;
  scenario: Scenario;
  eventDetails: string[];
  frozenTaskCount: number;
}) {
  const [changePage, setChangePage] = useState(1);
  if (!activePlan) {
    return (
      <div className="plan-detail-unavailable">
        <AlertTriangle size={22} />
        <div><strong>方案明细尚未同步</strong><p>页面只收到了审计编号，没有收到任务、资源和时段事实。请先读取最新权威快照；仍为空时需重新启动项目服务。页面不会根据方案编号补造内容。</p></div>
      </div>
    );
  }

  const comparison = candidatePlan ? compareRuntimePlans(activePlan, candidatePlan) : null;
  const changeWindow = paginateItems(
    comparison?.changes ?? [],
    changePage,
    PLAN_CHANGE_PAGE_SIZE,
  );
  const responseTarget = eventDetails.length > 0
    ? `响应 ${eventDetails.join("；")}`
    : "响应人工重新计算请求";

  return (
    <>
      <div className={`runtime-plan-columns${candidatePlan ? " has-candidate" : ""}`}>
        <PlanColumn
          label="当前执行方案"
          plan={activePlan}
          scenario={scenario}
          purpose={`这是已经确认并正在生效的安排，共调度 ${activePlan.assignments.length} 项保障任务；后端按这些资源、时段和路线推进任务状态。`}
        />
        {candidatePlan && (
          <PlanColumn
            candidate
            label="待确认候选方案"
            plan={candidatePlan}
            scenario={scenario}
            purpose={`${responseTarget}。系统保留 ${frozenTaskCount} 项已经发生的执行事实，只重新安排尚未开始的任务；采用前不会替换当前方案。`}
          />
        )}
      </div>

      {comparison && (
        <section className="plan-difference-section">
          <div className="plan-list-heading"><strong>候选相对当前方案的具体变化</strong><span>{comparison.changes.length} 项调整 · {comparison.unchangedTaskIds.length} 项保持不变</span></div>
          <div className="plan-difference-list">
            {changeWindow.items.map((change) => (
              <article key={change.taskId}>
                <div><strong>{change.taskId}</strong><span>{change.changedFields.map((field) => changeFieldLabels[field]).join("、")}</span></div>
                <p><span>当前</span>{assignmentSummary(change.activeAssignment)}</p>
                <p><span>候选</span>{assignmentSummary(change.candidateAssignment)}</p>
                {change.candidateUnassigned && <small>{unassignedReasonLabels[change.candidateUnassigned.reason] ?? "人工协调"}：{change.candidateUnassigned.detail}</small>}
              </article>
            ))}
            {comparison.changes.length === 0 && (
              <div className="plan-no-change"><CheckCircle2 size={20} /><span>本次复核未改变任务安排，候选仍经过独立约束检查并等待人工确认。</span></div>
            )}
          </div>
          {comparison.changes.length > 0 && (
            <PaginationControls
              label="候选方案变化"
              total={comparison.changes.length}
              page={changeWindow.page}
              pageSize={PLAN_CHANGE_PAGE_SIZE}
              onPageChange={setChangePage}
            />
          )}
        </section>
      )}
    </>
  );
}
