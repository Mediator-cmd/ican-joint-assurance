import type {
  AssistanceMode,
  DemoDataSource,
  DemoLoadFailure,
  DemoLoadResult,
  DemoPayload,
  EventDraftResponse,
  ExplanationFocus,
  PlanListResponse,
  PlanExplanationResponse,
  PlanningObjectiveProfile,
  PlanRecord,
  RuntimeSessionListResponse,
  RuntimeSessionSnapshot,
  SimulationSpeed,
} from "./types";

export type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface DemoLoadOptions {
  fetcher?: Fetcher;
  apiUrl?: string;
  staticUrl?: string;
  cacheBust?: string;
  signal?: AbortSignal;
}

interface AttemptSuccess {
  ok: true;
  payload: DemoPayload;
}

interface AttemptFailure {
  ok: false;
  failure: DemoLoadFailure;
}

type AttemptResult = AttemptSuccess | AttemptFailure;

const DEFAULT_API_URL = "/api/v1/demo";
const PUBLIC_LOAD_ERROR =
  "数据服务暂不可用，本地演示数据也未能载入。请确认项目服务已启动后重试。";

export class DemoPayloadLoadError extends Error {
  readonly failures: readonly DemoLoadFailure[];

  constructor(failures: readonly DemoLoadFailure[]) {
    super(PUBLIC_LOAD_ERROR);
    this.name = "DemoPayloadLoadError";
    this.failures = failures;
  }
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDemoPayload(value: unknown): value is DemoPayload {
  if (!isRecord(value) || !isRecord(value.project) || !isRecord(value.views)) {
    return false;
  }

  if (!Array.isArray(value.events) || !Array.isArray(value.changes)) {
    return false;
  }

  const views = value.views;
  return ["baseline", "after_events_fifo", "optimized"].every((key) => {
    const view = views[key];
    return isRecord(view) && isRecord(view.scenario) && isRecord(view.plan);
  });
}

function staticDemoUrl(cacheBust: string, configuredUrl?: string): string {
  const baseUrl = import.meta.env.BASE_URL.endsWith("/")
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`;
  const url = configuredUrl ?? `${baseUrl}demo-output.json`;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}reload=${encodeURIComponent(cacheBust)}`;
}

async function attemptLoad(
  fetcher: Fetcher,
  source: DemoDataSource,
  url: string,
  signal?: AbortSignal,
): Promise<AttemptResult> {
  let response: Response;

  try {
    response = await fetcher(url, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    });
  } catch {
    return {
      ok: false,
      failure: { source, reason: "network_error" },
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      failure: { source, reason: "http_error", status: response.status },
    };
  }

  try {
    const payload: unknown = await response.json();
    if (!isDemoPayload(payload)) {
      return {
        ok: false,
        failure: { source, reason: "invalid_response" },
      };
    }
    return { ok: true, payload };
  } catch {
    return {
      ok: false,
      failure: { source, reason: "invalid_response" },
    };
  }
}

export async function loadDemoPayload(options: DemoLoadOptions = {}): Promise<DemoLoadResult> {
  const fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
  const cacheBust = options.cacheBust ?? String(Date.now());

  const apiAttempt = await attemptLoad(
    fetcher,
    "api",
    options.apiUrl ?? DEFAULT_API_URL,
    options.signal,
  );
  if (apiAttempt.ok) {
    return { payload: apiAttempt.payload, source: "api" };
  }

  const staticAttempt = await attemptLoad(
    fetcher,
    "static_fallback",
    staticDemoUrl(cacheBust, options.staticUrl),
    options.signal,
  );
  if (staticAttempt.ok) {
    return { payload: staticAttempt.payload, source: "static_fallback" };
  }

  throw new DemoPayloadLoadError([apiAttempt.failure, staticAttempt.failure]);
}

interface ApiRequestOptions {
  fetcher?: Fetcher;
  signal?: AbortSignal;
  method?: "GET" | "POST";
  body?: unknown;
}

function apiErrorDetails(value: unknown): { code: string; message: string } | null {
  if (!isRecord(value) || !isRecord(value.error)) return null;
  const code = value.error.code;
  const message = value.error.message;
  return typeof code === "string" && typeof message === "string"
    ? { code, message }
    : null;
}

async function requestJson<T>(url: string, options: ApiRequestOptions = {}): Promise<T> {
  const fetcher = options.fetcher ?? ((input, init) => fetch(input, init));
  let response: Response;
  try {
    response = await fetcher(url, {
      method: options.method ?? "GET",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch {
    throw new ApiRequestError(0, "network_error", "运行服务暂时无法连接，请稍后重试。");
  }

  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    if (response.ok) {
      throw new ApiRequestError(
        response.status,
        "invalid_response",
        "运行服务返回了无法识别的数据，请稍后重试。",
      );
    }
  }

  if (!response.ok) {
    const details = apiErrorDetails(parsed);
    throw new ApiRequestError(
      response.status,
      details?.code ?? `http_${response.status}`,
      details?.message ?? "运行服务暂时无法完成请求，请刷新状态后重试。",
    );
  }
  return parsed as T;
}

function isRuntimeSnapshot(value: unknown): value is RuntimeSessionSnapshot {
  return isRecord(value)
    && typeof value.session_id === "string"
    && typeof value.revision === "number"
    && typeof value.status === "string"
    && isRecord(value.clock)
    && Array.isArray(value.tasks)
    && Array.isArray(value.resources)
    && Array.isArray(value.flights)
    && Array.isArray(value.events);
}

function requireRuntimeSnapshot(value: RuntimeSessionSnapshot): RuntimeSessionSnapshot {
  if (!isRuntimeSnapshot(value)) {
    throw new ApiRequestError(200, "invalid_response", "运行快照结构不完整，请稍后重试。");
  }
  return value;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isAssistanceTrace(value: unknown): boolean {
  return isRecord(value)
    && ["language_model", "deterministic_rules"].includes(String(value.source))
    && typeof value.provider_attempted === "boolean"
    && (value.model_label === null || typeof value.model_label === "string")
    && (value.fallback_reason === null || typeof value.fallback_reason === "string");
}

function isEventDraftResponse(value: unknown): value is EventDraftResponse {
  if (
    !isRecord(value)
    || typeof value.draft_id !== "string"
    || !isRecord(value.basis)
    || !["ready_for_review", "needs_clarification", "unsupported"].includes(String(value.status))
    || !isAssistanceTrace(value.trace)
    || !Array.isArray(value.evidence)
    || !isStringArray(value.missing_fields)
    || !Array.isArray(value.clarification_questions)
    || !isStringArray(value.warnings)
    || value.requires_human_confirmation !== true
    || value.applies_automatically !== false
    || typeof value.safety_notice !== "string"
  ) {
    return false;
  }
  const basis = value.basis;
  if (
    typeof basis.scenario_id !== "string"
    || typeof basis.scenario_version !== "number"
    || typeof basis.reference_time !== "string"
    || (basis.runtime_session_id !== null && typeof basis.runtime_session_id !== "string")
    || (basis.runtime_revision !== null && typeof basis.runtime_revision !== "number")
    || ((basis.runtime_session_id === null) !== (basis.runtime_revision === null))
  ) {
    return false;
  }
  return value.status === "ready_for_review"
    ? isRecord(value.event) && typeof value.event.event_id === "string"
    : value.event === null;
}

function isExplanationClaim(value: unknown): boolean {
  return isRecord(value)
    && typeof value.statement === "string"
    && isStringArray(value.evidence_ids)
    && value.evidence_ids.length > 0;
}

function isPlanExplanationResponse(value: unknown): value is PlanExplanationResponse {
  if (
    !isRecord(value)
    || typeof value.explanation_id !== "string"
    || !isRecord(value.context)
    || value.context.scope !== "runtime_plan"
    || typeof value.context.session_id !== "string"
    || typeof value.context.revision !== "number"
    || typeof value.context.plan_id !== "string"
    || (value.context.baseline_plan_id !== null
      && typeof value.context.baseline_plan_id !== "string")
    || !isAssistanceTrace(value.trace)
    || !isExplanationClaim(value.summary)
    || !Array.isArray(value.tradeoffs)
    || !value.tradeoffs.every(isExplanationClaim)
    || !isExplanationClaim(value.recommended_next_step)
    || !Array.isArray(value.evidence)
    || !isStringArray(value.unresolved_questions)
    || value.requires_human_confirmation !== true
    || value.modifies_plan !== false
    || typeof value.safety_notice !== "string"
  ) {
    return false;
  }
  const evidenceIds = new Set<string>();
  for (const item of value.evidence) {
    if (!isRecord(item) || typeof item.evidence_id !== "string") return false;
    evidenceIds.add(item.evidence_id);
  }
  const claims = [value.summary, ...value.tradeoffs, value.recommended_next_step];
  return claims.every((claim) => claim.evidence_ids.every((id: string) => evidenceIds.has(id)));
}

function requireEventDraftResponse(value: unknown): EventDraftResponse {
  if (!isEventDraftResponse(value)) {
    throw new ApiRequestError(200, "invalid_response", "事件草稿结构不完整，请稍后重试。");
  }
  return value;
}

function requirePlanExplanationResponse(value: unknown): PlanExplanationResponse {
  if (!isPlanExplanationResponse(value)) {
    throw new ApiRequestError(200, "invalid_response", "方案解释结构不完整，请稍后重试。");
  }
  return value;
}

function query(parameters: Record<string, string | number>): string {
  const search = new URLSearchParams();
  Object.entries(parameters).forEach(([key, value]) => search.set(key, String(value)));
  return search.toString();
}

export interface RuntimeApiOptions {
  fetcher?: Fetcher;
  signal?: AbortSignal;
}

export async function listOptimizedPlans(
  scenarioId: string,
  scenarioVersion: number,
  options: RuntimeApiOptions = {},
): Promise<PlanListResponse> {
  return requestJson<PlanListResponse>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/plans?${query({
      version: scenarioVersion,
      algorithm: "cp_sat",
    })}`,
    options,
  );
}

export async function createOptimizedPlan(
  scenarioId: string,
  scenarioVersion: number,
  options: RuntimeApiOptions = {},
): Promise<PlanRecord> {
  return requestJson<PlanRecord>(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/plans`,
    {
      ...options,
      method: "POST",
      body: { expected_version: scenarioVersion, algorithm: "cp_sat", max_time_seconds: 5 },
    },
  );
}

export async function listRuntimeSessions(
  scenarioId: string,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionListResponse> {
  return requestJson<RuntimeSessionListResponse>(
    `/api/v1/runtime-sessions?${query({ scenario_id: scenarioId, offset: 0, limit: 100 })}`,
    options,
  );
}

export async function getRuntimeSession(
  sessionId: string,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionSnapshot> {
  return requireRuntimeSnapshot(await requestJson<RuntimeSessionSnapshot>(
    `/api/v1/runtime-sessions/${encodeURIComponent(sessionId)}`,
    options,
  ));
}

export async function createRuntimeSession(
  scenarioId: string,
  scenarioVersion: number,
  activePlanId: string,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionSnapshot> {
  return requireRuntimeSnapshot(await requestJson<RuntimeSessionSnapshot>(
    "/api/v1/runtime-sessions",
    {
      ...options,
      method: "POST",
      body: {
        scenario_id: scenarioId,
        scenario_version: scenarioVersion,
        active_plan_id: activePlanId,
        speed: 1,
      },
    },
  ));
}

export type RuntimeAction =
  | "start"
  | "pause"
  | "speed"
  | "reset"
  | "replan"
  | "candidate/accept"
  | "candidate/reject";

export async function postRuntimeAction(
  sessionId: string,
  action: RuntimeAction,
  body: Record<string, unknown>,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionSnapshot> {
  return requireRuntimeSnapshot(await requestJson<RuntimeSessionSnapshot>(
    `/api/v1/runtime-sessions/${encodeURIComponent(sessionId)}/${action}`,
    { ...options, method: "POST", body },
  ));
}

export async function createRuntimeEventDraft(
  sessionId: string,
  expectedRevision: number,
  text: string,
  assistanceMode: AssistanceMode,
  options: RuntimeApiOptions = {},
): Promise<EventDraftResponse> {
  return requireEventDraftResponse(await requestJson<unknown>(
    "/api/v1/assistant/event-drafts",
    {
      ...options,
      method: "POST",
      body: {
        context: {
          scope: "runtime",
          session_id: sessionId,
          expected_revision: expectedRevision,
        },
        text,
        assistance_mode: assistanceMode,
      },
    },
  ));
}

export async function submitRuntimeEventDraft(
  draft: EventDraftResponse,
  objectiveProfile: PlanningObjectiveProfile,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionSnapshot> {
  return requireRuntimeSnapshot(await requestJson<RuntimeSessionSnapshot>(
    "/api/v1/assistant/event-drafts/submit",
    {
      ...options,
      method: "POST",
      body: {
        scope: "runtime",
        draft,
        confirm_event: true,
        objective_profile: objectiveProfile,
        confirm_objective: true,
      },
    },
  ));
}

export async function createRuntimePlanExplanation(
  sessionId: string,
  revision: number,
  planId: string,
  baselinePlanId: string | null,
  focus: ExplanationFocus,
  question: string,
  assistanceMode: AssistanceMode,
  options: RuntimeApiOptions = {},
): Promise<PlanExplanationResponse> {
  const normalizedQuestion = question.trim();
  return requirePlanExplanationResponse(await requestJson<unknown>(
    "/api/v1/assistant/plan-explanations",
    {
      ...options,
      method: "POST",
      body: {
        context: {
          scope: "runtime_plan",
          session_id: sessionId,
          revision,
          plan_id: planId,
          baseline_plan_id: baselinePlanId,
        },
        focus,
        ...(normalizedQuestion ? { question: normalizedQuestion } : {}),
        assistance_mode: assistanceMode,
      },
    },
  ));
}

async function ensureOptimizedPlan(
  scenarioId: string,
  scenarioVersion: number,
  options: RuntimeApiOptions,
): Promise<string> {
  const existing = await listOptimizedPlans(scenarioId, scenarioVersion, options);
  if (existing.items[0]) return existing.items[0].plan_id;
  try {
    return (await createOptimizedPlan(scenarioId, scenarioVersion, options)).plan.plan_id;
  } catch (error) {
    if (!(error instanceof ApiRequestError) || error.code !== "plan_already_exists") throw error;
    const refreshed = await listOptimizedPlans(scenarioId, scenarioVersion, options);
    if (!refreshed.items[0]) throw error;
    return refreshed.items[0].plan_id;
  }
}

function hasExactIds(actual: string[], expected: string[]): boolean {
  if (actual.length !== expected.length) return false;
  const expectedIds = new Set(expected);
  return expectedIds.size === expected.length && actual.every((id) => expectedIds.has(id));
}

export async function findOrCreateRuntimeSession(
  payload: DemoPayload,
  options: RuntimeApiOptions = {},
): Promise<RuntimeSessionSnapshot> {
  const scenario = payload.views.baseline.scenario;
  const initialPlanId = await ensureOptimizedPlan(
    scenario.scenario_id,
    scenario.version,
    options,
  );
  const sessions = await listRuntimeSessions(scenario.scenario_id, options);
  const expectedTaskIds = scenario.tasks.map((task) => task.task_id);
  const expectedEventIds = payload.events.map((event) => event.event_id);
  for (const item of sessions.items) {
    const snapshot = await getRuntimeSession(item.session_id, options);
    if (
      snapshot.initial_scenario_version === scenario.version
      && snapshot.initial_plan_id === initialPlanId
      && hasExactIds(snapshot.tasks.map((task) => task.task_id), expectedTaskIds)
      && hasExactIds(snapshot.events.map((event) => event.event_id), expectedEventIds)
    ) {
      return snapshot;
    }
  }
  return createRuntimeSession(
    scenario.scenario_id,
    scenario.version,
    initialPlanId,
    options,
  );
}

export function runtimeStreamUrl(sessionId: string): string {
  return `/api/v1/runtime-sessions/${encodeURIComponent(sessionId)}/stream`;
}

export function speedRequest(expectedRevision: number, speed: SimulationSpeed) {
  return { expected_revision: expectedRevision, speed };
}
