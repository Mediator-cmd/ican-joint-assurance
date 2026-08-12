import {
  AlertTriangle,
  BusFront,
  MapPinned,
  Maximize2,
  Plane,
  RefreshCw,
  Route,
  ShieldCheck,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import { spatialRoutePolylinePoints } from "./spatial";
import type {
  NormalizedPoint,
  RuntimeSpatialStatus,
  RuntimeSpatialView,
  SpatialMapFocus,
  SpatialLayout,
  SpatialTaskRoute,
} from "./types";


type SpatialLayer = "zones" | "active" | "candidate" | "tasks" | "resources" | "events";

interface RuntimeSpatialMapProps {
  view: RuntimeSpatialView | null;
  status: RuntimeSpatialStatus;
  error: string | null;
  expectedRevision: number;
  selectedTaskId: string;
  selectedResourceId: string;
  selectedEventId: string;
  onSelectTask: (taskId: string) => void;
  onSelectResource: (resourceId: string) => void;
  onSelectEvent: (eventId: string) => void;
  onOpenTask: (taskId: string) => void;
  onOpenResource: (resourceId: string) => void;
  onOpenEvent: (eventId: string) => void;
  onRetry: () => void;
  assistantFocus?: SpatialMapFocus | null;
}

const taskStatusLabels: Record<string, string> = {
  pending: "待出发",
  en_route: "前往服务点",
  waiting: "现场等待",
  in_service: "保障中",
  completed: "已完成",
  unassigned: "待协调",
  affected: "受扰动",
};

const eventStatusLabels: Record<string, string> = {
  pending: "等待发生",
  triggered: "已触发",
  applied: "已应用",
  replanning: "重规划中",
  awaiting_confirmation: "待确认",
  resolved: "已处理",
  failed: "处理失败",
};

const resourceStatusLabels: Record<string, string> = {
  idle: "空闲",
  moving: "移动中",
  waiting: "现场等待",
  serving: "保障中",
  unavailable: "不可用",
};

const layerLabels: Array<{ key: SpatialLayer; label: string }> = [
  { key: "zones", label: "区域" },
  { key: "active", label: "当前实线" },
  { key: "candidate", label: "候选虚线" },
  { key: "tasks", label: "任务" },
  { key: "resources", label: "资源" },
  { key: "events", label: "事件" },
];

function toCanvas(point: NormalizedPoint, layout: SpatialLayout): [number, number] {
  return [point.x * layout.canvas.width, point.y * layout.canvas.height];
}

function formatSpatialTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function taskRouteTone(route: SpatialTaskRoute): string {
  if (route.demand_only || route.task_status === "affected") return "attention";
  if (route.task_status === "completed") return "completed";
  if (["en_route", "waiting", "in_service"].includes(route.task_status)) return "executing";
  return "planned";
}

function declutterOffsets<T>(
  items: T[],
  idOf: (item: T) => string,
  pointOf: (item: T) => NormalizedPoint,
): Map<string, [number, number]> {
  const groups = new Map<string, T[]>();
  items.forEach((item) => {
    const point = pointOf(item);
    const key = `${point.x.toFixed(6)}:${point.y.toFixed(6)}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });
  const result = new Map<string, [number, number]>();
  groups.forEach((group) => {
    const sorted = [...group].sort((left, right) => idOf(left).localeCompare(idOf(right)));
    const columnCount = Math.min(3, sorted.length);
    const rowCount = Math.ceil(sorted.length / columnCount);
    const containsWideMarker = sorted.some((item) => idOf(item).startsWith("resource:"));
    const horizontalGap = containsWideMarker ? 98 : 42;
    const verticalGap = 42;
    sorted.forEach((item, index) => {
      if (sorted.length === 1) {
        result.set(idOf(item), [0, 0]);
        return;
      }
      const column = index % columnCount;
      const row = Math.floor(index / columnCount);
      result.set(idOf(item), [
        (column - (columnCount - 1) / 2) * horizontalGap,
        (row - (rowCount - 1) / 2) * verticalGap,
      ]);
    });
  });
  return result;
}

function activateWithKeyboard(event: React.KeyboardEvent<SVGGElement>, action: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    action();
  }
}

export default function RuntimeSpatialMap({
  view,
  status,
  error,
  expectedRevision,
  selectedTaskId,
  selectedResourceId,
  selectedEventId,
  onSelectTask,
  onSelectResource,
  onSelectEvent,
  onOpenTask,
  onOpenResource,
  onOpenEvent,
  onRetry,
  assistantFocus = null,
}: RuntimeSpatialMapProps) {
  const [expanded, setExpanded] = useState(false);
  const [layers, setLayers] = useState<Record<SpatialLayer, boolean>>({
    zones: true,
    active: true,
    candidate: true,
    tasks: true,
    resources: true,
    events: true,
  });
  const activeView = view?.overlay.revision === expectedRevision ? view : null;
  const markerOffsets = useMemo(
    () => activeView
      ? declutterOffsets(
        [
          ...activeView.overlay.resource_markers.map((item) => ({
            key: `resource:${item.resource_id}`,
            position: item.position,
          })),
          ...activeView.overlay.event_markers.map((item) => ({
            key: `event:${item.event_id}`,
            position: item.position,
          })),
        ],
        (item) => item.key,
        (item) => item.position,
      )
      : new Map<string, [number, number]>(),
    [activeView],
  );

  if (activeView === null) {
    return (
      <section className="workspace-section spatial-panel spatial-panel-empty" aria-live="polite">
        <div className="spatial-empty-symbol"><MapPinned size={30} /></div>
        <div>
          <p className="eyebrow">机场空间态势 · R{expectedRevision}</p>
          <h2>{status === "unavailable" ? "当前场景没有公开空间布局" : "正在绑定权威空间事实"}</h2>
          <p>{error ?? "地图不会使用旧 revision、随机位置或浏览器自推进数据。"}</p>
        </div>
        {status === "error" && (
          <button className="spatial-retry" onClick={onRetry}><RefreshCw size={16} />重新读取</button>
        )}
      </section>
    );
  }

  const { layout, overlay, coverage } = activeView;
  const activeRoutes = overlay.task_routes.filter((route) => route.route_kind === "active");
  const candidateRoutes = overlay.task_routes.filter((route) => route.route_kind === "candidate");
  const selectedActiveRoute = activeRoutes.find((route) => route.task_id === selectedTaskId);
  const selectedCandidateRoute = candidateRoutes.find((route) => route.task_id === selectedTaskId);
  const selectedResource = overlay.resource_markers.find((item) => item.resource_id === selectedResourceId);
  const selectedEvent = overlay.event_markers.find((item) => item.event_id === selectedEventId);

  return (
    <section
      className={`workspace-section spatial-panel${expanded ? " expanded" : ""}`}
      aria-label="机场空间态势"
    >
      <header className="spatial-panel-header">
        <div className="spatial-title">
          <div className="spatial-title-symbol"><MapPinned size={23} /></div>
          <div>
            <p className="eyebrow">匿名机场仿真空间示意图</p>
            <h2>任务与处置过程实时投影</h2>
            <span>空间时间 {formatSpatialTime(overlay.simulation_time)} · R{overlay.revision}</span>
          </div>
        </div>
        <div className="spatial-coverage" aria-label="空间对象覆盖率">
          <span><strong>{coverage.projected_active_task_ids.length}</strong>/{coverage.source_task_ids.length} 任务</span>
          <span><strong>{coverage.projected_resource_ids.length}</strong>/{coverage.source_resource_ids.length} 资源</span>
          <span><strong>{coverage.projected_event_ids.length}</strong>/{coverage.source_event_ids.length} 事件</span>
          <span className="complete"><ShieldCheck size={14} />一一对应完整</span>
        </div>
        <button
          className="spatial-expand"
          onClick={() => setExpanded((value) => !value)}
          aria-label={expanded ? "关闭空间态势放大视图" : "放大空间态势"}
        >
          {expanded ? <X size={19} /> : <Maximize2 size={19} />}
        </button>
      </header>

      <div className="spatial-layer-bar" role="group" aria-label="地图图层">
        <span>显示图层</span>
        {layerLabels.map((layer) => (
          <button
            key={layer.key}
            className={layers[layer.key] ? "active" : undefined}
            aria-pressed={layers[layer.key]}
            onClick={() => setLayers((current) => ({ ...current, [layer.key]: !current[layer.key] }))}
          >
            {layer.label}
          </button>
        ))}
        <div className="spatial-line-legend">
          <span><i className="active" />当前执行</span>
          <span><i className="candidate" />待确认候选</span>
          <span><i className="anchor" />连线指向权威位置</span>
        </div>
      </div>

      <div className="spatial-panel-body">
        <div className="spatial-map-frame">
          <svg
            className="spatial-map"
            viewBox={`0 0 ${layout.canvas.width} ${layout.canvas.height}`}
            role="img"
            aria-label={`机场空间态势，${activeRoutes.length} 条当前任务路线，${candidateRoutes.length} 条候选路线`}
          >
            <image
              href={layout.asset.public_path}
              width={layout.canvas.width}
              height={layout.canvas.height}
              preserveAspectRatio="xMidYMid meet"
            />

            {layers.zones && layout.zones.map((zone) => {
              const [x, y] = toCanvas(zone.anchor, layout);
              const aiFocused = assistantFocus?.zone_ids.includes(zone.zone_id) ?? false;
              return (
                <g key={zone.zone_id} className={`spatial-zone-label${aiFocused ? " assistant-focus" : ""}`} data-ai-focus={aiFocused || undefined}>
                  <circle cx={x} cy={y} r={7} />
                  <text x={x} y={y + 72}>{zone.label}</text>
                  <text className="zone-id" x={x} y={y + 90}>{zone.zone_id}</text>
                </g>
              );
            })}

            {layers.tasks && activeRoutes.map((route) => {
              if (!layers.active) return null;
              const points = spatialRoutePolylinePoints(layout, route.legs);
              const aiFocused = assistantFocus?.task_ids.includes(route.task_id) ?? false;
              return (
                <g
                  key={route.route_id}
                  role="button"
                  tabIndex={0}
                  className={`spatial-task-route active ${taskRouteTone(route)}${route.task_id === selectedTaskId ? " selected" : ""}${aiFocused ? " assistant-focus" : ""}`}
                  data-ai-focus={aiFocused || undefined}
                  aria-label={`当前任务 ${route.task_id}，${taskStatusLabels[route.task_status] ?? route.task_status}`}
                  data-route-id={route.route_id}
                  data-task-id={route.task_id}
                  onClick={() => onSelectTask(route.task_id)}
                  onDoubleClick={() => onOpenTask(route.task_id)}
                  onKeyDown={(event) => activateWithKeyboard(event, () => onSelectTask(route.task_id))}
                >
                  <polyline className="route-hit" points={points} />
                  <polyline className="route-line" points={points} />
                </g>
              );
            })}

            {layers.tasks && layers.candidate && candidateRoutes.map((route) => {
              const points = spatialRoutePolylinePoints(layout, route.legs);
              const aiFocused = assistantFocus?.task_ids.includes(route.task_id) ?? false;
              return (
                <g
                  key={route.route_id}
                  role="button"
                  tabIndex={0}
                  className={`spatial-task-route candidate${route.task_id === selectedTaskId ? " selected" : ""}${aiFocused ? " assistant-focus" : ""}`}
                  data-ai-focus={aiFocused || undefined}
                  aria-label={`候选任务 ${route.task_id}，采用前不替换当前路线`}
                  data-route-id={route.route_id}
                  data-task-id={route.task_id}
                  onClick={() => onSelectTask(route.task_id)}
                  onDoubleClick={() => onOpenTask(route.task_id)}
                  onKeyDown={(event) => activateWithKeyboard(event, () => onSelectTask(route.task_id))}
                >
                  <polyline className="route-hit" points={points} />
                  <polyline className="route-line" points={points} />
                </g>
              );
            })}

            {layers.resources && overlay.resource_markers.map((marker) => {
              const [anchorX, anchorY] = toCanvas(marker.position, layout);
              const [offsetX, offsetY] = markerOffsets.get(`resource:${marker.resource_id}`) ?? [0, 0];
              const x = anchorX + offsetX;
              const y = anchorY + offsetY;
              const aiFocused = assistantFocus?.resource_ids.includes(marker.resource_id) ?? false;
              return (
                <g
                  key={marker.resource_id}
                  role="button"
                  tabIndex={0}
                  className={`spatial-resource-marker ${marker.status}${marker.resource_id === selectedResourceId ? " selected" : ""}${aiFocused ? " assistant-focus" : ""}`}
                  data-ai-focus={aiFocused || undefined}
                  aria-label={`资源 ${marker.resource_id}，${resourceStatusLabels[marker.status] ?? marker.status}，进度 ${marker.progress_pct}%`}
                  data-resource-id={marker.resource_id}
                  data-authoritative-x={anchorX.toFixed(2)}
                  data-authoritative-y={anchorY.toFixed(2)}
                  onClick={() => onSelectResource(marker.resource_id)}
                  onDoubleClick={() => onOpenResource(marker.resource_id)}
                  onKeyDown={(event) => activateWithKeyboard(event, () => onSelectResource(marker.resource_id))}
                >
                  {(offsetX !== 0 || offsetY !== 0) && <line className="marker-anchor-line" x1={anchorX} y1={anchorY} x2={x} y2={y} />}
                  <rect x={x - 43} y={y - 15} width={86} height={30} rx={6} />
                  <BusFront x={x - 35} y={y - 8} size={16} />
                  <text x={x + 6} y={y + 5}>{marker.resource_id}</text>
                </g>
              );
            })}

            {layers.events && overlay.event_markers.map((marker, index) => {
              const [anchorX, anchorY] = toCanvas(marker.position, layout);
              const [offsetX, offsetY] = markerOffsets.get(`event:${marker.event_id}`) ?? [0, 0];
              const x = anchorX + offsetX;
              const y = anchorY + offsetY;
              const aiFocused = assistantFocus?.event_ids.includes(marker.event_id) ?? false;
              return (
                <g
                  key={marker.event_id}
                  role="button"
                  tabIndex={0}
                  className={`spatial-event-marker ${marker.status}${marker.event_id === selectedEventId ? " selected" : ""}${aiFocused ? " assistant-focus" : ""}`}
                  data-ai-focus={aiFocused || undefined}
                  aria-label={`事件 ${marker.event_id}，${eventStatusLabels[marker.status] ?? marker.status}，${marker.detail}`}
                  data-event-id={marker.event_id}
                  data-authoritative-x={anchorX.toFixed(2)}
                  data-authoritative-y={anchorY.toFixed(2)}
                  onClick={() => onSelectEvent(marker.event_id)}
                  onDoubleClick={() => onOpenEvent(marker.event_id)}
                  onKeyDown={(event) => activateWithKeyboard(event, () => onSelectEvent(marker.event_id))}
                >
                  {(offsetX !== 0 || offsetY !== 0) && <line className="marker-anchor-line" x1={anchorX} y1={anchorY} x2={x} y2={y} />}
                  <circle cx={x} cy={y} r={17} />
                  <Plane x={x - 9} y={y - 9} size={18} />
                  <text x={x} y={y + 31}>E{index + 1}</text>
                </g>
              );
            })}
          </svg>
          <div className="spatial-map-stamp">
            <ShieldCheck size={14} />本地原创底图 · normalized Cartesian · 非真实机场地图
          </div>
        </div>

        <aside className="spatial-inspector" aria-label="地图对象核对">
          <div className="spatial-inspector-heading">
            <p className="eyebrow">一一对应核对</p>
            <h3>当前选中对象</h3>
            <span>选择只改变地图与现有详情焦点，不改变 R{overlay.revision}</span>
          </div>

          <div className="spatial-task-register">
            <div>
              <strong>当前任务路线清册</strong>
              <span>{activeRoutes.length}/{coverage.source_task_ids.length} 条已投影</span>
            </div>
            <div className="spatial-task-register-grid">
              {activeRoutes.map((route) => (
                <button
                  key={route.route_id}
                  className={`${taskRouteTone(route)}${route.task_id === selectedTaskId ? " selected" : ""}${assistantFocus?.task_ids.includes(route.task_id) ? " assistant-focus" : ""}`}
                  aria-pressed={route.task_id === selectedTaskId}
                  onClick={() => onSelectTask(route.task_id)}
                  onDoubleClick={() => onOpenTask(route.task_id)}
                >
                  <strong>{route.task_id}</strong>
                  <span>{taskStatusLabels[route.task_status] ?? route.task_status}</span>
                  <small>{route.origin_zone_id} → {route.destination_zone_id}</small>
                  {candidateRoutes.some((item) => item.task_id === route.task_id) && <b>候选有变化</b>}
                </button>
              ))}
            </div>
          </div>

          <article className={`spatial-focus-card task${selectedActiveRoute ? " active" : ""}`}>
            <Route size={19} />
            <div>
              <span>任务路线</span>
              <strong>{selectedActiveRoute?.task_id ?? "尚未选择"}</strong>
              <p>{selectedActiveRoute
                ? `${selectedActiveRoute.origin_zone_id} → ${selectedActiveRoute.destination_zone_id} · ${taskStatusLabels[selectedActiveRoute.task_status]}`
                : "在地图上选择一条当前实线。"}</p>
              {selectedCandidateRoute && <small>候选变化：{selectedCandidateRoute.change_kind}，采用前不替换当前路线</small>}
            </div>
            {selectedActiveRoute && <button onClick={() => onOpenTask(selectedActiveRoute.task_id)}>详情</button>}
          </article>

          <article className={`spatial-focus-card resource${selectedResource ? " active" : ""}`}>
            <BusFront size={19} />
            <div>
              <span>资源位置</span>
              <strong>{selectedResource?.resource_id ?? "尚未选择"}</strong>
              <p>{selectedResource
                ? `${resourceStatusLabels[selectedResource.status]} · ${selectedResource.from_zone_id}${selectedResource.to_zone_id ? ` → ${selectedResource.to_zone_id} ${selectedResource.progress_pct}%` : ""}`
                : "在地图上选择一个资源标记。"}</p>
            </div>
            {selectedResource && <button onClick={() => onOpenResource(selectedResource.resource_id)}>详情</button>}
          </article>

          <article className={`spatial-focus-card event${selectedEvent ? " active" : ""}`}>
            <Plane size={19} />
            <div>
              <span>突发事件</span>
              <strong>{selectedEvent?.event_id ?? "尚未选择"}</strong>
              <p>{selectedEvent
                ? `${selectedEvent.primary_zone_id} · ${eventStatusLabels[selectedEvent.status]}`
                : "在地图上选择一个事件标记。"}</p>
              {selectedEvent && <small>{selectedEvent.detail}</small>}
            </div>
            {selectedEvent && <button onClick={() => onOpenEvent(selectedEvent.event_id)}>详情</button>}
          </article>

          <div className="spatial-process-key">
            <strong>过程显示规则</strong>
            <span><i className="solid" />当前方案始终为实线</span>
            <span><i className="dashed" />候选只显示真实变化</span>
            <span><i className="moving" />资源位置来自后端进度</span>
          </div>
        </aside>
      </div>

      <footer className="spatial-panel-footer">
        <AlertTriangle size={15} />
        <span>{activeView.safety_notice}</span>
        <strong>地图不拥有时钟、事件或方案决定</strong>
      </footer>
    </section>
  );
}
