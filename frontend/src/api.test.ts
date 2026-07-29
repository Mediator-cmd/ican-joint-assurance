import { describe, expect, it, vi } from "vitest";

import { DemoPayloadLoadError, loadDemoPayload } from "./api";
import type { Fetcher } from "./api";
import type { DemoPayload } from "./types";

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
