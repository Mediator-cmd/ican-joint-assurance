export interface PaginationMeta {
  page: number;
  pageSize: number;
  pageCount: number;
  total: number;
  startIndex: number;
  endIndex: number;
  rangeStart: number;
  rangeEnd: number;
}

export interface PaginatedItems<T> extends PaginationMeta {
  items: T[];
}

export function paginationMeta(
  total: number,
  requestedPage: number,
  pageSize: number,
): PaginationMeta {
  if (!Number.isInteger(total) || total < 0) {
    throw new RangeError("pagination total must be a non-negative integer");
  }
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new RangeError("pagination pageSize must be a positive integer");
  }
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(Math.trunc(requestedPage) || 1, 1), pageCount);
  const startIndex = (page - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, total);
  return {
    page,
    pageSize,
    pageCount,
    total,
    startIndex,
    endIndex,
    rangeStart: total === 0 ? 0 : startIndex + 1,
    rangeEnd: endIndex,
  };
}

export function paginateItems<T>(
  items: readonly T[],
  requestedPage: number,
  pageSize: number,
): PaginatedItems<T> {
  const meta = paginationMeta(items.length, requestedPage, pageSize);
  return {
    ...meta,
    items: items.slice(meta.startIndex, meta.endIndex),
  };
}

export function pageForIndex(index: number, pageSize: number): number {
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new RangeError("pagination pageSize must be a positive integer");
  }
  return index < 0 ? 1 : Math.floor(index / pageSize) + 1;
}

export function includesNormalizedQuery(
  query: string,
  values: Array<string | null | undefined>,
): boolean {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return true;
  return values.some((value) => value?.toLocaleLowerCase("zh-CN").includes(normalized));
}
