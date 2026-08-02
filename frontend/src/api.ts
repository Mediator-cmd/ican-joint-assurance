import type {
  DemoDataSource,
  DemoLoadFailure,
  DemoLoadResult,
  DemoPayload,
  PlanListResponse,
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
  for (const item of sessions.items) {
    const snapshot = await getRuntimeSession(item.session_id, options);
    if (
      snapshot.initial_scenario_version === scenario.version
      && snapshot.initial_plan_id === initialPlanId
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
