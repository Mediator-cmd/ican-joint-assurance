import {
  AlertTriangle,
  ArrowRight,
  BusFront,
  CheckCircle2,
  CircleGauge,
  ClipboardList,
  Clock3,
  LayoutDashboard,
  ListChecks,
  MapPinned,
  Pause,
  Plane,
  Play,
  RefreshCw,
  RotateCcw,
  Route,
  Server,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import PaginationControls from "./PaginationControls";
import { includesNormalizedQuery, pageForIndex, paginateItems } from "./pagination";
import { resolveSelectedId } from "./selection";
import RuntimePlanDetails, {
  runtimePlanAlgorithmLabel,
  runtimePlanFacts,
} from "./RuntimePlanDetails";
import RuntimeEventAssistant from "./RuntimeEventAssistant";
import RuntimePlanExplanation from "./RuntimePlanExplanation";
import RuntimeSpatialAssistant from "./RuntimeSpatialAssistant";
import RuntimeSpatialMap from "./RuntimeSpatialMap";
import type {
  DemoPayload,
  EventRuntimeProjection,
  ResourceRuntimeProjection,
  RuntimeConnectionStatus,
  RuntimeSessionSnapshot,
  SimulationSpeed,
  SpatialQuestionResponse,
  TaskRuntimeProjection,
} from "./types";
import { useRuntimeSession } from "./useRuntimeSession";
import { useRuntimeSpatialView } from "./useRuntimeSpatialView";

type PageKey = "overview" | "tasks" | "events" | "resources" | "evidence";
type RuntimeTaskFilter = "all" | "active" | "attention";

const TASK_PAGE_SIZE = 50;
const RESOURCE_PAGE_SIZE = 40;
const EVENT_PAGE_SIZE = 20;
const FLIGHT_PAGE_SIZE = 24;
const ZONE_PAGE_SIZE = 20;
const AFFECTED_TASK_PAGE_SIZE = 30;

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

const priorityLabels: Record<number, string> = {
  1: "紧急",
  2: "重点",
  3: "常规",
};

const flightStatusLabels = {
  scheduled: "计划中",
  boarding: "登机中",
  delayed: "已延误",
  departed: "已起飞",
} as const;

const connectionInfo: Record<
  RuntimeConnectionStatus,
  { label: string; detail: string; tone: string }
> = {
  connecting: { label: "正在连接", detail: "正在建立权威运行流", tone: "pending" },
  live: { label: "实时同步", detail: "SSE 权威状态流正常", tone: "live" },
  recovering: { label: "正在恢复", detail: "连接中断，正在自动重连", tone: "recovering" },
  polling: { label: "快照恢复", detail: "每 2 秒读取权威 REST 快照", tone: "polling" },
  offline_readonly: { label: "只读保护", detail: "连接不可用，控制已禁用", tone: "offline" },
};

const pageInfo: Record<PageKey, { eyebrow: string; title: string; subtitle: string }> = {
  overview: {
    eyebrow: "实时运行工作台",
    title: "调度总览",
    subtitle: "查看当前事实、系统动作和需要人工决定的事项。",
  },
  tasks: {
    eyebrow: "保障执行管理",
    title: "任务计划",
    subtitle: "按后端权威状态复核任务进度、执行资源和下一边界。",
  },
  events: {
    eyebrow: "运行扰动管理",
    title: "事件影响",
    subtitle: "跟踪扰动从等待发生到重规划与人工确认的完整过程。",
  },
  resources: {
    eyebrow: "保障资源管理",
    title: "资源态势",
    subtitle: "掌握资源当前位置、移动进度、当前作业和下一可用时间。",
  },
  evidence: {
    eyebrow: "决策依据复核",
    title: "方案依据",
    subtitle: "复核当前执行方案、待确认候选、版本证据和人工决定。",
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

function formatTime(value: string | null): string {
  if (!value) return "待定";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function eventTypeLabel(event: EventRuntimeProjection | undefined): string {
  if (!event) return "运行扰动";
  return event.event_type === "delay" ? "航班延误" : "登机口调整";
}

function taskTone(status: TaskRuntimeProjection["status"]): string {
  if (status === "completed") return "success";
  if (status === "affected" || status === "unassigned") return "warning";
  if (["en_route", "waiting", "in_service"].includes(status)) return "active";
  return "neutral";
}

function resourceTone(status: ResourceRuntimeProjection["status"]): string {
  if (status === "serving" || status === "moving") return "active";
  if (status === "unavailable") return "warning";
  if (status === "idle") return "success";
  return "neutral";
}

function eventTone(status: EventRuntimeProjection["status"]): string {
  if (status === "resolved") return "success";
  if (status === "failed") return "warning";
  if (["triggered", "applied", "replanning", "awaiting_confirmation"].includes(status)) {
    return "active";
  }
  return "neutral";
}

function RuntimeLoadingState() {
  return (
    <main className="state-screen runtime-loading-state" aria-live="polite" aria-busy="true">
      <CircleGauge className="state-icon spin" />
      <div>
        <strong>正在恢复权威运行工作台</strong>
        <p>初始化期间不会在浏览器内推导或补造业务状态。</p>
      </div>
      <ol className="runtime-loading-steps">
        <li><span>01</span>载入匿名教学场景</li>
        <li><span>02</span>恢复 SQLite 运行会话</li>
        <li><span>03</span>建立权威状态流</li>
      </ol>
    </main>
  );
}

function RuntimeTaskRow({
  task,
  taskName,
  resourceName,
  zoneName,
  selected,
  onSelect,
}: {
  task: TaskRuntimeProjection;
  taskName: string;
  resourceName: string;
  zoneName: string;
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
      <td data-label="保障任务"><div className="task-cell"><strong>{task.task_id}</strong><span>{taskName}</span></div></td>
      <td data-label="实时状态"><span className={`runtime-state ${taskTone(task.status)}`}>{task.status_label}</span></td>
      <td data-label="执行资源"><strong>{resourceName}</strong><span className="cell-subtext">{task.assignment_id ?? "暂无安排"}</span></td>
      <td data-label="当前位置"><strong>{zoneName}</strong><span className="cell-subtext">后端投影位置</span></td>
      <td data-label="下一边界" className="tabular">{formatTime(task.next_transition_at)}</td>
      <td data-label="执行保护">
        <span className={`lock-state${task.is_locked ? " locked" : ""}`}>
          {task.is_locked ? "执行事实已锁定" : "未来任务可调整"}
        </span>
      </td>
    </tr>
  );
}

function ResourceRuntimeRow({
  resource,
  resourceName,
  selected,
  onSelect,
}: {
  resource: ResourceRuntimeProjection;
  resourceName: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`runtime-resource-row${selected ? " selected" : ""}`} onClick={onSelect}>
      <div><strong>{resource.resource_id}</strong><span>{resourceName}</span></div>
      <span className={`runtime-state ${resourceTone(resource.status)}`}>{resource.status_label}</span>
      <div className="runtime-resource-next">
        <strong>{resource.current_task_id ?? "当前无作业"}</strong>
        <span>{resource.next_task_id ? `下一任务 ${resource.next_task_id}` : "暂无后续任务"}</span>
      </div>
      <ArrowRight size={17} />
    </button>
  );
}

interface RuntimeWorkspaceProps {
  payload: DemoPayload;
  onReload: () => void;
  onInitialUnavailable: () => void;
}

export default function RuntimeWorkspace({
  payload,
  onReload,
  onInitialUnavailable,
}: RuntimeWorkspaceProps) {
  const runtime = useRuntimeSession({ payload, onInitialUnavailable });
  const spatial = useRuntimeSpatialView(runtime.snapshot);
  const [activePage, setActivePage] = useState<PageKey>("overview");
  const [taskFilter, setTaskFilter] = useState<RuntimeTaskFilter>("all");
  const [taskQuery, setTaskQuery] = useState("");
  const [taskPage, setTaskPage] = useState(1);
  const [resourceQuery, setResourceQuery] = useState("");
  const [resourcePage, setResourcePage] = useState(1);
  const [eventQuery, setEventQuery] = useState("");
  const [eventPage, setEventPage] = useState(1);
  const [flightQuery, setFlightQuery] = useState("");
  const [flightPage, setFlightPage] = useState(1);
  const [zoneQuery, setZoneQuery] = useState("");
  const [zonePage, setZonePage] = useState(1);
  const [affectedTaskPage, setAffectedTaskPage] = useState(1);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedResourceId, setSelectedResourceId] = useState("");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [spatialAnswer, setSpatialAnswer] = useState<SpatialQuestionResponse | null>(null);

  const snapshot = runtime.snapshot;
  const scenario = payload.views.baseline.scenario;
  const taskMeta = useMemo(
    () => new Map(scenario.tasks.map((task) => [task.task_id, task])),
    [scenario.tasks],
  );
  const resourceMeta = useMemo(
    () => new Map(scenario.resources.map((resource) => [resource.resource_id, resource])),
    [scenario.resources],
  );
  const zoneNames = useMemo(
    () => new Map(scenario.zones.map((zone) => [zone.zone_id, zone.name])),
    [scenario.zones],
  );
  const flightMeta = useMemo(
    () => new Map(scenario.flights.map((flight) => [flight.flight_id, flight])),
    [scenario.flights],
  );
  const visibleTasks = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.tasks.filter((task) => {
      const matchesFilter = taskFilter === "all"
        || (taskFilter === "active" && ["en_route", "waiting", "in_service"].includes(task.status))
        || (taskFilter === "attention" && (task.status === "affected" || task.status === "unassigned"));
      if (!matchesFilter) return false;
      const meta = taskMeta.get(task.task_id);
      const flight = flightMeta.get(meta?.flight_id ?? "");
      return includesNormalizedQuery(taskQuery, [
        task.task_id,
        task.status_label,
        task.resource_id,
        task.assignment_id,
        taskTypeLabels[meta?.task_type ?? ""],
        flight?.display_code,
      ]);
    });
  }, [flightMeta, snapshot, taskFilter, taskMeta, taskQuery]);

  const taskWindow = useMemo(
    () => paginateItems(visibleTasks, taskPage, TASK_PAGE_SIZE),
    [taskPage, visibleTasks],
  );
  const visibleResources = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.resources.filter((resource) => {
      const meta = resourceMeta.get(resource.resource_id);
      return includesNormalizedQuery(resourceQuery, [
        resource.resource_id,
        resource.status_label,
        resource.current_task_id,
        resource.next_task_id,
        resourceTypeLabels[meta?.resource_type ?? ""],
      ]);
    });
  }, [resourceMeta, resourceQuery, snapshot]);
  const resourceWindow = useMemo(
    () => paginateItems(visibleResources, resourcePage, RESOURCE_PAGE_SIZE),
    [resourcePage, visibleResources],
  );
  const visibleEvents = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.events.filter((event) => {
      const flight = flightMeta.get(event.flight_id);
      return includesNormalizedQuery(eventQuery, [
        event.event_id,
        event.detail,
        event.status_label,
        flight?.display_code,
        eventTypeLabel(event),
      ]);
    });
  }, [eventQuery, flightMeta, snapshot]);
  const eventWindow = useMemo(
    () => paginateItems(visibleEvents, eventPage, EVENT_PAGE_SIZE),
    [eventPage, visibleEvents],
  );
  const visibleFlights = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.flights.filter((flight) => includesNormalizedQuery(flightQuery, [
      flight.flight_id,
      flightMeta.get(flight.flight_id)?.display_code,
      flight.gate_id,
      flightStatusLabels[flight.status],
      flight.last_event_id,
    ]));
  }, [flightMeta, flightQuery, snapshot]);
  const flightWindow = useMemo(
    () => paginateItems(visibleFlights, flightPage, FLIGHT_PAGE_SIZE),
    [flightPage, visibleFlights],
  );
  const visibleZones = useMemo(
    () => scenario.zones.filter((zone) => includesNormalizedQuery(zoneQuery, [zone.zone_id, zone.name])),
    [scenario.zones, zoneQuery],
  );
  const zoneWindow = useMemo(
    () => paginateItems(visibleZones, zonePage, ZONE_PAGE_SIZE),
    [visibleZones, zonePage],
  );
  const affectedTasks = useMemo(
    () => snapshot?.tasks.filter((task) => task.affected_by_event_ids.length > 0) ?? [],
    [snapshot],
  );
  const affectedTaskWindow = useMemo(
    () => paginateItems(affectedTasks, affectedTaskPage, AFFECTED_TASK_PAGE_SIZE),
    [affectedTaskPage, affectedTasks],
  );

  const visibleTaskIds = useMemo(() => taskWindow.items.map((task) => task.task_id), [taskWindow.items]);
  const resourceIds = useMemo(
    () => resourceWindow.items.map((resource) => resource.resource_id),
    [resourceWindow.items],
  );
  const eventIds = useMemo(
    () => eventWindow.items.map((event) => event.event_id),
    [eventWindow.items],
  );

  useEffect(() => setTaskPage(taskWindow.page), [taskWindow.page]);
  useEffect(() => setResourcePage(resourceWindow.page), [resourceWindow.page]);
  useEffect(() => setEventPage(eventWindow.page), [eventWindow.page]);
  useEffect(() => setFlightPage(flightWindow.page), [flightWindow.page]);
  useEffect(() => setZonePage(zoneWindow.page), [zoneWindow.page]);
  useEffect(() => setAffectedTaskPage(affectedTaskWindow.page), [affectedTaskWindow.page]);

  useEffect(() => {
    setSelectedTaskId((current) => resolveSelectedId(current, visibleTaskIds));
  }, [visibleTaskIds]);
  useEffect(() => {
    setSelectedResourceId((current) => resolveSelectedId(current, resourceIds));
  }, [resourceIds]);
  useEffect(() => {
    setSelectedEventId((current) => resolveSelectedId(current, eventIds));
  }, [eventIds]);
  useEffect(() => {
    setSpatialAnswer((current) => (
      current
      && spatial.view
      && current.basis.session_id === snapshot?.session_id
      && current.basis.revision === snapshot.revision
      && current.basis.layout_id === spatial.view.layout.layout_id
        ? current
        : null
    ));
  }, [snapshot?.revision, snapshot?.session_id, spatial.view]);

  if (runtime.initializing || !snapshot) return <RuntimeLoadingState />;

  const summary = snapshot.collection_summary;
  const activeTaskCount = summary.task_active;
  const attentionTaskCount = summary.task_attention;
  const activeResourceCount = summary.resource_active;
  const selectedTask = taskWindow.items.find((task) => task.task_id === selectedTaskId) ?? taskWindow.items[0];
  const selectedTaskMeta = selectedTask ? taskMeta.get(selectedTask.task_id) : undefined;
  const selectedResource = resourceWindow.items.find((resource) => resource.resource_id === selectedResourceId)
    ?? resourceWindow.items[0];
  const selectedResourceMeta = selectedResource ? resourceMeta.get(selectedResource.resource_id) : undefined;
  const selectedEvent = eventWindow.items.find((event) => event.event_id === selectedEventId)
    ?? eventWindow.items[0];
  const candidateEventDetails = snapshot.candidate_plan_id
    ? snapshot.events
      .filter((event) => event.candidate_plan_id === snapshot.candidate_plan_id)
      .map((event) => event.detail)
    : [];
  const frozenTaskCount = summary.task_locked;
  const activePlanTitle = snapshot.active_plan_detail
    ? runtimePlanAlgorithmLabel(snapshot.active_plan_detail.algorithm)
    : "方案明细尚未同步";
  const candidatePlanTitle = snapshot.candidate_plan_detail
    ? runtimePlanAlgorithmLabel(snapshot.candidate_plan_detail.algorithm)
    : snapshot.candidate_plan_id
      ? "候选方案明细正在同步"
      : "当前无需人工选择";
  const currentPage = pageInfo[activePage];
  const connection = connectionInfo[runtime.connectionStatus];
  const controlsDisabled = runtime.connectionStatus === "offline_readonly" || runtime.controlBusy !== null;
  const canStart = ["ready", "paused"].includes(snapshot.status) && !controlsDisabled;
  const canPause = snapshot.status === "running" && !controlsDisabled;
  const canSetSpeed = ["ready", "running", "paused"].includes(snapshot.status) && !controlsDisabled;
  const canReset = ["paused", "awaiting_confirmation", "completed", "failed"].includes(snapshot.status)
    && !controlsDisabled;
  const canReplan = snapshot.status === "paused" && !controlsDisabled;

  const requestReset = () => {
    if (window.confirm("确认把本次会话重置到仿真窗口起点吗？已记录的审计不会被伪装成新的生产数据。")) {
      void runtime.reset();
    }
  };

  const openTask = (taskId: string) => {
    const index = snapshot.tasks.findIndex((task) => task.task_id === taskId);
    setTaskQuery("");
    setTaskFilter("all");
    setTaskPage(pageForIndex(index, TASK_PAGE_SIZE));
    setSelectedTaskId(taskId);
    setActivePage("tasks");
  };

  const openResource = (resourceId: string) => {
    const index = snapshot.resources.findIndex((resource) => resource.resource_id === resourceId);
    setResourceQuery("");
    setResourcePage(pageForIndex(index, RESOURCE_PAGE_SIZE));
    setSelectedResourceId(resourceId);
    setActivePage("resources");
  };

  const openEvent = (eventId: string) => {
    const index = snapshot.events.findIndex((event) => event.event_id === eventId);
    setEventQuery("");
    setEventPage(pageForIndex(index, EVENT_PAGE_SIZE));
    setSelectedEventId(eventId);
    setActivePage("events");
  };

  return (
    <div className="app-shell runtime-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">联</div>
          <div className="brand-copy"><strong>{payload.project.name}</strong><span>JOINT ASSURANCE</span></div>
        </div>

        <nav aria-label="主工作区">
          {navigation.map((item) => {
            const Icon = item.icon;
            const badge = item.key === "tasks"
              ? attentionTaskCount
              : item.key === "events"
                ? summary.event_open
                : item.key === "resources"
                  ? activeResourceCount
                  : item.key === "evidence" && snapshot.candidate_plan_id
                    ? 1
                    : null;
            return (
              <button
                key={item.key}
                className={`nav-item${activePage === item.key ? " active" : ""}`}
                aria-current={activePage === item.key ? "page" : undefined}
                onClick={() => setActivePage(item.key)}
              >
                <Icon size={20} />
                <span>{item.label}</span>
                {badge !== null && <b>{badge}</b>}
              </button>
            );
          })}
        </nav>

        <div className={`sidebar-status runtime-sidebar-status ${connection.tone}`}>
          <span className="eyebrow">当前运行状态</span>
          <strong>{snapshot.status_label}</strong>
          <span><i /> {connection.label}</span>
          <small>会话 {snapshot.session_id} · V{snapshot.current_scenario_version}</small>
        </div>
      </aside>

      <main className="main-content runtime-main">
        <header className="topbar">
          <div className="page-heading">
            <p className="eyebrow">{currentPage.eyebrow}</p>
            <h1>{currentPage.title}</h1>
            <p>{currentPage.subtitle}</p>
          </div>
          <div className="topbar-actions">
            <span className={`connection-chip ${connection.tone}`} title={connection.detail} role="status">
              <Server size={17} />{connection.label}
            </span>
            <span className="data-chip"><ShieldCheck size={15} />合成数据</span>
            <button className="icon-button" title="读取最新权威快照" onClick={() => void runtime.refresh()}>
              <RefreshCw size={18} />
              <span className="sr-only">读取最新权威快照</span>
            </button>
          </div>
        </header>

        <section className="runtime-control-bar" aria-label="仿真运行控制">
          <div className="runtime-clock">
            <Clock3 size={22} />
            <div><span>当前仿真时间</span><strong>{formatTime(snapshot.clock.simulation_time)}</strong></div>
            <small>下一边界 {formatTime(snapshot.clock.next_boundary_at)}</small>
          </div>

          <div className="runtime-identity">
            <span className={`runtime-state ${snapshot.status === "running" ? "active" : "neutral"}`}>
              {snapshot.status_label}
            </span>
            <div><span>场景版本</span><strong>V{snapshot.current_scenario_version}</strong></div>
            <div><span>业务修订</span><strong>R{snapshot.revision}</strong></div>
          </div>

          <div className="runtime-commands">
            {snapshot.status === "running" ? (
              <button className="runtime-command primary" disabled={!canPause} onClick={() => void runtime.pause()}>
                <Pause size={18} />暂停
              </button>
            ) : (
              <button className="runtime-command primary" disabled={!canStart} onClick={() => void runtime.start()}>
                <Play size={18} />{snapshot.status === "ready" ? "开始" : "继续"}
              </button>
            )}
            <div className="speed-switch" role="group" aria-label="仿真倍速">
              {([1, 5, 15] as SimulationSpeed[]).map((speed) => (
                <button
                  key={speed}
                  className={snapshot.clock.speed === speed ? "active" : undefined}
                  aria-pressed={snapshot.clock.speed === speed}
                  disabled={!canSetSpeed}
                  onClick={() => void runtime.setSpeed(speed)}
                >{speed}x</button>
              ))}
            </div>
            <button className="runtime-command" disabled={!canReplan} onClick={() => void runtime.replan()} title="仅在暂停时重新计算未来任务">
              <Route size={18} />重新计算
            </button>
            <button className="runtime-command icon-only" disabled={!canReset} onClick={requestReset} title="重置运行会话">
              <RotateCcw size={18} /><span className="sr-only">重置运行会话</span>
            </button>
          </div>
        </section>

        <div className="workspace-body runtime-workspace-body">
          {runtime.notice && (
            <div className="runtime-notice" role="status">
              <AlertTriangle size={17} /><span>{runtime.notice}</span>
              <div className="runtime-notice-actions">
                <button onClick={() => void runtime.refresh()}>读取权威快照</button>
                <button onClick={onReload}>重新连接</button>
              </div>
            </div>
          )}

          <section className={`workspace-page runtime-page page-${activePage}`} aria-label={currentPage.title}>
            {activePage === "overview" && (
              <div className="overview-layout runtime-overview-layout">
                <section className={`decision-panel ${snapshot.guidance.action_required ? "attention" : "recommended"}`}>
                  <div className="decision-symbol"><CircleGauge size={25} /></div>
                  <div className="decision-copy">
                    <span>{snapshot.guidance.action_required ? "需要人工决定" : "当前运行事实"}</span>
                    <h2>{snapshot.guidance.headline}</h2>
                    <p>{snapshot.guidance.detail}</p>
                  </div>
                  <div className="decision-facts">
                    <span><strong>{activeTaskCount}</strong>执行中任务</span>
                    <span><strong>{attentionTaskCount}</strong>需要协调</span>
                    <span><strong>{activeResourceCount}</strong>活动资源</span>
                  </div>
                  <button
                    className="primary-action"
                    onClick={() => setActivePage(snapshot.candidate_plan_id ? "evidence" : "tasks")}
                  >
                    {snapshot.candidate_plan_id ? "处理候选方案" : "查看执行任务"}<ArrowRight size={16} />
                  </button>
                </section>

                <RuntimeSpatialMap
                  view={spatial.view}
                  status={spatial.status}
                  error={spatial.error}
                  expectedRevision={snapshot.revision}
                  selectedTaskId={selectedTaskId}
                  selectedResourceId={selectedResourceId}
                  selectedEventId={selectedEventId}
                  onSelectTask={setSelectedTaskId}
                  onSelectResource={setSelectedResourceId}
                  onSelectEvent={setSelectedEventId}
                  onOpenTask={openTask}
                  onOpenResource={openResource}
                  onOpenEvent={openEvent}
                  onRetry={spatial.refresh}
                  assistantFocus={spatial.view
                    && spatialAnswer?.basis.session_id === snapshot.session_id
                    && spatialAnswer.basis.revision === snapshot.revision
                    && spatialAnswer.basis.layout_id === spatial.view.layout.layout_id
                    ? spatialAnswer.focus
                    : null}
                />

                {spatial.view && (
                  <RuntimeSpatialAssistant
                    view={spatial.view}
                    connectionStatus={runtime.connectionStatus}
                    selectedTaskId={selectedTaskId}
                    selectedResourceId={selectedResourceId}
                    selectedEventId={selectedEventId}
                    onSelectTask={setSelectedTaskId}
                    onSelectResource={setSelectedResourceId}
                    onSelectEvent={setSelectedEventId}
                    onAnswerChange={setSpatialAnswer}
                  />
                )}

                <section className="kpi-grid" aria-label="实时状态摘要">
                  <article className="kpi-card tone-blue"><span className="kpi-label">仿真时间</span><div className="kpi-value runtime-time-value">{formatTime(snapshot.clock.simulation_time)}</div><span className="kpi-note">后端权威 · {snapshot.clock.speed}x</span></article>
                  <article className="kpi-card tone-green"><span className="kpi-label">已完成任务</span><div className="kpi-value">{summary.task_completed}<small>项</small></div><span className="kpi-note">完成事实不会回退</span></article>
                  <article className="kpi-card tone-orange"><span className="kpi-label">待协调任务</span><div className="kpi-value">{attentionTaskCount}<small>项</small></div><span className="kpi-note">受扰动或尚未分配</span></article>
                  <article className="kpi-card"><span className="kpi-label">场景版本</span><div className="kpi-value">V{snapshot.current_scenario_version}</div><span className="kpi-note">修订 R{snapshot.revision}</span></article>
                </section>

                <div className="overview-lower runtime-overview-lower">
                  <section className="workspace-section live-task-section">
                    <div className="section-heading"><div><p className="eyebrow">系统正在做什么</p><h2>当前执行队列</h2></div><span className="section-note">状态由后端时间边界决定</span></div>
                    <div className="live-queue">
                      {snapshot.tasks
                        .filter((task) => ["en_route", "waiting", "in_service", "affected"].includes(task.status))
                        .slice(0, 5)
                        .map((task) => (
                          <button key={task.task_id} onClick={() => openTask(task.task_id)}>
                            <span className={`runtime-state ${taskTone(task.status)}`}>{task.status_label}</span>
                            <div><strong>{task.task_id}</strong><small>{task.resource_id ?? "待协调资源"}</small></div>
                            <time>{formatTime(task.next_transition_at)}</time>
                            <ArrowRight size={16} />
                          </button>
                        ))}
                      {activeTaskCount + attentionTaskCount === 0 && (
                        <div className="empty-state compact"><CheckCircle2 size={26} /><strong>当前没有执行中的任务</strong><span>开始或继续运行后，任务会按后端边界自动变化。</span></div>
                      )}
                    </div>
                  </section>

                  <section className="workspace-section next-action-section">
                    <div className="section-heading"><div><p className="eyebrow">下一步</p><h2>运行关注事项</h2></div></div>
                    <div className="runtime-brief-list">
                      <button onClick={() => setActivePage("events")}><Plane size={19} /><span><strong>{summary.event_pending} 项事件等待发生</strong><small>下一边界 {formatTime(snapshot.clock.next_boundary_at)}</small></span><ArrowRight size={16} /></button>
                      <button onClick={() => setActivePage("resources")}><BusFront size={19} /><span><strong>{activeResourceCount} 项资源正在执行</strong><small>查看移动进度和下一可用时间</small></span><ArrowRight size={16} /></button>
                      <button onClick={() => setActivePage("evidence")}><ShieldCheck size={19} /><span><strong>{snapshot.candidate_plan_id ? "候选方案等待确认" : "当前方案继续执行"}</strong><small>{snapshot.guidance.recommended_action}</small></span><ArrowRight size={16} /></button>
                    </div>
                  </section>
                </div>
              </div>
            )}

            {activePage === "tasks" && (
              <div className="tasks-layout">
                <section className="workspace-section task-board">
                  <div className="section-heading task-toolbar">
                    <div><p className="eyebrow">实时执行清单</p><h2>保障任务状态</h2></div>
                    <div className="filter-switch" role="group" aria-label="任务状态筛选">
                      {([
                        ["all", `全部 ${summary.task_total}`],
                        ["active", `执行中 ${activeTaskCount}`],
                        ["attention", `需协调 ${attentionTaskCount}`],
                      ] as Array<[RuntimeTaskFilter, string]>).map(([filter, label]) => (
                        <button key={filter} className={taskFilter === filter ? "active" : undefined} onClick={() => { setTaskFilter(filter); setTaskPage(1); }}>{label}</button>
                      ))}
                    </div>
                    <span className="plan-state"><ShieldCheck size={15} />权威投影</span>
                  </div>
                  <div className="data-window-toolbar">
                    <label className="data-window-search">
                      <span>检索任务</span>
                      <input
                        type="search"
                        value={taskQuery}
                        placeholder="任务、航班、资源或状态"
                        onChange={(event) => { setTaskQuery(event.target.value); setTaskPage(1); }}
                      />
                    </label>
                    <p className="data-window-note">仅筛选当前权威快照，不改变运行修订或触发重规划。</p>
                  </div>
                  <div className="table-wrap">
                    {taskWindow.items.length ? (
                      <table>
                        <thead><tr><th>保障任务</th><th>实时状态</th><th>执行资源</th><th>当前位置</th><th>下一边界</th><th>执行保护</th></tr></thead>
                        <tbody>
                          {taskWindow.items.map((task) => {
                            const meta = taskMeta.get(task.task_id);
                            return (
                              <RuntimeTaskRow
                                key={task.task_id}
                                task={task}
                                taskName={taskTypeLabels[meta?.task_type ?? ""] ?? meta?.task_type ?? "保障任务"}
                                resourceName={task.resource_id ?? "待协调"}
                                zoneName={zoneNames.get(task.current_zone_id ?? "") ?? task.current_zone_id ?? "待定"}
                                selected={task.task_id === selectedTask?.task_id}
                                onSelect={() => setSelectedTaskId(task.task_id)}
                              />
                            );
                          })}
                        </tbody>
                      </table>
                    ) : (
                      <div className="empty-state"><ListChecks size={28} /><strong>当前筛选下没有任务</strong><span>切换筛选不会影响仿真运行或触发重规划。</span></div>
                    )}
                  </div>
                  <PaginationControls label="保障任务" total={visibleTasks.length} page={taskWindow.page} pageSize={TASK_PAGE_SIZE} onPageChange={setTaskPage} />
                  <p className="table-hint">移动端按任务卡片展示完整状态和执行保护信息。</p>
                </section>

                <aside className="workspace-section task-detail">
                  {selectedTask ? (
                    <>
                      <div className="detail-heading">
                        <div><span>任务详情</span><h2>{selectedTask.task_id}</h2><p>{taskTypeLabels[selectedTaskMeta?.task_type ?? ""] ?? "保障任务"}</p></div>
                        <span className={`runtime-state ${taskTone(selectedTask.status)}`}>{selectedTask.status_label}</span>
                      </div>
                      <dl className="detail-list runtime-detail-list">
                        <div><dt>保障等级</dt><dd>{priorityLabels[selectedTaskMeta?.priority ?? 0] ?? "未标注"}</dd></div>
                        <div><dt>保障对象</dt><dd>{passengerGroupLabels[selectedTaskMeta?.passenger_group ?? ""] ?? "匿名任务"}</dd></div>
                        <div><dt>关联航班</dt><dd>{flightMeta.get(selectedTaskMeta?.flight_id ?? "")?.display_code ?? selectedTaskMeta?.flight_id ?? "待定"}</dd></div>
                        <div><dt>执行资源</dt><dd>{selectedTask.resource_id ?? "待协调"}</dd></div>
                        <div><dt>当前位置</dt><dd>{zoneNames.get(selectedTask.current_zone_id ?? "") ?? selectedTask.current_zone_id ?? "待定"}</dd></div>
                        <div><dt>下一边界</dt><dd>{formatTime(selectedTask.next_transition_at)}</dd></div>
                        <div><dt>执行事实</dt><dd>{selectedTask.is_locked ? "已冻结保护" : "未来任务可调整"}</dd></div>
                        <div><dt>安排编号</dt><dd>{selectedTask.assignment_id ?? "暂无安排"}</dd></div>
                      </dl>
                      {selectedTask.affected_by_event_ids.length > 0 && (
                        <div className="manual-note"><AlertTriangle size={18} /><div><strong>任务受到运行扰动影响</strong><p>{selectedTask.affected_by_event_ids.join("、")}</p></div></div>
                      )}
                      {selectedTask.resource_id && (
                        <button className="secondary-action" onClick={() => openResource(selectedTask.resource_id ?? "")}>查看执行资源<ArrowRight size={16} /></button>
                      )}
                    </>
                  ) : <div className="empty-state compact"><ListChecks size={26} /><strong>选择任务查看详情</strong></div>}
                </aside>
              </div>
            )}

            {activePage === "events" && (
              <div className="events-layout runtime-events-layout">
                <RuntimeEventAssistant
                  snapshot={snapshot}
                  connectionStatus={runtime.connectionStatus}
                  controlBusy={runtime.controlBusy}
                  submitReviewedEvent={runtime.submitReviewedEvent}
                />
                <div className="runtime-events-overview">
                <section className="workspace-section event-catalog">
                  <div className="section-heading"><div><p className="eyebrow">处理时间流</p><h2>{summary.event_total} 项运行扰动</h2></div><span className="section-note">事件只应用一次</span></div>
                  <div className="data-window-toolbar compact">
                    <label className="data-window-search">
                      <span>检索事件</span>
                      <input
                        type="search"
                        value={eventQuery}
                        placeholder="事件、航班、状态或内容"
                        onChange={(event) => { setEventQuery(event.target.value); setEventPage(1); }}
                      />
                    </label>
                  </div>
                  <div className="event-selector runtime-event-selector">
                    {eventWindow.items.map((event, index) => {
                      return (
                        <button key={event.event_id} className={selectedEvent?.event_id === event.event_id ? "active" : undefined} onClick={() => setSelectedEventId(event.event_id)}>
                          <span className="event-index">{String(eventWindow.startIndex + index + 1).padStart(2, "0")}</span>
                          <div><strong>{eventTypeLabel(event)}</strong><span>{formatTime(event.occurred_at)} · {event.detail}</span></div>
                          <span className={`runtime-state ${eventTone(event.status)}`}>{event.status_label}</span>
                        </button>
                      );
                    })}
                    {!eventWindow.items.length && <div className="empty-state compact"><Plane size={25} /><strong>没有匹配的运行事件</strong></div>}
                  </div>
                  <PaginationControls label="运行事件" total={visibleEvents.length} page={eventWindow.page} pageSize={EVENT_PAGE_SIZE} onPageChange={setEventPage} />
                </section>

                <section className="workspace-section event-analysis">
                  {selectedEvent ? (
                    <>
                      <div className="event-hero"><div className="event-symbol"><Plane size={23} /></div><div><span>{eventTypeLabel(selectedEvent)}</span><h2>{selectedEvent.detail}</h2><p>{selectedEvent.note ?? "结构化运行事件"}</p></div></div>
                      <div className="impact-summary">
                        <article><span>发生时间</span><strong>{formatTime(selectedEvent.occurred_at)}</strong></article>
                        <article><span>处理状态</span><strong>{selectedEvent.status_label}</strong></article>
                        <article><span>应用版本</span><strong>{selectedEvent.scenario_version_after ? `V${selectedEvent.scenario_version_after}` : "待应用"}</strong></article>
                        <article><span>关联候选</span><strong>{selectedEvent.candidate_plan_id ? (selectedEvent.status === "resolved" ? "已完成人工确认" : "待确认") : "无"}</strong></article>
                      </div>
                      <div className="event-interpretation"><h3>处理依据</h3><p>{selectedEvent.applied_at ? `事件已于仿真时间 ${formatTime(selectedEvent.applied_at)} 原子应用；执行中的任务继续受冻结保护。` : "事件尚未到达发生边界，后端不会提前应用或触发规划。"}</p></div>
                    </>
                  ) : <div className="empty-state"><Plane size={28} /><strong>当前场景没有事件</strong></div>}
                </section>

                <section className="workspace-section flight-runtime-panel">
                  <div className="section-heading"><div><p className="eyebrow">航班动态</p><h2>当前预计与登机口</h2></div><span className="section-note">来自权威运行投影</span></div>
                  <div className="data-window-toolbar compact">
                    <label className="data-window-search">
                      <span>检索航班</span>
                      <input
                        type="search"
                        value={flightQuery}
                        placeholder="航班、登机口或状态"
                        onChange={(event) => { setFlightQuery(event.target.value); setFlightPage(1); }}
                      />
                    </label>
                  </div>
                  <div className="flight-runtime-list">
                    {flightWindow.items.map((flight) => (
                      <article key={flight.flight_id}>
                        <Plane size={20} />
                        <div><strong>{flightMeta.get(flight.flight_id)?.display_code ?? flight.flight_id}</strong><span>{flight.gate_id} · {flightStatusLabels[flight.status]}</span></div>
                        <time>{formatTime(flight.estimated_departure)}</time>
                        <small>{flight.last_event_id ? `最近事件 ${flight.last_event_id}` : "暂无变化"}</small>
                      </article>
                    ))}
                    {!flightWindow.items.length && <div className="empty-state compact"><Plane size={25} /><strong>没有匹配的航班投影</strong></div>}
                  </div>
                  <PaginationControls label="航班投影" total={visibleFlights.length} page={flightWindow.page} pageSize={FLIGHT_PAGE_SIZE} onPageChange={setFlightPage} />
                </section>
                </div>
              </div>
            )}

            {activePage === "resources" && (
              <div className="resources-layout runtime-resources-layout">
                <section className="workspace-section resource-catalog">
                  <div className="section-heading"><div><p className="eyebrow">资源清单</p><h2>实时资源状态</h2></div><span className="section-note">{activeResourceCount} / {summary.resource_total} 项正在执行</span></div>
                  <div className="data-window-toolbar compact">
                    <label className="data-window-search">
                      <span>检索资源</span>
                      <input
                        type="search"
                        value={resourceQuery}
                        placeholder="资源、任务、类型或状态"
                        onChange={(event) => { setResourceQuery(event.target.value); setResourcePage(1); }}
                      />
                    </label>
                  </div>
                  <div className="runtime-resource-list">
                    {resourceWindow.items.map((resource) => (
                      <ResourceRuntimeRow
                        key={resource.resource_id}
                        resource={resource}
                        resourceName={resourceTypeLabels[resourceMeta.get(resource.resource_id)?.resource_type ?? ""] ?? "保障资源"}
                        selected={selectedResource?.resource_id === resource.resource_id}
                        onSelect={() => setSelectedResourceId(resource.resource_id)}
                      />
                    ))}
                    {!resourceWindow.items.length && <div className="empty-state compact"><BusFront size={25} /><strong>没有匹配的资源投影</strong></div>}
                  </div>
                  <PaginationControls label="保障资源" total={visibleResources.length} page={resourceWindow.page} pageSize={RESOURCE_PAGE_SIZE} onPageChange={setResourcePage} />
                </section>

                <section className="workspace-section resource-detail">
                  {selectedResource ? (
                    <>
                      <div className="resource-detail-head"><div className="resource-symbol"><BusFront size={23} /></div><div><span>{resourceTypeLabels[selectedResourceMeta?.resource_type ?? ""] ?? "保障资源"}</span><h2>{selectedResource.resource_id}</h2></div><span className={`runtime-state ${resourceTone(selectedResource.status)}`}>{selectedResource.status_label}</span></div>
                      <div className="movement-progress">
                        <div><span style={{ width: `${selectedResource.position.progress_pct}%` }} /></div>
                        <strong>{selectedResource.position.progress_pct.toFixed(0)}%</strong>
                        <p>
                          {zoneNames.get(selectedResource.position.from_zone_id) ?? selectedResource.position.from_zone_id}
                          {selectedResource.position.to_zone_id && (
                            <><ArrowRight size={15} />{zoneNames.get(selectedResource.position.to_zone_id) ?? selectedResource.position.to_zone_id}</>
                          )}
                        </p>
                      </div>
                      <dl className="detail-list resource-facts">
                        <div><dt>当前任务</dt><dd>{selectedResource.current_task_id ?? "无"}</dd></div>
                        <div><dt>下一任务</dt><dd>{selectedResource.next_task_id ?? "无"}</dd></div>
                        <div><dt>下一可用</dt><dd>{formatTime(selectedResource.next_available_at)}</dd></div>
                        <div><dt>当前位置</dt><dd>{zoneNames.get(selectedResource.position.from_zone_id) ?? selectedResource.position.from_zone_id}</dd></div>
                        <div><dt>服务容量</dt><dd>{selectedResourceMeta?.capacity ?? "-"}</dd></div>
                        <div><dt>资源状态</dt><dd>{selectedResource.status_label}</dd></div>
                      </dl>
                    </>
                  ) : <div className="empty-state"><BusFront size={28} /><strong>当前没有资源投影</strong></div>}
                </section>

                <section className="workspace-section resource-sequence">
                  <div className="section-heading"><div><p className="eyebrow">当前与下一项</p><h2>执行衔接</h2></div></div>
                  <div className="resource-task-sequence">
                    {[selectedResource?.current_task_id, selectedResource?.next_task_id].map((taskId, index) => taskId && (
                      <button key={taskId} onClick={() => openTask(taskId)}>
                        <span>{index === 0 ? "当前" : "下一"}</span><div><strong>{taskId}</strong><small>{taskTypeLabels[taskMeta.get(taskId)?.task_type ?? ""] ?? "保障任务"}</small></div><ArrowRight size={16} />
                      </button>
                    ))}
                    {!selectedResource?.current_task_id && !selectedResource?.next_task_id && <div className="empty-state compact"><CheckCircle2 size={25} /><strong>当前无执行任务</strong></div>}
                  </div>
                </section>

                <section className="workspace-section zone-overview">
                  <div className="section-heading"><div><p className="eyebrow">区域事实</p><h2>保障区域</h2></div></div>
                  <div className="data-window-toolbar compact">
                    <label className="data-window-search">
                      <span>检索区域</span>
                      <input
                        type="search"
                        value={zoneQuery}
                        placeholder="区域编号或名称"
                        onChange={(event) => { setZoneQuery(event.target.value); setZonePage(1); }}
                      />
                    </label>
                  </div>
                  <div className="zone-list">
                    {zoneWindow.items.map((zone) => (
                      <article key={zone.zone_id} className={selectedResource?.position.from_zone_id === zone.zone_id ? "active" : undefined}><MapPinned size={18} /><div><strong>{zone.name}</strong><span>{selectedResource?.position.from_zone_id === zone.zone_id ? "资源当前区域" : zone.zone_id}</span></div></article>
                    ))}
                    {!zoneWindow.items.length && <div className="empty-state compact"><MapPinned size={25} /><strong>没有匹配的保障区域</strong></div>}
                  </div>
                  <PaginationControls label="保障区域" total={visibleZones.length} page={zoneWindow.page} pageSize={ZONE_PAGE_SIZE} onPageChange={setZonePage} />
                </section>
              </div>
            )}

            {activePage === "evidence" && (
              <div className="evidence-layout runtime-evidence-layout">
                <section className={`workspace-section candidate-panel${snapshot.candidate_plan_id ? " awaiting" : ""}`}>
                  <div className="candidate-heading"><div className="validation-symbol success"><ShieldCheck size={25} /></div><div><span>{snapshot.candidate_plan_id ? "需要人工决定" : "运行方案状态"}</span><h2>{snapshot.candidate_plan_id ? "新的滚动候选已经通过独立约束复核" : snapshot.active_plan_detail ? `${activePlanTitle}正在执行` : "当前方案正在执行，明细尚未同步"}</h2><p>{snapshot.candidate_plan_id ? "仿真时间保持冻结，确认前当前方案不会被替换。" : snapshot.active_plan_detail ? runtimePlanFacts(snapshot.active_plan_detail) : "当前方案编号已经保存，但运行服务尚未返回任务、资源与时段明细。"}</p></div></div>
                  <div className="plan-id-comparison">
                    <article className="active">
                      <div className="plan-card-label"><span>当前执行方案</span><b>正在生效</b></div>
                      <strong>{activePlanTitle}</strong>
                      <code>{snapshot.active_plan_id}</code>
                      <small>{snapshot.active_plan_detail ? runtimePlanFacts(snapshot.active_plan_detail) : "等待权威快照补齐具体执行内容"}</small>
                    </article>
                    <ArrowRight size={20} />
                    <article className={snapshot.candidate_plan_id ? "candidate" : undefined}>
                      <div className="plan-card-label"><span>待确认候选</span><b>{snapshot.candidate_plan_id ? "等待确认" : "无需处理"}</b></div>
                      <strong>{candidatePlanTitle}</strong>
                      {snapshot.candidate_plan_id ? <code>{snapshot.candidate_plan_id}</code> : <p>事件或人工重新计算触发后在这里出现。</p>}
                      <small>{snapshot.candidate_plan_detail ? runtimePlanFacts(snapshot.candidate_plan_detail) : snapshot.candidate_plan_id ? "已收到候选编号，等待权威快照补齐具体变化" : "当前方案继续生效，不会被静默替换"}</small>
                    </article>
                  </div>
                  {snapshot.candidate_plan_id && (
                    <div className="candidate-actions">
                      <button className="runtime-command primary" disabled={controlsDisabled} onClick={() => void runtime.acceptCandidate()}><CheckCircle2 size={18} />采用候选</button>
                      <button className="runtime-command" disabled={controlsDisabled} onClick={() => void runtime.rejectCandidate()}><ShieldCheck size={18} />保留当前方案</button>
                    </div>
                  )}
                </section>

                <section className="workspace-section runtime-plan-detail-panel">
                  <div className="section-heading"><div><p className="eyebrow">方案任务书</p><h2>当前安排与候选响应内容</h2></div><span className="section-note">全部来自后端计划事实</span></div>
                  <RuntimePlanDetails
                    activePlan={snapshot.active_plan_detail}
                    candidatePlan={snapshot.candidate_plan_detail}
                    scenario={scenario}
                    eventDetails={candidateEventDetails}
                    frozenTaskCount={frozenTaskCount}
                  />
                </section>

                <RuntimePlanExplanation
                  snapshot={snapshot}
                  connectionStatus={runtime.connectionStatus}
                />

                <section className="workspace-section affected-task-panel">
                  <div className="section-heading"><div><p className="eyebrow">变化范围</p><h2>受影响任务</h2></div><span className="section-note">{affectedTasks.length} 项</span></div>
                  <div className="affected-task-list">
                    {affectedTaskWindow.items.map((task) => (
                      <button key={task.task_id} onClick={() => openTask(task.task_id)}><span className={`runtime-state ${taskTone(task.status)}`}>{task.status_label}</span><div><strong>{task.task_id}</strong><small>{task.affected_by_event_ids.join("、")}</small></div><ArrowRight size={16} /></button>
                    ))}
                    {!affectedTasks.length && <div className="empty-state compact"><CheckCircle2 size={25} /><strong>当前没有受扰动待确认任务</strong></div>}
                  </div>
                  {affectedTasks.length > 0 && <PaginationControls label="受影响任务" total={affectedTasks.length} page={affectedTaskWindow.page} pageSize={AFFECTED_TASK_PAGE_SIZE} onPageChange={setAffectedTaskPage} />}
                </section>

                <section className="workspace-section basis-panel runtime-basis-panel">
                  <div className="section-heading"><div><p className="eyebrow">权威证据</p><h2>会话与版本</h2></div></div>
                  <dl className="basis-list">
                    <div><dt>运行会话</dt><dd>{snapshot.session_id}</dd></div>
                    <div><dt>场景版本</dt><dd>V{snapshot.initial_scenario_version} → V{snapshot.current_scenario_version}</dd></div>
                    <div><dt>业务修订</dt><dd>R{snapshot.revision}</dd></div>
                    <div><dt>存储范围</dt><dd>SQLite</dd></div>
                    <div><dt>流消息</dt><dd>{runtime.lastEventType ?? "等待首帧"}</dd></div>
                    <div><dt>人工确认</dt><dd>候选采用前必需</dd></div>
                  </dl>
                </section>

                <section className="workspace-section process-panel runtime-process-panel">
                  <div className="section-heading"><div><p className="eyebrow">运行链路</p><h2>状态如何形成</h2></div></div>
                  <div className="process-flow">
                    {[
                      ["01", "权威时钟", "后端推进仿真边界"],
                      ["02", "事件应用", "事件只原子应用一次"],
                      ["03", "事实冻结", "保护执行中的工作"],
                      ["04", "约束规划", "只重排未来任务"],
                      ["05", "人工确认", "明确采用或保留"],
                    ].map(([index, title, detail], stepIndex) => (
                      <div className="process-step" key={index}><span>{index}</span><div><strong>{title}</strong><p>{detail}</p></div>{stepIndex < 4 && <ArrowRight size={15} />}</div>
                    ))}
                  </div>
                </section>
              </div>
            )}
          </section>
        </div>

        <footer className="safety-footer"><AlertTriangle size={16} /><span>{snapshot.safety_notice}</span><strong>结果需人工确认</strong></footer>
      </main>
    </div>
  );
}
