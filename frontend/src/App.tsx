import {
  AlertTriangle,
  ArrowRight,
  BusFront,
  CheckCircle2,
  CircleGauge,
  CircleHelp,
  ClipboardList,
  Clock3,
  Lightbulb,
  ListChecks,
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

const priorityLabels: Record<number, { label: string; detail: string }> = {
  1: { label: "紧急", detail: "最先保障" },
  2: { label: "优先", detail: "随后保障" },
  3: { label: "常规", detail: "正常排队" },
};

const viewInfo: Record<
  ViewKey,
  { step: string; label: string; shortLabel: string; description: string }
> = {
  baseline: {
    step: "1",
    label: "原保障安排",
    shortLabel: "原安排",
    description: "查看航班发生变化前，人员和车辆原本怎样安排。",
  },
  after_events_fifo: {
    step: "2",
    label: "航班变化后",
    shortLabel: "变化后",
    description: "保留原来的先到先服务规则，看看延误和换登机口会造成什么影响。",
  },
  optimized: {
    step: "3",
    label: "系统优化建议",
    shortLabel: "系统建议",
    description: "在不违反资源、容量、路线和截止时间的前提下，优先保住紧急任务。",
  },
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
  zoneNames,
  selected,
  onSelect,
}: {
  assignment: Assignment;
  task: ServiceTask;
  zoneNames: Map<string, string>;
  selected: boolean;
  onSelect: () => void;
}) {
  const priority = priorityLabels[task.priority] ?? {
    label: `等级 ${task.priority}`,
    detail: "保障等级",
  };
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
        <span className="priority-mark" data-priority={task.priority}>{priority.label}</span>
        <span className="cell-subtext">{priority.detail}</span>
      </td>
      <td>
        <strong>{assignment.resource_id}</strong>
        <span className="cell-subtext">
          {resourceTypeLabels[assignment.resource_type] ?? assignment.resource_type}
        </span>
      </td>
      <td>
        <div className="route-cell">
          <span>{zoneNames.get(assignment.origin_zone_id) ?? assignment.origin_zone_id}</span>
          <ArrowRight size={14} />
          <span>{zoneNames.get(assignment.destination_zone_id) ?? assignment.destination_zone_id}</span>
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
  zoneNames,
  selected,
  onSelect,
}: {
  task: ServiceTask;
  reason: { reason: string; detail: string };
  zoneNames: Map<string, string>;
  selected: boolean;
  onSelect: () => void;
}) {
  const priority = priorityLabels[task.priority] ?? {
    label: `等级 ${task.priority}`,
    detail: "保障等级",
  };
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
        <span className="priority-mark" data-priority={task.priority}>{priority.label}</span>
        <span className="cell-subtext">{priority.detail}</span>
      </td>
      <td>
        <strong>未分配</strong>
        <span className="cell-subtext">
          {unassignedReasonLabels[reason.reason] ?? reason.reason}
        </span>
      </td>
      <td>
        <div className="route-cell">
          <span>{zoneNames.get(task.origin_zone_id) ?? task.origin_zone_id}</span>
          <ArrowRight size={14} />
          <span>{zoneNames.get(task.destination_zone_id) ?? task.destination_zone_id}</span>
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
  const [activeView, setActiveView] = useState<ViewKey>("optimized");
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
  const zoneNames = useMemo(
    () => new Map(current?.scenario.zones.map((zone) => [zone.zone_id, zone.name]) ?? []),
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
  const changedRuleMetrics = payload.views.after_events_fifo.plan.metrics;
  const modeIsApplied = activeView !== "baseline";
  const isOptimized = activeView === "optimized";
  const criticalTaskCount = current.scenario.tasks.filter((task) => task.priority === 1).length;
  const completedCriticalTaskCount = Math.round(
    (metrics.critical_task_completion_rate_pct / 100) * criticalTaskCount,
  );
  const criticalTaskShortfall = Math.max(criticalTaskCount - completedCriticalTaskCount, 0);
  const extraWaitMinutes = Math.max(
    metrics.average_wait_minutes - changedRuleMetrics.average_wait_minutes,
    0,
  );
  const algorithmLabel = current.plan.algorithm.startsWith("cp_sat")
    ? "约束优化（CP-SAT）"
    : "按到达顺序安排（FIFO）";

  const decision = isOptimized
    ? {
        eyebrow: "系统建议",
        title:
          criticalTaskShortfall === 0
            ? "建议采用：全部紧急任务都能按时保障"
            : `仍有 ${criticalTaskShortfall} 项紧急任务需要人工处理`,
        detail:
          metrics.unassigned_tasks === 0
            ? `系统已为 ${metrics.total_tasks} 项任务匹配人员或车辆，并完成资源冲突复核。`
            : `系统已安排 ${metrics.assigned_tasks}/${metrics.total_tasks} 项任务，橙色任务仍需人工确认。`,
        tone: criticalTaskShortfall === 0 ? "recommended" : "attention",
      }
    : activeView === "after_events_fifo"
      ? {
          eyebrow: "变化后的风险",
          title:
            criticalTaskShortfall > 0
              ? `按原规则排队，会漏掉 ${criticalTaskShortfall} 项紧急任务`
              : "按原规则排队，当前任务仍可完成",
          detail: "这是航班延误和登机口变化生效后的直接结果，可继续查看系统优化建议。",
          tone: criticalTaskShortfall > 0 ? "attention" : "neutral",
        }
      : {
          eyebrow: "原始参照",
          title:
            metrics.unassigned_tasks > 0
              ? `原安排中有 ${metrics.unassigned_tasks} 项任务没有执行资源`
              : "原安排中的任务均已找到执行资源",
          detail: "这一页只用于了解变化发生前的安排；下一步请查看航班变化后的结果。",
          tone: metrics.unassigned_tasks > 0 ? "attention" : "neutral",
        };

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
          <a className="nav-item active" href="#guide">
            <CircleHelp size={18} />
            怎么使用
          </a>
          <a className="nav-item" href="#decision">
            <Lightbulb size={18} />
            当前建议
          </a>
          <a className="nav-item" href="#tasks">
            <ClipboardList size={18} />
            具体安排
            <b>{metrics.total_tasks}</b>
          </a>
          <a className="nav-item" href="#events">
            <Plane size={18} />
            发生了什么
            <b>{payload.events.length}</b>
          </a>
          <a className="nav-item" href="#resources">
            <BusFront size={18} />
            人员与车辆
            <b>{metrics.resource_metrics.length}</b>
          </a>
        </nav>

        <div className="engine-state">
          <span className="eyebrow">当前演示</span>
          <strong>内置教学场景</strong>
          <span><i /> 计算与校验可用</span>
          <span className="engine-secondary">所有结果仍需人工确认</span>
        </div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div>
            <p className="eyebrow">特殊旅客联合保障</p>
            <h1>航班有变化，人员和车辆怎么重新安排？</h1>
            <p className="page-subtitle">联保智调会计算可执行方案，并把需要人工处理的任务直接标出来。</p>
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

        <section className="welcome-panel" id="guide" aria-labelledby="guide-title">
          <div className="welcome-heading">
            <span className="section-number">使用说明</span>
            <div>
              <h2 id="guide-title">第一次使用，只看这三步</h2>
              <p>不需要理解算法。先比较三个结果，再按系统建议处理橙色任务。</p>
            </div>
          </div>
          <div className="quick-start">
            <article>
              <span>1</span>
              <div><strong>看原安排</strong><p>了解变化发生前怎样分配。</p></div>
            </article>
            <article>
              <span>2</span>
              <div><strong>看变化后</strong><p>确认延误、换登机口带来的风险。</p></div>
            </article>
            <article>
              <span>3</span>
              <div><strong>看系统建议</strong><p>确认推荐方案并处理异常项。</p></div>
            </article>
          </div>
          <div className="system-proof">
            <ShieldCheck size={18} />
            <span><strong>它不是聊天回答：</strong>每项建议都落实到具体人员或车辆、地点、服务时间，并再次检查冲突。</span>
          </div>
        </section>

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
            {(Object.keys(viewInfo) as ViewKey[]).map((viewKey) => (
              <button
                key={viewKey}
                className={`${activeView === viewKey ? "active" : ""}${viewKey === "optimized" ? " optimized" : ""}`}
                aria-pressed={activeView === viewKey}
                onClick={() => setActiveView(viewKey)}
              >
                <span>{viewInfo[viewKey].step}</span>
                <strong>{viewInfo[viewKey].label}</strong>
                {viewKey === "optimized" && <em>推荐</em>}
              </button>
            ))}
          </div>
          <p className="mode-explanation" aria-live="polite">
            <strong>当前查看：</strong>{viewInfo[activeView].description}
          </p>
        </section>

        <section className={`decision-card ${decision.tone}`} id="decision" aria-live="polite">
          <div className="decision-icon"><Lightbulb size={25} /></div>
          <div className="decision-copy">
            <span>{decision.eyebrow}</span>
            <h2>{decision.title}</h2>
            <p>{decision.detail}</p>
          </div>
          <div className="decision-evidence" aria-label="结论依据">
            <span><strong>{completedCriticalTaskCount}/{criticalTaskCount}</strong> 紧急任务按时保障</span>
            <span><strong>{metrics.unassigned_tasks}</strong> 项需要人工处理</span>
            <span><strong>{current.plan.violations.length}</strong> 个资源或时间冲突</span>
          </div>
          {isOptimized ? (
            <a className="decision-action secondary" href="#tasks">查看具体安排 <ArrowRight size={16} /></a>
          ) : (
            <button className="decision-action" onClick={() => setActiveView("optimized")}>查看系统建议 <ArrowRight size={16} /></button>
          )}
        </section>

        <section className="kpi-grid" aria-label="关键指标">
          <KpiCard
            label="已安排任务"
            value={`${metrics.assigned_tasks}/${metrics.total_tasks}`}
            note="已匹配执行资源和服务时间"
            tone="green"
          />
          <KpiCard
            label="紧急任务按时保障"
            value={`${completedCriticalTaskCount}/${criticalTaskCount}`}
            note="紧急任务会最先分配资源"
            tone="blue"
          />
          <KpiCard
            label="平均等待"
            value={metrics.average_wait_minutes.toFixed(1)}
            unit="分钟"
            note={
              isOptimized
                ? `为保住紧急任务，平均增加 ${extraWaitMinutes.toFixed(1)} 分钟`
                : `最长 ${metrics.max_wait_minutes} 分钟`
            }
            tone="neutral"
          />
          <KpiCard
            label="需要人工处理"
            value={metrics.unassigned_tasks}
            unit="项"
            note={
              isOptimized
                ? `较原规则减少 ${changedRuleMetrics.unassigned_tasks - metrics.unassigned_tasks} 项`
                : metrics.unassigned_tasks === 0
                  ? "当前窗口可行"
                  : "请查看橙色任务及原因"
            }
            tone={metrics.unassigned_tasks === 0 ? "green" : "orange"}
          />
        </section>

        <div className="dashboard-grid">
          <section className="content-section assignment-section" id="tasks">
            <div className="section-heading">
              <div>
                <p className="eyebrow">具体执行安排</p>
                <h2><ListChecks size={18} /> 每项任务由谁、何时完成</h2>
                <p className="section-description">点击任意一行，可在表格下方查看这项任务的详细信息。</p>
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
                    <th>保障任务</th>
                    <th>紧急程度</th>
                    <th>由谁执行</th>
                    <th>从哪里到哪里</th>
                    <th>什么时候服务</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {current.plan.assignments.map((assignment) => (
                    <AssignmentRow
                      key={assignment.assignment_id}
                      assignment={assignment}
                      task={taskMap.get(assignment.task_id)!}
                      zoneNames={zoneNames}
                      selected={assignment.task_id === selectedTaskId}
                      onSelect={() => setSelectedTaskId(assignment.task_id)}
                    />
                  ))}
                  {current.plan.unassigned_tasks.map((item) => (
                    <UnassignedRow
                      key={item.task_id}
                      task={taskMap.get(item.task_id)!}
                      reason={item}
                      zoneNames={zoneNames}
                      selected={item.task_id === selectedTaskId}
                      onSelect={() => setSelectedTaskId(item.task_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="table-hint">在手机上可左右滑动查看“从哪里到哪里”和“什么时候服务”。</p>

            {selectedTask && (selectedAssignment || selectedUnassigned) && (
              <div className="task-inspector" aria-live="polite">
                <div>
                  <span>当前任务</span>
                  <strong>{selectedTask.task_id}</strong>
                </div>
                <div>
                  <span>服务对象</span>
                  <strong>
                    {passengerGroupLabels[selectedTask.passenger_group] ?? selectedTask.passenger_group}
                  </strong>
                </div>
                <div>
                  <span>人员/车辆前往起点</span>
                  <strong>
                    {selectedAssignment ? `${selectedAssignment.reposition_minutes} 分钟` : "未分配"}
                  </strong>
                </div>
                <div>
                  <span>服务时长</span>
                  <strong>{selectedTask.duration_minutes} 分钟</strong>
                </div>
                <div>
                  <span>必须在此时间前完成</span>
                  <strong>{formatTime(selectedTask.deadline_at)}</strong>
                </div>
              </div>
            )}
          </section>

          <aside className="content-section event-section" id="events">
            <div className="section-heading compact">
              <div>
                <p className="eyebrow">场景中发生的变化</p>
                <h2>发生了什么</h2>
                <p className="section-description">这些变化会影响任务的目的地、截止时间或服务顺序。</p>
              </div>
              <span className={`event-mode ${modeIsApplied ? "applied" : "pending"}`}>
                {modeIsApplied ? "已计入当前安排" : "原安排未计入"}
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
                <strong>
                  {current.plan.violations.length === 0
                    ? "检查通过：没有发现资源或时间冲突"
                    : `发现 ${current.plan.violations.length} 个需要人工处理的冲突`}
                </strong>
                <span>已检查同一资源重复占用、容量不足、超时和路线不可达。</span>
              </div>
            </div>
          </aside>
        </div>

        <div className="lower-grid">
          <section className="content-section" id="resources">
            <div className="section-heading compact">
              <div>
                <p className="eyebrow">执行资源</p>
                <h2>人员和车辆忙碌程度</h2>
                <p className="section-description">忙碌程度越高，临时插入新任务的余量越小。</p>
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
                <p className="eyebrow">变化记录</p>
                <h2>变化影响了什么</h2>
                <p className="section-description">保留前后对比，方便人工追溯为什么要重新安排。</p>
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
              <span className="eyebrow">地点参考</span>
              <strong>任务地点</strong>
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

        <details className="advanced-panel">
          <summary><CircleHelp size={18} /> 专业信息：算法、版本与校验说明</summary>
          <div className="advanced-content">
            <p>这里用于答辩和复核。日常查看时，只需阅读上方的系统建议和具体安排。</p>
            <div className="advanced-grid">
              <article>
                <span>当前计算方式</span>
                <strong>{algorithmLabel}</strong>
                <p>{isOptimized ? "先尽可能保障紧急任务，再减少未完成任务和等待时间。" : "按任务出现的先后顺序尝试安排，作为对照方案。"}</p>
              </article>
              <article>
                <span>可追溯依据</span>
                <strong>资源、地点、时间、路线</strong>
                <p>每项任务都保存执行资源、行程、服务时间和未完成原因。</p>
              </article>
              <article>
                <span>校验范围</span>
                <strong>容量、可用时间、路线与重复占用</strong>
                <p>生成方案后会独立复核，发现冲突会明确显示，不会伪装为可执行。</p>
              </article>
              <article>
                <span>场景版本</span>
                <strong>V{current.scenario.version} · {current.plan.plan_id}</strong>
                <p>当前为{payload.project.data_classification === "synthetic" ? "合成教学数据" : "匿名化回放数据"}，用于演示和复盘。</p>
              </article>
            </div>
          </div>
        </details>

        <footer>
          <AlertTriangle size={16} />
          <span>{payload.project.safety_notice}</span>
          <strong>结果需人工确认</strong>
        </footer>
      </main>
    </div>
  );
}
