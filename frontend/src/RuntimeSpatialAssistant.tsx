import {
  AlertTriangle,
  Bot,
  Crosshair,
  FileCheck2,
  LoaderCircle,
  MapPinned,
  Quote,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiRequestError, createRuntimeSpatialQuestion } from "./api";
import type {
  AssistanceMode,
  RuntimeConnectionStatus,
  RuntimeSpatialView,
  SpatialFact,
  SpatialQuestionResponse,
  SpatialQuestionSelection,
} from "./types";


const fallbackLabels: Record<string, string> = {
  model_not_configured: "模型未配置 · 已使用空间规则回答",
  model_timeout: "模型超时 · 已回退空间规则",
  provider_error: "模型服务异常 · 已回退空间规则",
  invalid_model_output: "模型引用或聚焦校验失败 · 已回退空间规则",
  question_not_grounded: "当前空间事实不足 · 未调用模型猜测",
};

function sourceLabel(response: SpatialQuestionResponse): string {
  if (response.trace.source === "language_model") {
    return `模型辅助回答 · ${response.trace.model_label ?? "已配置模型"}`;
  }
  return response.trace.fallback_reason
    ? fallbackLabels[response.trace.fallback_reason] ?? "确定性空间规则"
    : "确定性空间规则";
}

function publicError(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.message
    : "空间问答服务暂时无法完成请求，请稍后重试。";
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function selectionLabel(selection: SpatialQuestionSelection): string {
  const selected = [selection.task_id, selection.resource_id, selection.event_id].filter(Boolean);
  return selected.length > 0 ? selected.join(" · ") : "未限定对象";
}

function firstSelectableEntity(
  fact: SpatialFact,
  view: RuntimeSpatialView,
): { kind: "task" | "resource" | "event"; id: string } | null {
  const taskIds = new Set(view.coverage.source_task_ids);
  const resourceIds = new Set(view.coverage.source_resource_ids);
  const eventIds = new Set(view.coverage.source_event_ids);
  for (const entityId of fact.entity_ids) {
    if (taskIds.has(entityId)) return { kind: "task", id: entityId };
    if (resourceIds.has(entityId)) return { kind: "resource", id: entityId };
    if (eventIds.has(entityId)) return { kind: "event", id: entityId };
  }
  return null;
}

export function SpatialQuestionResult({
  response,
  view,
  onSelectTask,
  onSelectResource,
  onSelectEvent,
}: {
  response: SpatialQuestionResponse;
  view: RuntimeSpatialView;
  onSelectTask: (taskId: string) => void;
  onSelectResource: (resourceId: string) => void;
  onSelectEvent: (eventId: string) => void;
}) {
  const facts = new Map(response.facts.map((fact) => [fact.fact_id, fact]));
  const focusItems = [
    ...response.focus.task_ids.map((id) => ({ kind: "任务", id, action: () => onSelectTask(id) })),
    ...response.focus.resource_ids.map((id) => ({ kind: "资源", id, action: () => onSelectResource(id) })),
    ...response.focus.event_ids.map((id) => ({ kind: "事件", id, action: () => onSelectEvent(id) })),
    ...response.focus.zone_ids.map((id) => ({ kind: "区域", id, action: null })),
  ];

  const activateFact = (fact: SpatialFact) => {
    const target = firstSelectableEntity(fact, view);
    if (target?.kind === "task") onSelectTask(target.id);
    if (target?.kind === "resource") onSelectResource(target.id);
    if (target?.kind === "event") onSelectEvent(target.id);
  };

  return (
    <div className={`spatial-answer-result ${response.answer.status}`}>
      <div className="spatial-answer-meta">
        <span>{response.question_id} · R{response.basis.revision} · {formatTime(response.basis.simulation_time)}</span>
        <strong>{sourceLabel(response)}</strong>
      </div>
      <blockquote>{response.question}</blockquote>
      <article className="spatial-direct-answer">
        <div><Bot size={19} /><span>{response.answer.status === "answered" ? "已按当前空间事实回答" : "权威空间事实不足"}</span></div>
        <p>{response.answer.statement}</p>
      </article>

      {response.answer.matched_entity_ids.length > 0 && (
        <div className="spatial-answer-entities">
          <span>已识别对象</span>
          {response.answer.matched_entity_ids.map((entityId) => <code key={entityId}>{entityId}</code>)}
        </div>
      )}

      {focusItems.length > 0 && (
        <div className="spatial-answer-focus" aria-label="AI 地图聚焦对象">
          <div><Crosshair size={16} /><strong>地图已聚焦</strong><span>只改变视觉高亮</span></div>
          <div>
            {focusItems.map((item) => item.action ? (
              <button key={`${item.kind}-${item.id}`} onClick={item.action}><small>{item.kind}</small>{item.id}</button>
            ) : (
              <span key={`${item.kind}-${item.id}`}><small>{item.kind}</small>{item.id}</span>
            ))}
          </div>
        </div>
      )}

      {response.answer.fact_ids.length > 0 && (
        <div className="spatial-answer-facts">
          <header><Quote size={16} /><strong>当前 revision 的权威引用</strong></header>
          {response.answer.fact_ids.map((factId) => {
            const fact = facts.get(factId);
            const selectable = fact ? firstSelectableEntity(fact, view) : null;
            return (
              <button
                key={factId}
                type="button"
                disabled={!selectable}
                onClick={() => fact && activateFact(fact)}
              >
                <code>{factId}</code>
                <span>{fact?.claim ?? "引用事实未同步"}</span>
              </button>
            );
          })}
        </div>
      )}

      {response.unresolved_questions.length > 0 && (
        <div className="spatial-answer-unresolved">
          <AlertTriangle size={17} />
          <div><strong>仍需人工确认</strong>{response.unresolved_questions.map((item) => <p key={item}>{item}</p>)}</div>
        </div>
      )}
      <footer><AlertTriangle size={14} /><span>{response.safety_notice}</span></footer>
    </div>
  );
}

interface RuntimeSpatialAssistantProps {
  view: RuntimeSpatialView;
  connectionStatus: RuntimeConnectionStatus;
  selectedTaskId: string;
  selectedResourceId: string;
  selectedEventId: string;
  onSelectTask: (taskId: string) => void;
  onSelectResource: (resourceId: string) => void;
  onSelectEvent: (eventId: string) => void;
  onAnswerChange: (response: SpatialQuestionResponse | null) => void;
}

export default function RuntimeSpatialAssistant({
  view,
  connectionStatus,
  selectedTaskId,
  selectedResourceId,
  selectedEventId,
  onSelectTask,
  onSelectResource,
  onSelectEvent,
  onAnswerChange,
}: RuntimeSpatialAssistantProps) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AssistanceMode>("auto");
  const [response, setResponse] = useState<SpatialQuestionResponse | null>(null);
  const [requestSignature, setRequestSignature] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestSignature = useRef("");
  const selection: SpatialQuestionSelection = {
    task_id: selectedTaskId || null,
    resource_id: selectedResourceId || null,
    event_id: selectedEventId || null,
  };
  const currentSignature = [
    view.overlay.session_id,
    view.overlay.revision,
    question.trim(),
    mode,
    selection.task_id ?? "",
    selection.resource_id ?? "",
    selection.event_id ?? "",
  ].join("|");
  latestSignature.current = currentSignature;
  const activeResponse = response !== null
    && requestSignature === currentSignature
    && response.basis.session_id === view.overlay.session_id
    && response.basis.revision === view.overlay.revision
    && response.basis.layout_id === view.layout.layout_id
    ? response
    : null;

  useEffect(() => {
    if (response !== null && activeResponse === null) {
      setResponse(null);
      setRequestSignature("");
      onAnswerChange(null);
    }
  }, [activeResponse, onAnswerChange, response]);

  const requestAnswer = async () => {
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || busy || connectionStatus === "offline_readonly") return;
    const signature = currentSignature;
    setBusy(true);
    setError(null);
    try {
      const next = await createRuntimeSpatialQuestion(
        view.overlay.session_id,
        view.overlay.revision,
        normalizedQuestion,
        selection,
        mode,
      );
      if (latestSignature.current !== signature) return;
      if (next.basis.session_id !== view.overlay.session_id
        || next.basis.revision !== view.overlay.revision
        || next.basis.layout_id !== view.layout.layout_id) {
        setError("空间回答已落后于当前运行修订，已停止显示，请重新提问。");
        onAnswerChange(null);
        return;
      }
      setResponse(next);
      setRequestSignature(signature);
      onAnswerChange(next);
    } catch (requestError) {
      if (latestSignature.current === signature) {
        setError(publicError(requestError));
        onAnswerChange(null);
      }
    } finally {
      setBusy(false);
    }
  };

  const usePrompt = (value: string) => {
    setQuestion(value);
    setError(null);
  };

  return (
    <section className="workspace-section spatial-assistant-panel" aria-labelledby="spatial-assistant-title">
      <div className="assistant-heading spatial-assistant-heading">
        <div className="assistant-symbol"><MapPinned size={22} /></div>
        <div>
          <p className="eyebrow">地图事实联动</p>
          <h2 id="spatial-assistant-title">空间态势 AI 问答</h2>
          <p>绑定 {view.overlay.session_id} · R{view.overlay.revision} · 当前选择 {selectionLabel(selection)}</p>
        </div>
        <div className="assistant-mode" role="group" aria-label="空间问答模式">
          <button type="button" className={mode === "auto" ? "active" : undefined} aria-pressed={mode === "auto"} onClick={() => setMode("auto")}><Sparkles size={15} />智能辅助</button>
          <button type="button" className={mode === "deterministic_only" ? "active" : undefined} aria-pressed={mode === "deterministic_only"} onClick={() => setMode("deterministic_only")}><FileCheck2 size={15} />仅规则</button>
        </div>
      </div>

      <div className="spatial-question-compose">
        <label>
          <span>询问当前地图中的事件、任务、资源、路线或处置边界</span>
          <textarea
            value={question}
            maxLength={500}
            placeholder="例如：task4 现在在哪里，由哪个资源执行？也可以问“事件2发生在哪里”。"
            onChange={(event) => setQuestion(event.target.value)}
          />
        </label>
        <div className="spatial-question-presets" aria-label="空间问题示例">
          <button type="button" onClick={() => usePrompt("当前机场空间态势是什么情况？")}>整体态势</button>
          <button type="button" onClick={() => usePrompt(selectedEventId ? `${selectedEventId}发生在哪里，现在是什么状态？` : "事件2发生在哪里，现在是什么状态？")}>事件位置</button>
          <button type="button" onClick={() => usePrompt(selectedTaskId ? `${selectedTaskId}当前路线是什么，由哪个资源执行？` : "task4当前路线是什么，由哪个资源执行？")}>任务路线</button>
          <button type="button" onClick={() => usePrompt("候选虚线改变了哪些任务，采用前应注意什么？")}>候选变化</button>
        </div>
        <button
          type="button"
          className="assistant-primary-action"
          disabled={!question.trim() || busy || connectionStatus === "offline_readonly"}
          onClick={() => void requestAnswer()}
        >
          {busy ? <LoaderCircle className="spin" size={18} /> : <Bot size={18} />}
          结合当前地图回答
        </button>
      </div>

      {connectionStatus === "offline_readonly" && <div className="assistant-alert"><AlertTriangle size={18} /><span>运行服务处于只读保护，空间问答已禁用；地图仍保留最后一次权威快照。</span></div>}
      {error && <div className="assistant-alert" role="alert"><AlertTriangle size={18} /><span>{error}</span></div>}
      {activeResponse && (
        <SpatialQuestionResult
          response={activeResponse}
          view={view}
          onSelectTask={onSelectTask}
          onSelectResource={onSelectResource}
          onSelectEvent={onSelectEvent}
        />
      )}
    </section>
  );
}
