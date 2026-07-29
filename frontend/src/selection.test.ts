import { describe, expect, it } from "vitest";

import { resolveSelectedId } from "./selection";

describe("resolveSelectedId", () => {
  it("keeps the current selection when refreshed data still contains it", () => {
    expect(resolveSelectedId("TASK-002", ["TASK-001", "TASK-002"])).toBe("TASK-002");
  });

  it("selects the first available item when the previous selection disappears", () => {
    expect(resolveSelectedId("TASK-OLD", ["TASK-003", "TASK-004"])).toBe("TASK-003");
  });

  it("clears the selection when refreshed data has no available items", () => {
    expect(resolveSelectedId("TASK-001", [])).toBe("");
  });
});
