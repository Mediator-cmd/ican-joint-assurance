import { describe, expect, it } from "vitest";

import {
  includesNormalizedQuery,
  pageForIndex,
  paginateItems,
  paginationMeta,
} from "./pagination";

describe("paginationMeta", () => {
  it("clamps large collections to bounded data windows", () => {
    const meta = paginationMeta(2000, 99, 50);

    expect(meta).toEqual({
      page: 40,
      pageSize: 50,
      pageCount: 40,
      total: 2000,
      startIndex: 1950,
      endIndex: 2000,
      rangeStart: 1951,
      rangeEnd: 2000,
    });
  });

  it("uses a stable empty range without creating page zero", () => {
    expect(paginationMeta(0, 4, 25)).toMatchObject({
      page: 1,
      pageCount: 1,
      rangeStart: 0,
      rangeEnd: 0,
    });
  });

  it("rejects invalid counts and page sizes", () => {
    expect(() => paginationMeta(-1, 1, 20)).toThrow(RangeError);
    expect(() => paginationMeta(10, 1, 0)).toThrow(RangeError);
  });
});

describe("paginateItems", () => {
  it("returns only the requested slice and locates selected records", () => {
    const items = Array.from({ length: 2000 }, (_, index) => `TASK-${index + 1}`);
    const window = paginateItems(items, pageForIndex(1249, 50), 50);

    expect(window.items).toHaveLength(50);
    expect(window.items[0]).toBe("TASK-1201");
    expect(window.items.at(-1)).toBe("TASK-1250");
  });
});

describe("includesNormalizedQuery", () => {
  it("matches identifiers and Chinese labels without changing source data", () => {
    expect(includesNormalizedQuery(" sim102 ", ["FL-SIM102", "航班延误"])).toBe(true);
    expect(includesNormalizedQuery("延误", ["FL-SIM102", "航班延误"])).toBe(true);
    expect(includesNormalizedQuery("轮椅", ["TASK-001", "陪同引导"])).toBe(false);
  });
});
