import { describe, expect, it, vi } from "vitest";

import {
  DemoPayloadLoadError,
  findOrCreateRuntimeSession,
  loadDemoPayload,
  postRuntimeAction,
} from "./api";
import type { Fetcher } from "./api";
import type { DemoPayload, RuntimeSessionSnapshot } from "./types";

const demoPayload = {
  project: {},
  events: [],
  changes: [],
  views: {
    baseline: { scenario: {}, plan: {} },
    after_events_fifo: { scenario: {}, plan: {} },
    optimized: { scenario: {}, plan: {} },
  },
} as unknown as DemoPayload;

const runtimeDemoPayload = {
  ...demoPayload,
  views: {
    ...demoPayload.views,
    baseline: {
      scenario: {
        scenario_id: "SCN-DEMO",
        version: 1,
      },
      plan: {},
    },
  },
} as unknown as DemoPayload;

const runtimeSnapshot = {
  session_id: "RUN-DEMO",
  scenario_id: "SCN-DEMO",
  initial_scenario_version: 1,
  initial_plan_id: "PLAN-DEMO-V1-CP-SAT",
  active_plan_id: "PLAN-DEMO-V1-CP-SAT",
  candidate_plan_id: null,
  revision: 1,
  status: "ready",
  clock: {},
  tasks: [],
  resources: [],
  flights: [],
  events: [],
} as unknown as RuntimeSessionSnapshot;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("loadDemoPayload", () => {
  it("returns API data without requesting the static fallback", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse(demoPayload));

    const result = await loadDemoPayload({ fetcher, cacheBust: "api-success" });

    expect(result).toEqual({ payload: demoPayload, source: "api" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/demo");
  });

  it("returns static data when the API request fails", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse(demoPayload));

    const result = await loadDemoPayload({ fetcher, cacheBust: "fallback" });

    expect(result).toEqual({ payload: demoPayload, source: "static_fallback" });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]?.[0]).toContain("demo-output.json?reload=fallback");
  });

  it("throws one safe error when both data sources fail", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockRejectedValueOnce(new Error("private network detail"))
      .mockResolvedValueOnce(jsonResponse({}, 404));

    const loading = loadDemoPayload({ fetcher, cacheBust: "double-failure" });

    await expect(loading).rejects.toMatchObject({
      name: "DemoPayloadLoadError",
      message: "数据服务暂不可用，本地演示数据也未能载入。请确认项目服务已启动后重试。",
      failures: [
        { source: "api", reason: "network_error" },
        { source: "static_fallback", reason: "http_error", status: 404 },
      ],
    });
    await expect(loading).rejects.not.toThrow("private network detail");
  });

  it("runs the complete chain again and recovers on retry", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({}, 503))
      .mockResolvedValueOnce(jsonResponse({}, 404))
      .mockResolvedValueOnce(jsonResponse(demoPayload));

    await expect(
      loadDemoPayload({ fetcher, cacheBust: "first-attempt" }),
    ).rejects.toBeInstanceOf(DemoPayloadLoadError);

    const retryResult = await loadDemoPayload({ fetcher, cacheBust: "retry" });

    expect(retryResult.source).toBe("api");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/demo",
      expect.stringContaining("demo-output.json?reload=first-attempt"),
      "/api/v1/demo",
    ]);
  });
});

describe("findOrCreateRuntimeSession", () => {
  it("finds the newest matching SQLite session without creating another", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({
        items: [{ plan_id: "PLAN-DEMO-V1-CP-SAT" }],
        total: 1,
        storage_scope: "process_memory",
      }))
      .mockResolvedValueOnce(jsonResponse({
        items: [{ session_id: "RUN-DEMO" }],
        total: 1,
        offset: 0,
        limit: 100,
        storage_scope: "sqlite",
        safety_notice: "safe",
      }))
      .mockResolvedValueOnce(jsonResponse(runtimeSnapshot));

    const result = await findOrCreateRuntimeSession(runtimeDemoPayload, { fetcher });

    expect(result.session_id).toBe("RUN-DEMO");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.every((call) => call[1]?.method === "GET")).toBe(true);
  });

  it("creates one optimized plan and one session when neither exists", async () => {
    const fetcher = vi
      .fn<Fetcher>()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, storage_scope: "process_memory" }))
      .mockResolvedValueOnce(jsonResponse({
        plan: { plan_id: "PLAN-DEMO-V1-CP-SAT" },
        guidance: {},
        storage_scope: "process_memory",
        safety_notice: "safe",
      }, 201))
      .mockResolvedValueOnce(jsonResponse({
        items: [],
        total: 0,
        offset: 0,
        limit: 100,
        storage_scope: "sqlite",
        safety_notice: "safe",
      }))
      .mockResolvedValueOnce(jsonResponse(runtimeSnapshot, 201));

    const result = await findOrCreateRuntimeSession(runtimeDemoPayload, { fetcher });

    expect(result.session_id).toBe("RUN-DEMO");
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(fetcher.mock.calls[1]?.[1]?.method).toBe("POST");
    expect(fetcher.mock.calls[3]?.[1]?.method).toBe("POST");
    expect(fetcher.mock.calls[3]?.[1]?.body).toContain('"active_plan_id":"PLAN-DEMO-V1-CP-SAT"');
  });
});

describe("runtime API errors", () => {
  it("returns the safe structured 409 error needed for refresh-before-retry", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      error: {
        code: "runtime_revision_conflict",
        message: "运行状态已更新到修订 4；请刷新当前会话后重试",
      },
    }, 409));

    const request = postRuntimeAction(
      "RUN-DEMO",
      "pause",
      { expected_revision: 3 },
      { fetcher },
    );

    await expect(request).rejects.toMatchObject({
      status: 409,
      code: "runtime_revision_conflict",
    });
  });
});
