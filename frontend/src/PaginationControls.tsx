import { ChevronLeft, ChevronRight } from "lucide-react";

import { paginationMeta } from "./pagination";

export default function PaginationControls({
  label,
  total,
  page,
  pageSize,
  onPageChange,
}: {
  label: string;
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const meta = paginationMeta(total, page, pageSize);
  return (
    <nav className="data-window-pagination" aria-label={`${label}分页`}>
      <span>
        <b>{meta.rangeStart}–{meta.rangeEnd}</b>
        <small> / 共 {meta.total} 项</small>
      </span>
      <div>
        <button
          type="button"
          disabled={meta.page === 1}
          aria-label={`上一页${label}`}
          onClick={() => onPageChange(meta.page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <strong>{meta.page} / {meta.pageCount}</strong>
        <button
          type="button"
          disabled={meta.page === meta.pageCount}
          aria-label={`下一页${label}`}
          onClick={() => onPageChange(meta.page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </nav>
  );
}
