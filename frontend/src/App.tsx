import {
  AlertTriangle,
  ArrowRight,
  BusFront,
  CheckCircle2,
  CircleGauge,
  CircleHelp,
  ClipboardList,
  Clock3,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  MapPinned,
  Plane,
  RefreshCw,
  Route,
  Server,
  ShieldCheck,
  HardDrive,
  UsersRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { loadDemoPayload } from "./api";
import RuntimeWorkspace from "./RuntimeWorkspace";
import { resolveSelectedId } from "./selection";

import type {
  Assignment,
  DemoDataSource,
  DemoEvent,
  DemoPayload,
  ResourceMetric,
  ServiceTask,
  ViewKey,
} from "./types";

type PageKey = "overview" | "tasks" | "events" | "resources" | "evidence";
type TaskFilter = "all" | "urgent" | "manual";

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

const passengerGroupLabels: Record<string, string> = {
  special_assistance: "特殊协助旅客",
  urgent_connection: "紧急中转旅客",
  general_assistance: "一般协助旅客",
};

const resourceStatusLabels: Record<string, string> = {
  available: "可调度",
  busy: "执行中",
  unavailable: "暂停使用",
};

const changeFieldLabels: Record<string, string> = {
  scheduled_departure: "计划起飞时间",
  gate_id: "登机口",
  deadline_at: "任务截止时间",
  destination_zone_id: "目的区域",
};

const unassignedReasonLabels: Record<string, string> = {
  no_compatible_resource: "资源能力不匹配",
  resource_unavailable: "匹配资源不可用",
  no_route: "保障路径不可达",
  time_window: "服务时间窗冲突",
  priority_tradeoff: "优先级资源取舍",
};

const planStatusLabels: Record<string, string> = {
  executable: "校验通过",
  partial: "部分待协调",
  invalid: "存在约束冲突",
};

const priorityLabels: Record<number, { label: string; detail: string }> = {
  1: { label: "紧急", detail: "优先保障" },
  2: { label: "重点", detail: "次级优先" },
  3: { label: "常规", detail: "顺序保障" },
};

const viewInfo: Record<ViewKey, { label: string; description: string }> = {
  baseline: {
    label: "原始计划",
    description: "扰动发生前的保障排班，用作方案比较基准。",
  },
  after_events_fifo: {
    label: "规则重排",
    description: "事件生效后，仍按任务到达顺序形成的对照方案。",
  },
  optimized: {
    label: "优化方案",
    description: "满足资源与时间约束，并优先保障紧急任务。",
  },
};

const pageInfo: Record<PageKey, { eyebrow: string; title: string; subtitle: string }> = {
  overview: {
    eyebrow: "运行决策工作台",
    title: "调度总览",
    subtitle: "集中研判当前方案、关键风险与协同处置重点。",
  },
  tasks: {
    eyebrow: "保障执行管理",
    title: "任务计划",
    subtitle: "复核各项保障任务的执行资源、通行路径、服务时段与协调状态。",
  },
  events: {
    eyebrow: "运行扰动管理",
    title: "事件影响",
    subtitle: "追踪航班扰动及其对任务时限、服务区域与资源衔接的影响。",
  },
  resources: {
    eyebrow: "保障资源管理",
    title: "资源态势",
    subtitle: "掌握人员与车辆的负载水平、可用时段、区域位置及后续任务序列。",
  },
  evidence: {
    eyebrow: "决策依据复核",
    title: "方案依据",
    subtitle: "核验方案计算链路、约束结果、版本信息与适用边界。",
  },
};

const navigation: Array<{
  key: PageKey;
  label: string;
  icon: typeof LayoutDashboard;
}> = [
  { key: "overview", label: "调度总览", icon: LayoutDashboard },
  { key: "tasks", label: "任务计划", icon: ClipboardList },
  { key: "events", label: "事件影响", icon: Plane },
  { key: "resources", label: "资源态势", icon: BusFront },
  { key: "evidence", label: "方案依据", icon: ShieldCheck },
];

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

function loadAssessment(utilization: number): { label: string; tone: string; advice: string } {
  if (utilization >= 75) {
    return { label: "高负载", tone: "high", advice: "建议预留替补资源，并减少非必要跨区移动。" };
  }
  if (utilization >= 50) {
    return { label: "负载偏高", tone: "medium", advice: "当前可执行，但临时任务插入余量有限。" };
  }
  return { label: "余量充足", tone: "low", advice: "仍具备承接临时保障任务的能力。" };
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

function PlanSwitch({
  activeView,
  onChange,
}: {
  activeView: ViewKey;
  onChange: (view: ViewKey) => void;
}) {
  return (
    <div className="plan-switch" role="group" aria-label="方案视图">
      {(Object.keys(viewInfo) as ViewKey[]).map((viewKey) => (
        <button
          key={viewKey}
          className={activeView === viewKey ? "active" : undefined}
          aria-pressed={activeView === viewKey}
          onClick={() => onChange(viewKey)}
        >
          <strong>{viewInfo[viewKey].label}</strong>
          {viewKey === "optimized" && <span>推荐</span>}
        </button>
      ))}
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
        <span className="status-badge success"><CheckCircle2 size={14} />已排定</span>
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
        <strong>待协调</strong>
        <span className="cell-subtext">{unassignedReasonLabels[reason.reason] ?? reason.reason}</span>
      </td>
      <td>
        <div className="route-cell">
          <span>{zoneNames.get(task.origin_zone_id) ?? task.origin_zone_id}</span>
          <ArrowRight size={14} />
          <span>{zoneNames.get(task.destination_zone_id) ?? task.destination_zone_id}</span>
        </div>
      </td>
      <td className="tabular">{formatTime(task.release_at)}—{formatTime(task.deadline_at)}</td>
      <td>
        <span className="status-badge warning"><AlertTriangle size={14} />待协调</span>
      </td>
    </tr>
  );
}

function ResourceRow({
  metric,
  selected,
  onSelect,
}: {
  metric: ResourceMetric;
  selected: boolean;
  onSelect: () => void;
}) {
  const assessment = loadAssessment(metric.utilization_pct);
  return (
    <button className={`resource-row${selected ? " selected" : ""}`} onClick={onSelect}>
      <div className="resource-identity">
        <strong>{metric.resource_id}</strong>
        <span>{resourceTypeLabels[metric.resource_type] ?? metric.resource_type}</span>
      </div>
      <div className="resource-load-column">
        <div className="utilization-track" aria-label={`${metric.resource_id} 负载率`}>
          <span style={{ width: `${metric.utilization_pct}%` }} />
        </div>
        <span>{metric.busy_minutes}/{metric.available_minutes} 分钟</span>
      </div>
      <div className="resource-value">
        <strong>{metric.utilization_pct.toFixed(1)}%</strong>
        <span className={`load-label ${assessment.tone}`}>{assessment.label}</span>
      </div>
    </button>
  );
}

function eventTypeLabel(event: DemoEvent): string {
  return event.event_type === "delay" ? "航班延误" : "登机口调整";
}

export default function App() {
  const [payload, setPayload] = useState<DemoPayload | null>(null);
  const [dataSource, setDataSource] = useState<DemoDataSource | null>(null);
  const [activePage, setActivePage] = useState<PageKey>("overview");
  const [activeView, setActiveView] = useState<ViewKey>("optimized");
  const [taskFilter, setTaskFilter] = useState<TaskFilter>("all");
  const [selectedTaskId, setSelectedTaskId] = useState("TASK-001");
  const [selectedEventId, setSelectedEventId] = useState("EVT-SIM102-DELAY");
  const [selectedResourceId, setSelectedResourceId] = useState("WC-01");
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);

  const reloadDemo = useCallback(() => {
    setRuntimeUnavailable(false);
    setReloadToken((value) => value + 1);
  }, []);

  const useOfflineFallback = useCallback(() => {
    setRuntimeUnavailable(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    setError(null);
    setPayload(null);
    setDataSource(null);
    void loadDemoPayload({ cacheBust: String(reloadToken), signal: controller.signal })
      .then((result) => {
        if (!active) return;
        setDataSource(result.source);
        setPayload(result.payload);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "数据服务暂不可用，请确认项目服务已启动后重试。",
        );
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [reloadToken]);

  const current = payload?.views[activeView];
  const taskMap = useMemo(
    () => new Map(current?.scenario.tasks.map((task) => [task.task_id, task]) ?? []),
    [current],
  );
  const zoneNames = useMemo(
    () => new Map(current?.scenario.zones.map((zone) => [zone.zone_id, zone.name]) ?? []),
    [current],
  );
  const flightNames = useMemo(
    () => new Map(current?.scenario.flights.map((flight) => [flight.flight_id, flight.display_code]) ?? []),
    [current],
  );

  const visibleAssignments = useMemo(() => {
    if (!current) return [];
    return current.plan.assignments.filter((assignment) => {
      const task = taskMap.get(assignment.task_id);
      if (!task) return false;
      if (taskFilter === "urgent") return task.priority === 1;
      if (taskFilter === "manual") return false;
      return true;
    });
  }, [current, taskFilter, taskMap]);

  const visibleUnassigned = useMemo(() => {
    if (!current) return [];
    return current.plan.unassigned_tasks.filter((item) => {
      const task = taskMap.get(item.task_id);
      if (!task) return false;
      if (taskFilter === "urgent") return task.priority === 1;
      return true;
    });
  }, [current, taskFilter, taskMap]);

  const visibleTaskIds = useMemo(
    () => [
      ...visibleAssignments.map((assignment) => assignment.task_id),
      ...visibleUnassigned.map((item) => item.task_id),
    ],
    [visibleAssignments, visibleUnassigned],
  );

  useEffect(() => {
    if (!current) return;
    const nextTaskId = resolveSelectedId(selectedTaskId, visibleTaskIds);
    if (nextTaskId !== selectedTaskId) setSelectedTaskId(nextTaskId);
  }, [current, selectedTaskId, visibleTaskIds]);

  const eventIds = useMemo(
    () => payload?.events.map((event) => event.event_id) ?? [],
    [payload],
  );

  useEffect(() => {
    if (!payload) return;
    const nextEventId = resolveSelectedId(selectedEventId, eventIds);
    if (nextEventId !== selectedEventId) setSelectedEventId(nextEventId);
  }, [eventIds, payload, selectedEventId]);

  const resourceIds = useMemo(
    () => current?.plan.metrics.resource_metrics.map((metric) => metric.resource_id) ?? [],
    [current],
  );

  useEffect(() => {
    if (!current) return;
    const nextResourceId = resolveSelectedId(selectedResourceId, resourceIds);
    if (nextResourceId !== selectedResourceId) setSelectedResourceId(nextResourceId);
  }, [current, resourceIds, selectedResourceId]);

  const selectedEvent = payload?.events.find((event) => event.event_id === selectedEventId)
    ?? payload?.events[0];
  const affectedChanges = useMemo(() => {
    if (!payload || !selectedEvent) return [];
    const fields = selectedEvent.event_type === "delay"
      ? new Set(["scheduled_departure", "deadline_at"])
      : new Set(["gate_id", "destination_zone_id"]);
    return payload.changes.filter((change) => fields.has(change.field));
  }, [payload, selectedEvent]);

  if (error) {
    return <ErrorState message={`演示数据加载失败：${error}`} retry={reloadDemo} />;
  }
  if (!payload || !current || !dataSource) return <LoadingState />;

  if (dataSource === "api" && !runtimeUnavailable) {
    return (
      <RuntimeWorkspace
        payload={payload}
        onReload={reloadDemo}
        onInitialUnavailable={useOfflineFallback}
      />
    );
  }

  const metrics = current.plan.metrics;
  const changedRuleMetrics = payload.views.after_events_fifo.plan.metrics;
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
  const currentPage = pageInfo[activePage];
  const selectedTask = taskMap.get(selectedTaskId);
  const selectedAssignment = current.plan.assignments.find(
    (assignment) => assignment.task_id === selectedTaskId,
  );
  const selectedUnassigned = current.plan.unassigned_tasks.find(
    (item) => item.task_id === selectedTaskId,
  );
  const hasVisibleSelection = visibleTaskIds.includes(selectedTaskId);
  const selectedResourceMetric = metrics.resource_metrics.find(
    (metric) => metric.resource_id === selectedResourceId,
  ) ?? metrics.resource_metrics[0];
  const selectedResource = current.scenario.resources.find(
    (resource) => resource.resource_id === selectedResourceMetric?.resource_id,
  );
  const selectedResourceAssignments = current.plan.assignments.filter(
    (assignment) => assignment.resource_id === selectedResourceMetric?.resource_id,
  );
  const resourceAssessment = selectedResourceMetric
    ? loadAssessment(selectedResourceMetric.utilization_pct)
    : null;
  const maxResourceMetric = metrics.resource_metrics.reduce<ResourceMetric | null>(
    (maximum, item) => !maximum || item.utilization_pct > maximum.utilization_pct ? item : maximum,
    null,
  );
  const modeIsApplied = activeView !== "baseline";
  const algorithmLabel = current.plan.algorithm.startsWith("cp_sat")
    ? "约束优化（CP-SAT）"
    : "顺序规则（FIFO）";
  const isApiSource = dataSource === "api" && !runtimeUnavailable;
  const SourceIcon = isApiSource ? Server : HardDrive;
  const sourceLabel = isApiSource ? "服务数据" : "离线只读";
  const sourceDetail = isApiSource
    ? "当前数据由本地业务服务提供"
    : runtimeUnavailable
      ? "运行服务暂不可用，当前为离线演示，时间不会推进"
      : "业务服务暂未连接，当前使用内置演示数据，时间不会推进";

  const decision = isOptimized
    ? {
        label: "推荐结果",
        title: criticalTaskShortfall === 0
          ? `紧急任务保障率 ${metrics.critical_task_completion_rate_pct.toFixed(0)}%`
          : `${criticalTaskShortfall} 项紧急任务仍需协调`,
        detail: metrics.unassigned_tasks === 0
          ? `${metrics.total_tasks} 项任务均已匹配执行资源，并通过独立约束校验。`
          : `${metrics.assigned_tasks}/${metrics.total_tasks} 项任务已排定，剩余任务需人工协调。`,
        tone: criticalTaskShortfall === 0 ? "recommended" : "attention",
      }
    : activeView === "after_events_fifo"
      ? {
          label: "风险提示",
          title: criticalTaskShortfall > 0
            ? `${criticalTaskShortfall} 项紧急任务未获保障`
            : "规则重排可覆盖当前任务",
          detail: "该结果反映航班扰动生效后，继续沿用顺序规则的直接影响。",
          tone: criticalTaskShortfall > 0 ? "attention" : "neutral",
        }
      : {
          label: "基准状态",
          title: metrics.unassigned_tasks > 0
            ? `${metrics.unassigned_tasks} 项任务处于待协调状态`
            : "原始计划已覆盖全部任务",
          detail: "该计划仅用于还原扰动发生前的资源分配基准。",
          tone: metrics.unassigned_tasks > 0 ? "attention" : "neutral",
        };

  const goToResource = (resourceId: string) => {
    setSelectedResourceId(resourceId);
    setActivePage("resources");
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">联</div>
          <div className="brand-copy">
            <strong>{payload.project.name}</strong>
            <span>JOINT ASSURANCE</span>
          </div>
        </div>

        <nav aria-label="主工作区">
          {navigation.map((item) => {
            const Icon = item.icon;
            const badge = item.key === "tasks"
              ? metrics.unassigned_tasks
              : item.key === "events"
                ? payload.events.length
                : item.key === "resources"
                  ? metrics.resource_metrics.length
                  : item.key === "evidence"
                    ? current.plan.violations.length
                    : null;
            return (
              <button
                key={item.key}
                className={`nav-item${activePage === item.key ? " active" : ""}`}
                aria-current={activePage === item.key ? "page" : undefined}
                onClick={() => setActivePage(item.key)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
                {badge !== null && <b>{badge}</b>}
              </button>
            );
          })}
        </nav>

        <div className={`sidebar-status${isApiSource ? "" : " is-fallback"}`}>
          <span className="eyebrow">当前运行状态</span>
          <strong>{viewInfo[activeView].label}</strong>
          <span><i /> {isApiSource ? "数据服务连接正常" : "离线演示 · 时间不会推进"}</span>
          <small>合成教学场景 · V{current.scenario.version}</small>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="page-heading">
            <p className="eyebrow">{currentPage.eyebrow}</p>
            <h1>{currentPage.title}</h1>
            <p>{currentPage.subtitle}</p>
          </div>
          <div className="topbar-actions">
            <span
              className={`source-chip ${isApiSource ? "api" : "fallback"}`}
              title={sourceDetail}
              aria-label={`数据来源：${sourceLabel}`}
              role="status"
            >
              <SourceIcon size={17} />
              {sourceLabel}
            </span>
            <span className="data-chip"><ShieldCheck size={15} />合成数据</span>
            <button className="icon-button" title="重新连接运行服务" onClick={reloadDemo}>
              <RefreshCw size={18} />
              <span className="sr-only">重新载入演示数据</span>
            </button>
          </div>
        </header>

        <section className="context-bar" aria-label="场景与方案控制">
          <div className="scenario-identity">
            <span className="live-dot" />
            <div>
              <span>当前场景</span>
              <strong>{current.scenario.name}</strong>
            </div>
          </div>
          <div className="scenario-window">
            <Clock3 size={16} />
            <span>{isApiSource ? "仿真窗口" : "离线演示"}</span>
            <strong>
              {isApiSource
                ? `${formatTime(current.scenario.window_start)}—${formatTime(current.scenario.window_end)}`
                : "时间不会推进"}
            </strong>
          </div>
          <PlanSwitch activeView={activeView} onChange={setActiveView} />
        </section>

        <div className="workspace-body">
          <section className={`workspace-page page-${activePage}`} key={activePage} aria-label={currentPage.title}>
            {activePage === "overview" && (
              <div className="overview-layout">
                <section className={`decision-panel ${decision.tone}`} aria-live="polite">
                  <div className="decision-symbol"><Lightbulb size={24} /></div>
                  <div className="decision-copy">
                    <span>{decision.label}</span>
                    <h2>{decision.title}</h2>
                    <p>{decision.detail}</p>
                  </div>
                  <div className="decision-facts">
                    <span><strong>{completedCriticalTaskCount}/{criticalTaskCount}</strong>紧急任务</span>
                    <span><strong>{metrics.unassigned_tasks}</strong>待协调</span>
                    <span><strong>{current.plan.violations.length}</strong>约束冲突</span>
                  </div>
                  <button className="primary-action" onClick={() => setActivePage("tasks")}>
                    复核任务计划<ArrowRight size={16} />
                  </button>
                </section>

                <section className="kpi-grid" aria-label="核心运行指标">
                  <KpiCard label="任务覆盖" value={`${metrics.assigned_tasks}/${metrics.total_tasks}`} note="已匹配资源与服务时段" tone="green" />
                  <KpiCard label="紧急任务保障" value={`${completedCriticalTaskCount}/${criticalTaskCount}`} note="按时完成的紧急任务" tone="blue" />
                  <KpiCard
                    label="平均等待"
                    value={metrics.average_wait_minutes.toFixed(1)}
                    unit="分钟"
                    note={isOptimized ? `较规则重排增加 ${extraWaitMinutes.toFixed(1)} 分钟` : `最长 ${metrics.max_wait_minutes} 分钟`}
                    tone="neutral"
                  />
                  <KpiCard
                    label="待协调任务"
                    value={metrics.unassigned_tasks}
                    unit="项"
                    note={isOptimized ? `较规则重排减少 ${changedRuleMetrics.unassigned_tasks - metrics.unassigned_tasks} 项` : "需进一步配置资源"}
                    tone={metrics.unassigned_tasks === 0 ? "green" : "orange"}
                  />
                </section>

                <div className="overview-lower">
                  <section className="workspace-section comparison-section">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">方案评估</p>
                        <h2>调度方案对照</h2>
                      </div>
                      <span className="section-note">点击方案可同步更新全工作台</span>
                    </div>
                    <div className="comparison-list">
                      {(Object.keys(viewInfo) as ViewKey[]).map((viewKey) => {
                        const viewMetrics = payload.views[viewKey].plan.metrics;
                        return (
                          <button
                            key={viewKey}
                            className={`comparison-item${activeView === viewKey ? " active" : ""}`}
                            onClick={() => setActiveView(viewKey)}
                          >
                            <div>
                              <strong>{viewInfo[viewKey].label}</strong>
                              <span>{viewInfo[viewKey].description}</span>
                            </div>
                            <dl>
                              <div><dt>覆盖</dt><dd>{viewMetrics.assigned_tasks}/{viewMetrics.total_tasks}</dd></div>
                              <div><dt>紧急保障</dt><dd>{viewMetrics.critical_task_completion_rate_pct.toFixed(0)}%</dd></div>
                              <div><dt>待协调</dt><dd>{viewMetrics.unassigned_tasks}</dd></div>
                              <div><dt>平均等待</dt><dd>{viewMetrics.average_wait_minutes.toFixed(1)} 分</dd></div>
                            </dl>
                          </button>
                        );
                      })}
                    </div>
                  </section>

                  <section className="workspace-section operations-brief">
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">运行摘要</p>
                        <h2>运行关注事项</h2>
                      </div>
                    </div>
                    <div className="brief-list">
                      <button onClick={() => setActivePage("events")}>
                        <Plane size={18} />
                        <span><strong>{payload.events.length} 项扰动已纳入评估</strong><small>{payload.changes.length} 个业务字段发生变化</small></span>
                        <ArrowRight size={15} />
                      </button>
                      <button onClick={() => maxResourceMetric && goToResource(maxResourceMetric.resource_id)}>
                        <BusFront size={18} />
                        <span><strong>{maxResourceMetric?.resource_id} 负载最高</strong><small>当前负载 {maxResourceMetric?.utilization_pct.toFixed(1)}%</small></span>
                        <ArrowRight size={15} />
                      </button>
                      <button onClick={() => setActivePage("evidence")}>
                        <ShieldCheck size={18} />
                        <span><strong>独立约束复核完成</strong><small>资源、容量、时间窗与路线均已检查</small></span>
                        <ArrowRight size={15} />
                      </button>
                    </div>
                  </section>
                </div>
              </div>
            )}

            {activePage === "tasks" && (
              <div className="tasks-layout">
                <section className="workspace-section task-board">
                  <div className="section-heading task-toolbar">
                    <div>
                      <p className="eyebrow">执行清单</p>
                      <h2>保障任务计划</h2>
                    </div>
                    <div className="filter-switch" role="group" aria-label="任务筛选">
                      {([
                        ["all", `全部 ${metrics.total_tasks}`],
                        ["urgent", `紧急 ${criticalTaskCount}`],
                        ["manual", `待协调 ${metrics.unassigned_tasks}`],
                      ] as Array<[TaskFilter, string]>).map(([filter, label]) => (
                        <button
                          key={filter}
                          className={taskFilter === filter ? "active" : undefined}
                          aria-pressed={taskFilter === filter}
                          onClick={() => setTaskFilter(filter)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <span className={`plan-state ${current.plan.status}`}>
                      <ShieldCheck size={15} />{planStatusLabels[current.plan.status] ?? current.plan.status}
                    </span>
                  </div>

                  <div className="table-wrap">
                    {visibleTaskIds.length > 0 ? (
                      <table>
                        <thead>
                          <tr>
                            <th>保障任务</th>
                            <th>等级</th>
                            <th>执行资源</th>
                            <th>保障路径</th>
                            <th>服务时段</th>
                            <th>状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visibleAssignments.map((assignment) => (
                            <AssignmentRow
                              key={assignment.assignment_id}
                              assignment={assignment}
                              task={taskMap.get(assignment.task_id)!}
                              zoneNames={zoneNames}
                              selected={assignment.task_id === selectedTaskId}
                              onSelect={() => setSelectedTaskId(assignment.task_id)}
                            />
                          ))}
                          {visibleUnassigned.map((item) => (
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
                    ) : (
                      <div className="empty-state">
                        <CheckCircle2 size={28} />
                        <strong>当前方案无待协调任务</strong>
                        <span>所有任务均已匹配执行资源和服务时段。</span>
                      </div>
                    )}
                  </div>
                  <p className="table-hint">移动端可横向查看完整路径和服务时段。</p>
                </section>

                <aside className="workspace-section task-detail">
                  {hasVisibleSelection && selectedTask ? (
                    <>
                      <div className="detail-heading">
                        <div>
                          <span>任务详情</span>
                          <h2>{selectedTask.task_id}</h2>
                          <p>{taskTypeLabels[selectedTask.task_type] ?? selectedTask.task_type}</p>
                        </div>
                        <span className={`status-badge ${selectedUnassigned ? "warning" : "success"}`}>
                          {selectedUnassigned ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
                          {selectedUnassigned ? "待协调" : "已排定"}
                        </span>
                      </div>
                      <div className="task-route-detail">
                        <div><span>起点</span><strong>{zoneNames.get(selectedTask.origin_zone_id) ?? selectedTask.origin_zone_id}</strong></div>
                        <ArrowRight size={17} />
                        <div><span>目的地</span><strong>{zoneNames.get(selectedTask.destination_zone_id) ?? selectedTask.destination_zone_id}</strong></div>
                      </div>
                      <dl className="detail-list">
                        <div><dt>关联航班</dt><dd>{flightNames.get(selectedTask.flight_id) ?? selectedTask.flight_id}</dd></div>
                        <div><dt>保障对象</dt><dd>{passengerGroupLabels[selectedTask.passenger_group] ?? selectedTask.passenger_group}</dd></div>
                        <div><dt>保障等级</dt><dd>{priorityLabels[selectedTask.priority]?.label ?? selectedTask.priority}</dd></div>
                        <div><dt>执行资源</dt><dd>{selectedAssignment?.resource_id ?? "待协调"}</dd></div>
                        <div><dt>服务时长</dt><dd>{selectedTask.duration_minutes} 分钟</dd></div>
                        <div><dt>完成时限</dt><dd>{formatTime(selectedTask.deadline_at)}</dd></div>
                      </dl>
                      {selectedUnassigned ? (
                        <div className="manual-note">
                          <AlertTriangle size={17} />
                          <div><strong>{unassignedReasonLabels[selectedUnassigned.reason] ?? selectedUnassigned.reason}</strong><p>{selectedUnassigned.detail}</p></div>
                        </div>
                      ) : selectedAssignment ? (
                        <button className="secondary-action" onClick={() => goToResource(selectedAssignment.resource_id)}>
                          查看执行资源<ArrowRight size={15} />
                        </button>
                      ) : null}
                    </>
                  ) : (
                    <div className="empty-state compact"><ListChecks size={26} /><strong>选择任务查看详情</strong></div>
                  )}
                </aside>
              </div>
            )}

            {activePage === "events" && selectedEvent && (
              <div className="events-layout">
                <section className="workspace-section event-catalog">
                  <div className="section-heading">
                    <div><p className="eyebrow">事件清单</p><h2>{payload.events.length} 项运行扰动</h2></div>
                    <span className={`event-state ${modeIsApplied ? "applied" : "pending"}`}>
                      {modeIsApplied ? "已纳入当前方案" : "基准计划未纳入"}
                    </span>
                  </div>
                  <div className="event-selector">
                    {payload.events.map((event, index) => (
                      <button
                        key={event.event_id}
                        className={selectedEvent.event_id === event.event_id ? "active" : undefined}
                        onClick={() => setSelectedEventId(event.event_id)}
                      >
                        <span className="event-index">{String(index + 1).padStart(2, "0")}</span>
                        <div><strong>{eventTypeLabel(event)}</strong><span>{formatTime(event.occurred_at)} · {event.detail}</span></div>
                        <ArrowRight size={15} />
                      </button>
                    ))}
                  </div>
                </section>

                <section className="workspace-section event-analysis">
                  <div className="event-hero">
                    <div className="event-symbol"><Plane size={23} /></div>
                    <div>
                      <span>{eventTypeLabel(selectedEvent)}</span>
                      <h2>{selectedEvent.detail}</h2>
                      <p>{selectedEvent.note ?? "无补充说明"}</p>
                    </div>
                  </div>
                  <div className="impact-summary">
                    <article><span>发生时间</span><strong>{formatTime(selectedEvent.occurred_at)}</strong></article>
                    <article><span>影响字段</span><strong>{affectedChanges.length} 项</strong></article>
                    <article><span>关联任务</span><strong>{affectedChanges.filter((change) => change.entity_type === "task").length} 项</strong></article>
                    <article><span>当前状态</span><strong>{modeIsApplied ? "已重排" : "待应用"}</strong></article>
                  </div>
                  <div className="event-interpretation">
                    <h3>影响判定</h3>
                    <p>
                      {selectedEvent.event_type === "delay"
                        ? "航班时间顺延会同步放宽未锁定保障任务的完成时限，并触发资源时序复核。"
                        : "登机口调整会改变相关任务的目的区域，需要重新计算资源移动路径与衔接时间。"}
                    </p>
                  </div>
                </section>

                <section className="workspace-section change-audit">
                  <div className="section-heading">
                    <div><p className="eyebrow">影响明细</p><h2>字段变更记录</h2></div>
                    <span className="change-count">{affectedChanges.length} 处</span>
                  </div>
                  <div className="change-list">
                    {affectedChanges.map((change) => (
                      <div className="change-row" key={`${change.entity_id}-${change.field}`}>
                        <div><strong>{change.entity_id}</strong><span>{changeFieldLabels[change.field] ?? change.field}</span></div>
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
            )}

            {activePage === "resources" && selectedResourceMetric && selectedResource && resourceAssessment && (
              <div className="resources-layout">
                <section className="workspace-section resource-catalog">
                  <div className="section-heading">
                    <div><p className="eyebrow">资源清单</p><h2>保障资源负载</h2></div>
                    <div className="overall-load"><span>综合负载</span><strong>{metrics.overall_resource_utilization_pct.toFixed(1)}%</strong></div>
                  </div>
                  <div className="resource-list">
                    {metrics.resource_metrics.map((metric) => (
                      <ResourceRow
                        key={metric.resource_id}
                        metric={metric}
                        selected={metric.resource_id === selectedResourceMetric.resource_id}
                        onSelect={() => setSelectedResourceId(metric.resource_id)}
                      />
                    ))}
                  </div>
                </section>

                <section className="workspace-section resource-detail">
                  <div className="resource-detail-head">
                    <div className="resource-symbol"><BusFront size={23} /></div>
                    <div><span>{resourceTypeLabels[selectedResource.resource_type] ?? selectedResource.resource_type}</span><h2>{selectedResource.resource_id}</h2></div>
                    <span className={`load-pill ${resourceAssessment.tone}`}>{resourceAssessment.label}</span>
                  </div>
                  <div className="resource-gauge">
                    <div><span style={{ width: `${selectedResourceMetric.utilization_pct}%` }} /></div>
                    <strong>{selectedResourceMetric.utilization_pct.toFixed(1)}%</strong>
                    <span>当前方案负载</span>
                  </div>
                  <dl className="detail-list resource-facts">
                    <div><dt>运行状态</dt><dd>{resourceStatusLabels[selectedResource.status] ?? selectedResource.status}</dd></div>
                    <div><dt>当前位置</dt><dd>{zoneNames.get(selectedResource.current_zone_id) ?? selectedResource.current_zone_id}</dd></div>
                    <div><dt>待命区域</dt><dd>{zoneNames.get(selectedResource.home_zone_id) ?? selectedResource.home_zone_id}</dd></div>
                    <div><dt>可用时段</dt><dd>{formatTime(selectedResource.available_from)}—{formatTime(selectedResource.available_to)}</dd></div>
                    <div><dt>服务容量</dt><dd>{selectedResource.capacity} 人/组</dd></div>
                    <div><dt>忙碌时间</dt><dd>{selectedResourceMetric.busy_minutes} 分钟</dd></div>
                    <div><dt>可用余量</dt><dd>{selectedResourceMetric.available_minutes - selectedResourceMetric.busy_minutes} 分钟</dd></div>
                    <div><dt>后续任务</dt><dd>{selectedResourceAssignments.length} 项</dd></div>
                  </dl>
                  <div className={`resource-advice ${resourceAssessment.tone}`}>
                    <CircleGauge size={18} />
                    <div><strong>负载评估</strong><p>{resourceAssessment.advice}</p></div>
                  </div>
                </section>

                <section className="workspace-section resource-sequence">
                  <div className="section-heading">
                    <div><p className="eyebrow">执行序列</p><h2>已分配任务</h2></div>
                    <span className="section-note">按服务开始时间排序</span>
                  </div>
                  <div className="sequence-list">
                    {selectedResourceAssignments.length > 0 ? selectedResourceAssignments.map((assignment, index) => (
                      <button
                        key={assignment.assignment_id}
                        onClick={() => { setSelectedTaskId(assignment.task_id); setActivePage("tasks"); }}
                      >
                        <span className="sequence-index">{index + 1}</span>
                        <div>
                          <strong>{assignment.task_id} · {taskTypeLabels[taskMap.get(assignment.task_id)?.task_type ?? ""]}</strong>
                          <span>{formatTime(assignment.service_started_at)}—{formatTime(assignment.service_ended_at)}</span>
                        </div>
                        <ArrowRight size={15} />
                      </button>
                    )) : (
                      <div className="empty-state compact"><CheckCircle2 size={25} /><strong>当前无已分配任务</strong></div>
                    )}
                  </div>
                </section>

                <section className="workspace-section zone-overview">
                  <div className="section-heading"><div><p className="eyebrow">区域覆盖</p><h2>保障区域</h2></div></div>
                  <div className="zone-list">
                    {current.scenario.zones.map((zone) => (
                      <article key={zone.zone_id} className={zone.zone_id === selectedResource.current_zone_id ? "active" : undefined}>
                        <MapPinned size={17} />
                        <div><strong>{zone.name}</strong><span>{zone.zone_id === selectedResource.current_zone_id ? "资源当前位置" : zone.zone_id}</span></div>
                      </article>
                    ))}
                  </div>
                </section>
              </div>
            )}

            {activePage === "evidence" && (
              <div className="evidence-layout">
                <section className="workspace-section validation-panel">
                  <div className="validation-summary">
                    <div className={`validation-symbol ${current.plan.violations.length ? "warning" : "success"}`}>
                      {current.plan.violations.length ? <AlertTriangle size={25} /> : <ShieldCheck size={25} />}
                    </div>
                    <div>
                      <span>独立约束复核</span>
                      <h2>{current.plan.violations.length === 0 ? "当前方案通过全部硬约束检查" : `发现 ${current.plan.violations.length} 项约束冲突`}</h2>
                      <p>校验过程独立于方案生成，不以模型解释替代结构化检查。</p>
                    </div>
                  </div>
                  <div className="constraint-grid">
                    {[
                      ["资源互斥", "同一资源不得在同一时段执行多项任务"],
                      ["能力与容量", "资源类型及服务容量必须满足任务要求"],
                      ["任务时间窗", "移动和服务必须在任务截止时间前完成"],
                      ["路径可达性", "区域间必须存在可计算的移动路径"],
                    ].map(([title, description]) => (
                      <article key={title}>
                        <CheckCircle2 size={18} />
                        <div><strong>{title}</strong><span>{description}</span></div>
                        <b>{current.plan.violations.length === 0 ? "通过" : "复核"}</b>
                      </article>
                    ))}
                  </div>
                </section>

                <section className="workspace-section process-panel">
                  <div className="section-heading"><div><p className="eyebrow">计算链路</p><h2>方案形成过程</h2></div></div>
                  <div className="process-flow">
                    {[
                      ["01", "场景校验", "核对航班、任务、资源与区域引用"],
                      ["02", "事件应用", "从不可变基线重放结构化事件"],
                      ["03", "调度计算", "生成资源分配和服务时序"],
                      ["04", "约束复核", "独立检查容量、重叠、路线和时间窗"],
                      ["05", "人工确认", "由保障人员复核并决定是否采用"],
                    ].map(([index, title, description], stepIndex) => (
                      <div className="process-step" key={index}>
                        <span>{index}</span>
                        <div><strong>{title}</strong><p>{description}</p></div>
                        {stepIndex < 4 && <ArrowRight size={15} />}
                      </div>
                    ))}
                  </div>
                </section>

                <section className="workspace-section basis-panel">
                  <div className="section-heading"><div><p className="eyebrow">版本与算法</p><h2>复核信息</h2></div></div>
                  <dl className="basis-list">
                    <div><dt>计算方式</dt><dd>{algorithmLabel}</dd></div>
                    <div><dt>场景版本</dt><dd>V{current.scenario.version}</dd></div>
                    <div><dt>方案编号</dt><dd>{current.plan.plan_id}</dd></div>
                    <div><dt>数据分类</dt><dd>{payload.project.data_classification === "synthetic" ? "合成教学数据" : "匿名化回放数据"}</dd></div>
                    <div><dt>方案状态</dt><dd>{planStatusLabels[current.plan.status] ?? current.plan.status}</dd></div>
                    <div><dt>人工确认</dt><dd>必需</dd></div>
                  </dl>
                </section>

                <section className="workspace-section glossary-panel">
                  <div className="section-heading"><div><p className="eyebrow">术语说明</p><h2>指标口径</h2></div></div>
                  <div className="glossary-list">
                    <article><strong>规则重排</strong><p>按任务到达顺序分配资源，用作常规人工规则的对照。</p></article>
                    <article><strong>优化方案</strong><p>在硬约束内优先保障紧急任务，再降低未完成数与等待成本。</p></article>
                    <article><strong>平均等待</strong><p>已分配任务从可开始服务到实际开始服务的平均间隔。</p></article>
                    <article><strong>资源负载</strong><p>资源移动与服务时间占可用时间的比例，用于判断调度余量。</p></article>
                  </div>
                </section>
              </div>
            )}
          </section>
        </div>

        <footer className="safety-footer">
          <AlertTriangle size={15} />
          <span>{payload.project.safety_notice}</span>
          <strong>结果需人工确认</strong>
        </footer>
      </main>
    </div>
  );
}
