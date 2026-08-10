import {
  AlertTriangle,
  Bot,
  FileCheck2,
  LoaderCircle,
  MessageSquareText,
  Quote,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";

import { ApiRequestError, createRuntimePlanExplanation } from "./api";
import type {
  AssistanceMode,
  ExplanationClaim,
  ExplanationEvidence,
  ExplanationFocus,
  PlanExplanationResponse,
  RuntimeConnectionStatus,
  RuntimeSessionSnapshot,
} from "./types";

type PlanTarget = "active" | "candidate";

const focusOptions: Array<{ value: ExplanationFocus; label: string }> = [
  { value: "summary", label: "方案摘要" },
  { value: "tradeoffs", label: "方案取舍" },
  { value: "task_changes", label: "任务变化" },
  { value: "manual_handling", label: "人工处理" },
];

const fallbackLabels: Record<string, string> = {
  model_not_configured: "模型未配置 · 规则解释",
  model_timeout: "模型超时 · 已回退规则解释",
  provider_error: "模型服务异常 · 已回退规则解释",
  invalid_model_output: "模型结果校验失败 · 已回退规则解释",
  question_not_grounded: "当前事实不足 · 未调用模型猜测",
};

function sourceLabel(explanation: PlanExplanationResponse): string {
  if (explanation.trace.source === "language_model") {
    return `模型辅助解释 · ${explanation.trace.model_label ?? "已配置模型"}`;
  }
  return explanation.trace.fallback_reason
    ? fallbackLabels[explanation.trace.fallback_reason] ?? "确定性规则解释"
    : "确定性规则解释";
}

function publicError(error: unknown): string {
  return error instanceof ApiRequestError
    ? error.message
    : "方案解释服务暂时无法完成请求，请稍后重试。";
}

function ExplanationClaimBlock({
  label,
  claim,
  evidence,
  emphasized = false,
}: {
  label: string;
  claim: ExplanationClaim;
  evidence: Map<string, ExplanationEvidence>;
  emphasized?: boolean;
}) {
  return (
    <article className={`explanation-claim${emphasized ? " emphasized" : ""}`}>
      <div className="claim-heading"><span>{label}</span><strong>{claim.statement}</strong></div>
      <div className="claim-evidence">
        {claim.evidence_ids.map((evidenceId) => {
          const fact = evidence.get(evidenceId);
          return (
            <div key={evidenceId}>
              <code>{evidenceId}</code>
              <span>{fact?.statement ?? "引用事实未同步"}</span>
            </div>
          );
        })}
      </div>
    </article>
  );
}

function ExplanationSection({
  title,
  responsibility,
  claims,
  evidence,
  emphasized,
}: {
  title: string;
  responsibility: string;
  claims: ExplanationClaim[];
  evidence: Map<string, ExplanationEvidence>;
  emphasized: boolean;
}) {
  return (
    <section className={`explanation-section${emphasized ? " emphasized" : ""}`}>
      <header>
        <h3>{title}</h3>
        <p>{responsibility}</p>
        {emphasized && <span>当前重点</span>}
      </header>
      {claims.map((claim, index) => (
        <ExplanationClaimBlock
          key={`${claim.statement}-${index}`}
          label={claims.length > 1 ? `${title} ${index + 1}` : title}
          claim={claim}
          evidence={evidence}
        />
      ))}
    </section>
  );
}

export function PlanExplanationResult({
  explanation,
  stale = false,
}: {
  explanation: PlanExplanationResponse;
  stale?: boolean;
}) {
  const evidence = new Map(
    explanation.evidence.map((item) => [item.evidence_id, item]),
  );
  return (
    <div className={`plan-explanation-result${stale ? " stale" : ""}`}>
      <div className="assistant-result-title">
        <span>{explanation.explanation_id} · {explanation.context.plan_id}</span>
        <strong>{sourceLabel(explanation)}</strong>
      </div>
      <section className={`question-answer-card ${explanation.question_answer.status}`} aria-label="针对你的问题">
        <header>
          <span>针对你的问题</span>
          <strong>{explanation.question_answer.status === "answered" ? "已按权威事实回答" : explanation.question_answer.status === "insufficient_evidence" ? "权威事实不足" : "未提出补充问题"}</strong>
        </header>
        {explanation.question && <blockquote>{explanation.question}</blockquote>}
        <p>{explanation.question_answer.statement}</p>
        {explanation.question_answer.matched_entity_ids.length > 0 && (
          <div className="matched-entities">
            <span>已匹配对象</span>
            {explanation.question_answer.matched_entity_ids.map((entityId) => <code key={entityId}>{entityId}</code>)}
          </div>
        )}
        {explanation.question_answer.evidence_ids.length > 0 && (
          <div className="claim-evidence">
            {explanation.question_answer.evidence_ids.map((evidenceId) => (
              <div key={evidenceId}><code>{evidenceId}</code><span>{evidence.get(evidenceId)?.statement ?? "引用事实未同步"}</span></div>
            ))}
          </div>
        )}
      </section>
      <div className="explanation-section-grid">
        <ExplanationSection title="方案摘要" responsibility="回答整体状态，以及它与本次问题的关系。" claims={[explanation.summary]} evidence={evidence} emphasized={explanation.focus === "summary"} />
        <ExplanationSection title="方案取舍" responsibility="解释目标、收益、代价和量化指标。" claims={explanation.tradeoffs} evidence={evidence} emphasized={explanation.focus === "tradeoffs"} />
        <ExplanationSection title="任务变化" responsibility="列明任务、资源、服务时间、路线及基线差异。" claims={explanation.task_changes} evidence={evidence} emphasized={explanation.focus === "task_changes"} />
        <ExplanationSection title="人工处理" responsibility="明确人员需要核对、协调或作出的决定。" claims={explanation.manual_handling} evidence={evidence} emphasized={explanation.focus === "manual_handling"} />
      </div>
      <ExplanationClaimBlock label="建议下一步" claim={explanation.recommended_next_step} evidence={evidence} emphasized />
      {explanation.unresolved_questions.length > 0 && (
        <div className="unresolved-questions">
          <Quote size={18} />
          <div><strong>仍需人工确认</strong>{explanation.unresolved_questions.map((item) => <p key={item}>{item}</p>)}</div>
        </div>
      )}
    </div>
  );
}

interface RuntimePlanExplanationProps {
  snapshot: RuntimeSessionSnapshot;
  connectionStatus: RuntimeConnectionStatus;
}

export default function RuntimePlanExplanation({
  snapshot,
  connectionStatus,
}: RuntimePlanExplanationProps) {
  const [target, setTarget] = useState<PlanTarget>("active");
  const [focus, setFocus] = useState<ExplanationFocus>("summary");
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<AssistanceMode>("auto");
  const [explanation, setExplanation] = useState<PlanExplanationResponse | null>(null);
  const [requestSignature, setRequestSignature] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!snapshot.candidate_plan_id && target === "candidate") setTarget("active");
  }, [snapshot.candidate_plan_id, target]);

  const planId = target === "candidate"
    ? snapshot.candidate_plan_id
    : snapshot.active_plan_id;
  const baselinePlanId = target === "candidate" ? snapshot.active_plan_id : null;
  const currentSignature = [
    snapshot.session_id,
    snapshot.revision,
    planId ?? "",
    baselinePlanId ?? "",
    focus,
    question.trim(),
    mode,
  ].join("|");
  const stale = explanation !== null && (
    explanation.context.session_id !== snapshot.session_id
    || explanation.context.revision !== snapshot.revision
    || explanation.context.plan_id !== planId
    || explanation.context.baseline_plan_id !== baselinePlanId
    || requestSignature !== currentSignature
  );
  const requestExplanation = async () => {
    if (!planId || busy || connectionStatus === "offline_readonly") return;
    const signature = currentSignature;
    setBusy(true);
    setError(null);
    try {
      const next = await createRuntimePlanExplanation(
        snapshot.session_id,
        snapshot.revision,
        planId,
        baselinePlanId,
        focus,
        question,
        mode,
      );
      setExplanation(next);
      setRequestSignature(signature);
    } catch (requestError) {
      setError(publicError(requestError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="workspace-section plan-explanation-panel" aria-labelledby="plan-explanation-title">
      <div className="assistant-heading plan-explanation-heading">
        <div className="assistant-symbol"><MessageSquareText size={22} /></div>
        <div>
          <p className="eyebrow">事实约束解释</p>
          <h2 id="plan-explanation-title">方案说明与引用依据</h2>
          <p>只读解释 · 不修改方案 · 当前会话 R{snapshot.revision}</p>
        </div>
        <div className="assistant-mode" role="group" aria-label="方案解释模式">
          <button type="button" className={mode === "auto" ? "active" : undefined} aria-pressed={mode === "auto"} onClick={() => setMode("auto")}><Sparkles size={15} />智能辅助</button>
          <button type="button" className={mode === "deterministic_only" ? "active" : undefined} aria-pressed={mode === "deterministic_only"} onClick={() => setMode("deterministic_only")}><FileCheck2 size={15} />仅规则</button>
        </div>
      </div>

      <div className="explanation-controls">
        <div className="plan-target-switch" role="group" aria-label="需要解释的方案">
          <button type="button" className={target === "active" ? "active" : undefined} aria-pressed={target === "active"} onClick={() => setTarget("active")}>
            <span>当前执行方案</span><strong>{snapshot.active_plan_id}</strong>
          </button>
          <button type="button" className={target === "candidate" ? "active" : undefined} aria-pressed={target === "candidate"} disabled={!snapshot.candidate_plan_id} onClick={() => setTarget("candidate")}>
            <span>待确认候选</span><strong>{snapshot.candidate_plan_id ?? "暂无候选"}</strong>
          </button>
        </div>
        <div className="explanation-focus" role="group" aria-label="解释重点">
          {focusOptions.map((option) => (
            <button type="button" key={option.value} className={focus === option.value ? "active" : undefined} aria-pressed={focus === option.value} onClick={() => setFocus(option.value)}>{option.label}</button>
          ))}
        </div>
        <p className="explanation-focus-note">四部分都会返回；所选重点决定哪一部分必须直接结合你的问题展开。</p>
        <label className="explanation-question">
          <span>向 AI 提问（可选）</span>
          <textarea value={question} maxLength={500} placeholder="例如：任务 TASK-… 由哪个资源执行，服务时间和路线是什么？" onChange={(event) => setQuestion(event.target.value)} />
        </label>
        <button type="button" className="assistant-primary-action" disabled={!planId || busy || connectionStatus === "offline_readonly"} onClick={() => void requestExplanation()}>
          {busy ? <LoaderCircle className="spin" size={18} /> : <Bot size={18} />}
          生成事实解释
        </button>
      </div>

      {connectionStatus === "offline_readonly" && <div className="assistant-alert"><AlertTriangle size={18} /><span>运行服务处于只读保护，解释请求已禁用。</span></div>}
      {error && <div className="assistant-alert" role="alert"><AlertTriangle size={18} /><span>{error}</span></div>}
      {stale && <div className="assistant-alert" role="status"><AlertTriangle size={18} /><span>运行 revision、方案或解释条件已经变化，以下旧解释仅供追溯，请重新生成。</span></div>}

      {explanation && <PlanExplanationResult explanation={explanation} stale={stale} />}
    </section>
  );
}
