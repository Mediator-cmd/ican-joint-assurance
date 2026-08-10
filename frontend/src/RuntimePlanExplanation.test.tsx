import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PlanExplanationResult } from "./RuntimePlanExplanation";
import type { PlanExplanationResponse } from "./types";

const explanation: PlanExplanationResponse = {
  explanation_id: "EXPL-0123456789ABCDEF",
  context: {
    scope: "runtime_plan",
    session_id: "RUN-UI-TEST",
    revision: 7,
    plan_id: "PLAN-CANDIDATE",
    baseline_plan_id: "PLAN-ACTIVE",
  },
  trace: {
    source: "language_model",
    provider_attempted: true,
    model_label: "deepseek-chat",
    fallback_reason: null,
  },
  focus: "task_changes",
  question: "任务 TASK-021 的路线发生了什么变化？",
  question_answer: {
    status: "answered",
    statement: "任务 TASK-021 改由 RES-04 执行，路线调整为 Z-02 至 Z-07。",
    evidence_ids: ["FACT-CHANGE-TASK-021"],
    matched_entity_ids: ["TASK-021"],
  },
  summary: {
    statement: "候选方案仍覆盖全部关键任务。",
    evidence_ids: ["FACT-PRIMARY-STATUS"],
  },
  tradeoffs: [{
    statement: "路线调整降低等待，但提高了 RES-04 的占用。",
    evidence_ids: ["FACT-PRIMARY-UTILIZATION"],
  }],
  task_changes: [{
    statement: "TASK-021 的资源和路线均相对基线发生变化。",
    evidence_ids: ["FACT-CHANGE-TASK-021"],
  }],
  manual_handling: [{
    statement: "人员需要核对 RES-04 的连续服务窗口。",
    evidence_ids: ["FACT-PRIMARY-CONSTRAINTS"],
  }],
  recommended_next_step: {
    statement: "复核 TASK-021 任务书后再决定是否采用候选。",
    evidence_ids: ["FACT-CHANGE-TASK-021"],
  },
  evidence: [
    { evidence_id: "FACT-PRIMARY-STATUS", kind: "constraint", entity_ids: ["PLAN-CANDIDATE"], field: "status", value: "executable", statement: "方案状态可执行。" },
    { evidence_id: "FACT-PRIMARY-UTILIZATION", kind: "plan_metric", entity_ids: ["PLAN-CANDIDATE"], field: "metrics.overall_resource_utilization_pct", value: "74", statement: "总体资源利用率为 74%。" },
    { evidence_id: "FACT-CHANGE-TASK-021", kind: "plan_change", entity_ids: ["TASK-021"], field: "assignment.change", value: "changed", statement: "TASK-021 改由 RES-04 执行，路线为 Z-02 至 Z-07。" },
    { evidence_id: "FACT-PRIMARY-CONSTRAINTS", kind: "constraint", entity_ids: ["PLAN-CANDIDATE"], field: "violations.count", value: "0", statement: "硬约束违规数为 0。" },
  ],
  unresolved_questions: [],
  requires_human_confirmation: true,
  modifies_plan: false,
  safety_notice: "仅供教学仿真与辅助决策使用。",
};

describe("PlanExplanationResult", () => {
  it("renders the direct answer and all four functional sections", () => {
    const html = renderToStaticMarkup(<PlanExplanationResult explanation={explanation} />);

    expect(html).toContain("针对你的问题");
    expect(html).toContain("任务 TASK-021 改由 RES-04 执行");
    expect(html).toContain("方案摘要");
    expect(html).toContain("方案取舍");
    expect(html).toContain("任务变化");
    expect(html).toContain("人工处理");
    expect(html).toContain("当前重点");
    expect(html).toContain("FACT-CHANGE-TASK-021");
    expect((html.match(/explanation-section emphasized/g) ?? [])).toHaveLength(1);
  });
});
