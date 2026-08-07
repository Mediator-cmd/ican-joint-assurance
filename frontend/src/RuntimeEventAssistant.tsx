import {
  AlertTriangle,
  CheckCircle2,
  FileCheck2,
  LoaderCircle,
  Send,
  Sparkles,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ApiRequestError, createRuntimeEventDraft } from "./api";
import type {
  AssistanceMode,
  EventDraftField,
  EventDraftResponse,
  EvidenceOrigin,
  PlanningObjectiveProfile,
  RuntimeConnectionStatus,
  RuntimeSessionSnapshot,
} from "./types";

const objectiveOptions: Array<{
  value: PlanningObjectiveProfile;
  label: string;
  detail: string;
}> = [
  { value: "balanced", label: "均衡保障", detail: "兼顾完成率、等待与调整规模" },
  { value: "critical_first", label: "紧急优先", detail: "优先保护高等级保障任务" },
  { value: "minimum_wait", label: "最少等待", detail: "优先压缩总体等待时间" },
  { value: "minimum_change", label: "最少变更", detail: "优先保持既有资源安排" },
];

const fieldLabels: Record<EventDraftField, string> = {
  event_type: "事件类型",
  flight_id: "关联航班",
  occurred_at: "发生时间",
  delay_minutes: "延误时长",
  previous_gate_id: "原登机口",
  new_gate_id: "新登机口",
};

const originLabels: Record<EvidenceOrigin, string> = {
  user_text: "输入原文",
  authoritative_context: "权威上下文",
  deterministic_derivation: "确定性推导",
};

const fallbackLabels: Record<string, string> = {
  model_not_configured: "模型未配置，已使用确定性规则",
  model_timeout: "模型响应超时，已回退确定性规则",
  provider_error: "模型服务异常，已回退确定性规则",
  invalid_model_output: "模型结果未通过校验，已回退确定性规则",
};

function assistanceLabel(draft: EventDraftResponse): string {
  if (draft.trace.source === "language_model") {
    return `模型辅助 · ${draft.trace.model_label ?? "已配置模型"}`;
  }
  return draft.trace.fallback_reason
    ? fallbackLabels[draft.trace.fallback_reason] ?? "确定性规则回退"
    : "确定性规则";
}

function formatDraftTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function publicError(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.message
    : "事件辅助服务暂时无法完成请求，请稍后重试。";
}

interface RuntimeEventAssistantProps {
  snapshot: RuntimeSessionSnapshot;
  connectionStatus: RuntimeConnectionStatus;
  controlBusy: string | null;
  submitReviewedEvent: (
    draft: EventDraftResponse,
    objectiveProfile: PlanningObjectiveProfile,
  ) => Promise<boolean>;
}

export default function RuntimeEventAssistant({
  snapshot,
  connectionStatus,
  controlBusy,
  submitReviewedEvent,
}: RuntimeEventAssistantProps) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<AssistanceMode>("auto");
  const [draft, setDraft] = useState<EventDraftResponse | null>(null);
  const [draftInput, setDraftInput] = useState("");
  const [objective, setObjective] = useState<PlanningObjectiveProfile>(
    snapshot.objective_profile,
  );
  const [eventConfirmed, setEventConfirmed] = useState(false);
  const [objectiveConfirmed, setObjectiveConfirmed] = useState(false);
  const [requestBusy, setRequestBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedDraftId, setSubmittedDraftId] = useState<string | null>(null);

  const normalizedInput = text.trim();
  const draftStale = draft !== null && (
    draft.basis.runtime_session_id !== snapshot.session_id
    || draft.basis.runtime_revision !== snapshot.revision
    || draftInput !== normalizedInput
  );
  const writeDisabled = connectionStatus === "offline_readonly" || controlBusy !== null;
  const statusAllowsSubmission = snapshot.status === "ready" || snapshot.status === "paused";
  const canSubmit = draft?.status === "ready_for_review"
    && !draftStale
    && submittedDraftId !== draft.draft_id
    && eventConfirmed
    && objectiveConfirmed
    && statusAllowsSubmission
    && !writeDisabled;

  const evidenceByField = useMemo(
    () => new Map(draft?.evidence.map((item) => [item.field, item]) ?? []),
    [draft],
  );

  const createDraft = async () => {
    if (!normalizedInput || requestBusy || writeDisabled) return;
    setRequestBusy(true);
    setError(null);
    setSubmittedDraftId(null);
    try {
      const next = await createRuntimeEventDraft(
        snapshot.session_id,
        snapshot.revision,
        normalizedInput,
        mode,
      );
      setDraft(next);
      setDraftInput(normalizedInput);
      setObjective(next.objective_recommendation?.profile ?? snapshot.objective_profile);
      setEventConfirmed(false);
      setObjectiveConfirmed(false);
    } catch (requestError) {
      setError(publicError(requestError));
    } finally {
      setRequestBusy(false);
    }
  };

  const submitDraft = async () => {
    if (!draft || !canSubmit) return;
    const submitted = await submitReviewedEvent(draft, objective);
    if (submitted) setSubmittedDraftId(draft.draft_id);
  };

  const addClarification = (field: EventDraftField, value: string) => {
    const addition = `${fieldLabels[field]} ${value}`;
    setText((current) => `${current.trim()}；${addition}`);
    setError(null);
  };

  return (
    <section className="workspace-section event-assistant-panel" aria-labelledby="event-assistant-title">
      <div className="assistant-heading">
        <div className="assistant-symbol"><Sparkles size={22} /></div>
        <div>
          <p className="eyebrow">新增运行事实</p>
          <h2 id="event-assistant-title">事件辅助录入</h2>
          <p>匿名教学仿真事件 · 当前会话 R{snapshot.revision}</p>
        </div>
        <div className="assistant-mode" role="group" aria-label="事件辅助模式">
          <button
            type="button"
            className={mode === "auto" ? "active" : undefined}
            aria-pressed={mode === "auto"}
            onClick={() => setMode("auto")}
          >智能辅助</button>
          <button
            type="button"
            className={mode === "deterministic_only" ? "active" : undefined}
            aria-pressed={mode === "deterministic_only"}
            onClick={() => setMode("deterministic_only")}
          >仅规则</button>
        </div>
      </div>

      <div className="event-assistant-compose">
        <label>
          <span>事件描述</span>
          <textarea
            value={text}
            maxLength={1000}
            placeholder="例如：SIM330 于 08:25 确认延误 15 分钟"
            onChange={(event) => {
              setText(event.target.value);
              setSubmittedDraftId(null);
              setError(null);
            }}
          />
        </label>
        <button
          type="button"
          className="assistant-primary-action"
          disabled={!normalizedInput || requestBusy || writeDisabled}
          onClick={() => void createDraft()}
        >
          {requestBusy ? <LoaderCircle className="spin" size={18} /> : <FileCheck2 size={18} />}
          生成复核草稿
        </button>
      </div>

      {connectionStatus === "offline_readonly" && (
        <div className="assistant-alert"><AlertTriangle size={18} /><span>运行服务处于只读保护，事件辅助请求与提交已禁用。</span></div>
      )}
      {error && <div className="assistant-alert" role="alert"><AlertTriangle size={18} /><span>{error}</span></div>}
      {draftStale && (
        <div className="assistant-alert" role="status">
          <AlertTriangle size={18} />
          <span>{draftInput !== normalizedInput ? "事件描述已经修改，请重新生成草稿。" : "运行 revision 已变化，旧草稿不能提交。"}</span>
        </div>
      )}

      {draft && draft.status === "needs_clarification" && (
        <div className="clarification-panel">
          <div className="assistant-result-title"><span>需要补充</span><strong>{assistanceLabel(draft)}</strong></div>
          {draft.clarification_questions.map((question) => (
            <article key={question.field}>
              <div><strong>{question.question}</strong><p>{question.reason}</p></div>
              {question.options.length > 0 && (
                <div className="clarification-options">
                  {question.options.map((option) => (
                    <button
                      type="button"
                      key={option.value}
                      onClick={() => addClarification(question.field, option.value)}
                    >{option.label}</button>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      )}

      {draft && draft.status === "unsupported" && (
        <div className="assistant-alert" role="status">
          <AlertTriangle size={18} />
          <span>{draft.warnings.join("；")}</span>
        </div>
      )}

      {draft?.status === "ready_for_review" && draft.event && (
        <div className={`event-review-panel${draftStale ? " stale" : ""}`}>
          <div className="assistant-result-title">
            <span>待人工复核 · {draft.draft_id}</span>
            <strong>{assistanceLabel(draft)}</strong>
          </div>
          <div className="event-review-grid">
            <div className="event-review-facts">
              <article><span>事件类型</span><strong>{draft.event.event_type === "delay" ? "航班延误" : "登机口调整"}</strong></article>
              <article><span>关联航班</span><strong>{draft.event.flight_id}</strong></article>
              <article><span>发生时间</span><strong>{formatDraftTime(draft.event.occurred_at)}</strong></article>
              {draft.event.event_type === "delay" ? (
                <article><span>延误时长</span><strong>{draft.event.delay_minutes} 分钟</strong></article>
              ) : (
                <article><span>登机口变化</span><strong>{draft.event.previous_gate_id} → {draft.event.new_gate_id}</strong></article>
              )}
            </div>
            <div className="field-evidence-list">
              {draft.evidence.map((evidence) => (
                <article key={evidence.field}>
                  <span>{fieldLabels[evidence.field]}</span>
                  <strong>{evidence.normalized_value}</strong>
                  <small>{originLabels[evidence.origin]}{evidence.source_quote ? ` · “${evidence.source_quote}”` : ""}</small>
                </article>
              ))}
              {!evidenceByField.size && <span>未返回字段依据</span>}
            </div>
          </div>

          <fieldset className="objective-review">
            <legend>本次重规划目标</legend>
            <div className="objective-options">
              {objectiveOptions.map((option) => (
                <label key={option.value} className={objective === option.value ? "selected" : undefined}>
                  <input
                    type="radio"
                    name="planning-objective"
                    value={option.value}
                    checked={objective === option.value}
                    onChange={() => {
                      setObjective(option.value);
                      setObjectiveConfirmed(false);
                    }}
                  />
                  <span><strong>{option.label}</strong><small>{option.detail}</small></span>
                </label>
              ))}
            </div>
            {draft.objective_recommendation && (
              <p className="objective-recommendation">
                辅助建议：{draft.objective_recommendation.display_name}。{draft.objective_recommendation.rationale}
              </p>
            )}
          </fieldset>

          <div className="review-confirmations">
            <label><input type="checkbox" checked={eventConfirmed} onChange={(event) => setEventConfirmed(event.target.checked)} /><span>已核对事件字段与逐字段依据</span></label>
            <label><input type="checkbox" checked={objectiveConfirmed} onChange={(event) => setObjectiveConfirmed(event.target.checked)} /><span>已确认本次确定性调度目标</span></label>
          </div>

          <div className="review-submit-row">
            <div>
              {submittedDraftId === draft.draft_id ? (
                <span className="submission-success"><CheckCircle2 size={17} />事件已进入权威运行链，候选方案仍需人工决定。</span>
              ) : !statusAllowsSubmission ? (
                <span>请先将运行置于“准备”或“已暂停”状态。</span>
              ) : (
                <span>提交不会自动采用随后生成的候选方案。</span>
              )}
            </div>
            <button
              type="button"
              className="assistant-primary-action"
              disabled={!canSubmit}
              onClick={() => void submitDraft()}
            >
              {controlBusy === "event_submit" ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}
              提交已复核事件
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
