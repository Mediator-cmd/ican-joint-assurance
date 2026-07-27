import {
  AlertTriangle,
  ArrowRight,
  BusFront,
  CheckCircle2,
  CircleGauge,
  ClipboardList,
  Clock3,
  LayoutDashboard,
  MapPinned,
  Plane,
  RefreshCw,
  Route,
  ShieldCheck,
  UsersRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  Assignment,
  DemoPayload,
  ResourceMetric,
  ServiceTask,
  ViewKey,
} from "./types";

const taskTypeLabels: Record<string, string> = {
  wheelchair_transfer: "轮椅转运",
  escort: "陪同引导",
  shuttle_transfer: "摆渡转运",
  boarding_assistance: "登机协助",
};

const resourceTypeLabels: Record<string, string> = {
  shuttle_bus: "摆渡车",
  wheelchair: "轮椅",
  service_agent: "服务人员",
};

const passengerGroupLabels: Record<string, string> = {
  special_assistance: "特殊协助旅客",
  urgent_connection: "紧急中转旅客",
  general_assistance: "一般协助旅客",
};

const changeFieldLabels: Record<string, string> = {
  scheduled_departure: "计划起飞",
  gate_id: "登机口",
  deadline_at: "任务截止",
  destination_zone_id: "目的区域",
};

const unassignedReasonLabels: Record<string, string> = {
  no_compatible_resource: "无匹配资源",
  resource_unavailable: "资源不可用",
  no_route: "无可用路线",
  time_window: "时间窗冲突",
  priority_tradeoff: "优先级取舍",
};

const planStatusLabels: Record<string, string> = {
  executable: "全部可执行",
  partial: "部分可执行",
  invalid: "方案无效",
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatChangeValue(field: string, value: string): string {
  return field.includes("_at") || field === "scheduled_departure" ? formatTime(value) : value;
}

function LoadingState() {
  return (
    <main className="state-screen" aria-live="polite">
      <CircleGauge className="state-icon spin" />
      <p>正在载入仿真场景</p>
    </main>
  );
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <main className="state-screen" role="alert">
      <AlertTriangle className="state-icon error" />
      <p>{message}</p>
      <button className="command-button" onClick={retry}>
        <RefreshCw size={17} />
        重新载入
      </button>
    </main>
  );
}

function KpiCard({
  label,
  value,
  unit,
  note,
  tone,
}: {
  label: string;
  value: string | number;
  unit?: string;
  note: string;
  tone: "green" | "blue" | "orange" | "neutral";
}) {
  return (
    <article className={`kpi-card tone-${tone}`}>
      <span className="kpi-label">{label}</span>
      <div className="kpi-value">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      <span className="kpi-note">{note}</span>
    </article>
  );
}

function ResourceBar({ metric }: { metric: ResourceMetric }) {
  return (
    <div className="resource-row">
      <div className="resource-identity">
        <strong>{metric.resource_id}</strong>
        <span>{resourceTypeLabels[metric.resource_type] ?? metric.resource_type}</span>
      </div>
      <div className="utilization-track" aria-label={`${metric.resource_id} 利用率`}>
        <span style={{ width: `${metric.utilization_pct}%` }} />
      </div>
      <div className="resource-value">
        <strong>{metric.utilization_pct.toFixed(1)}%</strong>
        <span>{metric.busy_minutes} 分钟</span>
      </div>
    </div>
  );
}

function AssignmentRow({
  assignment,
  task,
  selected,
  onSelect,
}: {
  assignment: Assignment;
  task: ServiceTask;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={selected ? "selected-row" : undefined}
      onClick={onSelect}
      onKeyDown={(event) => event.key === "Enter" && onSelect()}
      tabIndex={0}
    >
      <td>
        <div className="task-cell">
          <strong>{assignment.task_id}</strong>
          <span>{taskTypeLabels[task.task_type] ?? task.task_type}</span>
        </div>
      </td>
      <td>
        <span className="priority-mark" data-priority={task.priority}>
          P{task.priority}
        </span>
      </td>
      <td>
        <strong>{assignment.resource_id}</strong>
        <span className="cell-subtext">
          {resourceTypeLabels[assignment.resource_type] ?? assignment.resource_type}
        </span>
      </td>
      <td>
        <div className="route-cell">
          <span>{assignment.origin_zone_id}</span>
          <ArrowRight size={14} />
          <span>{assignment.destination_zone_id}</span>
        </div>
      </td>
      <td className="tabular">
        {formatTime(assignment.service_started_at)}—{formatTime(assignment.service_ended_at)}
      </td>
      <td>
        <span className="status-badge success">
          <CheckCircle2 size={14} />
          可执行
        </span>
      </td>
    </tr>
  );
}

function UnassignedRow({
  task,
  reason,
  selected,
  onSelect,
}: {
  task: ServiceTask;
  reason: { reason: string; detail: string };
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={`unassigned-row${selected ? " selected-row" : ""}`}
      onClick={onSelect}
      onKeyDown={(event) => event.key === "Enter" && onSelect()}
      tabIndex={0}
    >
      <td>
        <div className="task-cell">
          <strong>{task.task_id}</strong>
          <span>{taskTypeLabels[task.task_type] ?? task.task_type}</span>
        </div>
      </td>
      <td>
        <span className="priority-mark" data-priority={task.priority}>P{task.priority}</span>
      </td>
      <td>
        <strong>未分配</strong>
        <span className="cell-subtext">
          {unassignedReasonLabels[reason.reason] ?? reason.reason}
        </span>
      </td>
      <td>
        <div className="route-cell">
          <span>{task.origin_zone_id}</span>
          <ArrowRight size={14} />
          <span>{task.destination_zone_id}</span>
        </div>
      </td>
      <td className="tabular">
        {formatTime(task.release_at)}—{formatTime(task.deadline_at)}
      </td>
      <td>
        <span className="status-badge warning">
          <AlertTriangle size={14} />
          待人工处置
        </span>
      </td>
    </tr>
  );
}

export default function App() {
  const [payload, setPayload] = useState<DemoPayload | null>(null);
  const [activeView, setActiveView] = useState<ViewKey>("baseline");
  const [selectedTaskId, setSelectedTaskId] = useState<string>("TASK-001");
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const loadData = useCallback(() => {
    setError(null);
    setPayload(null);
    fetch(`${import.meta.env.BASE_URL}demo-output.json?reload=${reloadToken}`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<DemoPayload>;
      })
      .then(setPayload)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法读取演示数据");
      });
  }, [reloadToken]);

  useEffect(() => loadData(), [loadData]);

  const retry = () => setReloadToken((current) => current + 1);

  const current = payload?.views[activeView];
  const taskMap = useMemo(
    () => new Map(current?.scenario.tasks.map((task) => [task.task_id, task]) ?? []),
    [current],
  );
  const selectedTask = taskMap.get(selectedTaskId);
  const selectedAssignment = current?.plan.assignments.find(
    (assignment) => assignment.task_id === selectedTaskId,
  );
  const selectedUnassigned = current?.plan.unassigned_tasks.find(
    (item) => item.task_id === selectedTaskId,
  );

  if (error) return <ErrorState message={`演示数据加载失败：${error}`} retry={retry} />;
  if (!payload || !current) return <LoadingState />;

  const metrics = current.plan.metrics;
  const baselineMetrics = payload.views.baseline.plan.metrics;
  const modeIsApplied = activeView !== "baseline";
  const isOptimized = activeView === "optimized";
  const criticalTaskCount = current.scenario.tasks.filter((task) => task.priority === 1).length;
  const completedCriticalTaskCount = Math.round(
    (metrics.critical_task_completion_rate_pct / 100) * criticalTaskCount,
  );
  const engineLabel = current.plan.algorithm.startsWith("cp_sat")
    ? "CP-SAT priority v1"
    : "FIFO baseline v1";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">联</div>
          <div>
            <strong>{payload.project.name}</strong>
            <span>JOINT ASSURANCE</span>
          </div>
        </div>

        <nav aria-label="页面导航">
          <a className="nav-item active" href="#overview">
            <LayoutDashboard size={18} />
            运行总览
          </a>
          <a className="nav-item" href="#events">
            <Plane size={18} />
            扰动事件
            <b>{payload.events.length}</b>
          </a>
          <a className="nav-item" href="#tasks">
            <ClipboardList size={18} />
            任务计划
            <b>{metrics.total_tasks}</b>
          </a>
          <a className="nav-item" href="#resources">
            <BusFront size={18} />
            保障资源
            <b>{metrics.resource_metrics.length}</b>
          </a>
          <a className="nav-item" href="#validation">
            <ShieldCheck size={18} />
            约束校验
            <b>{current.plan.violations.length}</b>
          </a>
        </nav>

        <div className="engine-state">
          <span className="eyebrow">计算引擎</span>
          <strong>{engineLabel}</strong>
          <span><i /> 规则引擎在线</span>
          <span className="engine-secondary">AI 解析器未接入</span>
        </div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div>
            <p className="eyebrow">AIRPORT ASSISTANCE CONTROL</p>
            <h1>特殊旅客保障运行台</h1>
          </div>
          <div className="topbar-actions">
            <span className="data-chip">
              <ShieldCheck size={15} />
              合成数据
            </span>
            <button className="icon-button" title="重新载入演示数据" onClick={retry}>
              <RefreshCw size={18} />
              <span className="sr-only">重新载入演示数据</span>
            </button>
          </div>
        </header>

        <section className="scenario-bar" aria-label="场景控制">
          <div className="scenario-name">
            <span className="live-dot" />
            <div>
              <span>当前场景</span>
              <strong>{current.scenario.name}</strong>
            </div>
          </div>
          <div className="scenario-meta">
            <Clock3 size={17} />
            <span>仿真窗口</span>
            <strong>
              {formatTime(current.scenario.window_start)}—{formatTime(current.scenario.window_end)}
            </strong>
          </div>
          <div className="mode-switch" role="group" aria-label="方案视图">
            <button
              className={activeView === "baseline" ? "active" : undefined}
              aria-pressed={activeView === "baseline"}
              onClick={() => setActiveView("baseline")}
            >
              扰动前 FIFO
            </button>
            <button
              className={activeView === "after_events_fifo" ? "active" : undefined}
              aria-pressed={activeView === "after_events_fifo"}
              onClick={() => setActiveView("after_events_fifo")}
            >
              事件后 FIFO
            </button>
            <button
              className={activeView === "optimized" ? "active optimized" : undefined}
              aria-pressed={activeView === "optimized"}
              onClick={() => setActiveView("optimized")}
            >
              CP-SAT 优化
            </button>
          </div>
        </section>

        <section className="kpi-grid" aria-label="关键指标">
          <KpiCard
            label="任务完成率"
            value={metrics.task_completion_rate_pct.toFixed(0)}
            unit="%"
            note={
              isOptimized
                ? `较 FIFO +${(metrics.task_completion_rate_pct - baselineMetrics.task_completion_rate_pct).toFixed(0)} 个百分点`
                : `${metrics.assigned_tasks}/${metrics.total_tasks} 项已分配`
            }
            tone="green"
          />
          <KpiCard
            label="关键任务完成率"
            value={metrics.critical_task_completion_rate_pct.toFixed(0)}
            unit="%"
            note={
              isOptimized
                ? `较 FIFO +${(metrics.critical_task_completion_rate_pct - baselineMetrics.critical_task_completion_rate_pct).toFixed(0)} 个百分点`
                : `${completedCriticalTaskCount}/${criticalTaskCount} 项 P1 已保障`
            }
            tone="blue"
          />
          <KpiCard
            label="平均等待"
            value={metrics.average_wait_minutes.toFixed(1)}
            unit="分钟"
            note={
              isOptimized
                ? `取舍代价 +${(metrics.average_wait_minutes - baselineMetrics.average_wait_minutes).toFixed(1)} 分钟`
                : `最长 ${metrics.max_wait_minutes} 分钟`
            }
            tone="neutral"
          />
          <KpiCard
            label="未完成任务"
            value={metrics.unassigned_tasks}
            unit="项"
            note={
              isOptimized
                ? `较 FIFO 减少 ${baselineMetrics.unassigned_tasks - metrics.unassigned_tasks} 项`
                : metrics.unassigned_tasks === 0
                  ? "当前窗口可行"
                  : "需要人工干预"
            }
            tone={metrics.unassigned_tasks === 0 ? "green" : "orange"}
          />
        </section>

        <div className="dashboard-grid">
          <section className="content-section assignment-section" id="tasks">
            <div className="section-heading">
              <div>
                <p className="eyebrow">ASSIGNMENT TIMELINE</p>
                <h2>任务执行序列</h2>
              </div>
              <span className={`plan-state ${current.plan.status}`}>
                <ShieldCheck size={15} />
                {planStatusLabels[current.plan.status] ?? current.plan.status}
              </span>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>优先级</th>
                    <th>分配资源</th>
                    <th>保障路线</th>
                    <th>服务时间</th>
                    <th>校验</th>
                  </tr>
                </thead>
                <tbody>
                  {current.plan.assignments.map((assignment) => (
                    <AssignmentRow
                      key={assignment.assignment_id}
                      assignment={assignment}
                      task={taskMap.get(assignment.task_id)!}
                      selected={assignment.task_id === selectedTaskId}
                      onSelect={() => setSelectedTaskId(assignment.task_id)}
                    />
                  ))}
                  {current.plan.unassigned_tasks.map((item) => (
                    <UnassignedRow
                      key={item.task_id}
                      task={taskMap.get(item.task_id)!}
                      reason={item}
                      selected={item.task_id === selectedTaskId}
                      onSelect={() => setSelectedTaskId(item.task_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {selectedTask && (selectedAssignment || selectedUnassigned) && (
              <div className="task-inspector" aria-live="polite">
                <div>
                  <span>当前任务</span>
                  <strong>{selectedTask.task_id}</strong>
                </div>
                <div>
                  <span>匿名旅客组</span>
                  <strong>
                    {passengerGroupLabels[selectedTask.passenger_group] ?? selectedTask.passenger_group}
                  </strong>
                </div>
                <div>
                  <span>资源移动</span>
                  <strong>
                    {selectedAssignment ? `${selectedAssignment.reposition_minutes} 分钟` : "未分配"}
                  </strong>
                </div>
                <div>
                  <span>服务时长</span>
                  <strong>{selectedTask.duration_minutes} 分钟</strong>
                </div>
                <div>
                  <span>任务截止</span>
                  <strong>{formatTime(selectedTask.deadline_at)}</strong>
                </div>
              </div>
            )}
          </section>

          <aside className="content-section event-section" id="events">
            <div className="section-heading compact">
              <div>
                <p className="eyebrow">DISRUPTION FEED</p>
                <h2>扰动事件</h2>
              </div>
              <span className={`event-mode ${modeIsApplied ? "applied" : "pending"}`}>
                {modeIsApplied ? "已应用" : "待注入"}
              </span>
            </div>
            <div className="event-list">
              {payload.events.map((event, index) => (
                <article className="event-item" key={event.event_id}>
                  <div className="event-time">{formatTime(event.occurred_at)}</div>
                  <div className="event-rail">
                    <span>{index + 1}</span>
                  </div>
                  <div className="event-copy">
                    <strong>{event.title}</strong>
                    <p>{event.detail}</p>
                    <span>{event.note}</span>
                  </div>
                </article>
              ))}
            </div>

            <div className="validation-box" id="validation">
              <ShieldCheck size={22} />
              <div>
                <strong>{current.plan.violations.length} 项硬约束违规</strong>
                <span>资源、容量、时间窗、路线已复核</span>
              </div>
            </div>
          </aside>
        </div>

        <div className="lower-grid">
          <section className="content-section" id="resources">
            <div className="section-heading compact">
              <div>
                <p className="eyebrow">RESOURCE LOAD</p>
                <h2>保障资源负载</h2>
              </div>
              <div className="overall-load">
                <span>总体</span>
                <strong>{metrics.overall_resource_utilization_pct.toFixed(1)}%</strong>
              </div>
            </div>
            <div className="resource-list">
              {metrics.resource_metrics.map((metric) => (
                <ResourceBar key={metric.resource_id} metric={metric} />
              ))}
            </div>
          </section>

          <section className="content-section change-section">
            <div className="section-heading compact">
              <div>
                <p className="eyebrow">CHANGE AUDIT</p>
                <h2>事件影响清单</h2>
              </div>
              <span className="change-count">{payload.changes.length} 处变化</span>
            </div>
            <div className="change-list">
              {payload.changes.map((change) => (
                <div className="change-row" key={`${change.entity_id}-${change.field}`}>
                  <div>
                    <strong>{change.entity_id}</strong>
                    <span>{changeFieldLabels[change.field] ?? change.field}</span>
                  </div>
                  <div className="change-values">
                    <span>{formatChangeValue(change.field, change.before)}</span>
                    <ArrowRight size={14} />
                    <strong>{formatChangeValue(change.field, change.after)}</strong>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="zone-strip" aria-label="区域态势">
          <div className="zone-title">
            <MapPinned size={19} />
            <div>
              <span className="eyebrow">ZONE STATUS</span>
              <strong>区域态势</strong>
            </div>
          </div>
          {current.scenario.zones.map((zone) => (
            <div className="zone-node" key={zone.zone_id}>
              <Route size={17} />
              <div>
                <strong>{zone.name}</strong>
                <span>{zone.zone_id}</span>
              </div>
            </div>
          ))}
          <div className="zone-node passengers">
            <UsersRound size={17} />
            <div>
              <strong>{current.scenario.tasks.length} 组</strong>
              <span>匿名保障任务</span>
            </div>
          </div>
        </section>

        <footer>
          <AlertTriangle size={16} />
          <span>{payload.project.safety_notice}</span>
          <strong>M2 · {current.plan.algorithm.startsWith("cp_sat") ? "CP-SAT 优化" : "FIFO 基线"}</strong>
        </footer>
      </main>
    </div>
  );
}
