import type {
  DemoDataSource,
  DemoLoadFailure,
  DemoLoadResult,
  DemoPayload,
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
