# M5-4 权威方案事实与可追溯解释审查

## 1. 结论

M5-4 已完成。新增的方案解释能力是只读的事实表达层：后端先根据场景版本或运行 revision 构造有限的权威事实，再由确定性规则或可选 OpenAI-compatible 模型组织说明。它不应用事件、不生成或采用候选、不推进仿真时间、不写审计，也不改变任何方案。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 实现范围

- `POST /api/v1/assistant/plan-explanations` 支持 `scenario_plan` 和 `runtime_plan` 上下文。
- 场景上下文从请求指定的不可变场景版本和已保存方案读取，并核对方案归属；运行上下文由 `RuntimeSessionService.get_explanation_context()` 同时返回 revision 绑定的快照和深拷贝投影源。
- 证据由后端确定性生成，包含方案状态、目标、指标、任务分配、未分配原因、硬约束、事件和方案差异。证据 ID 固定为 `FACT-*`，不接受模型创造的事实。
- `deterministic_only` 完全不调用外部模型。无完整 `AI_*` 环境变量时仍返回完整中文解释；配置模型后只发送有限证据、focus 和可选问题，模型失败或引用未知事实时自动回退规则解释。
- 模型 claim 在进入响应模型前重新校验结构和引用；响应固定 `modifies_plan=false`、`requires_human_confirmation=true`，并保留完整安全声明。

## 3. 状态所有权与安全边界

运行场景不能通过 `InMemoryScenarioRepository` 旁路猜测版本，也不能直接读取 SQLite 形成第二状态。解释请求必须带当前 revision；陈旧 revision 返回 `assistant_revision_conflict`，方案不属于该 revision 的当前/候选集合时返回 `assistant_plan_context_mismatch`。

AI 只做表达增强。API 密钥、提供方 URL 和模型配置只从服务端环境变量读取，未写入源码、文档、测试、日志、前端 bundle 或响应。本阶段继续只使用匿名合成教学数据，不接真实机场生产数据、个人信息或外部控制系统；无 AI 配置时 GitHub 克隆和后续公网部署仍能运行 M4 实时闭环与规则解释。

## 4. 验证证据

- M5-4 专项：`tests/test_api_ai_explanations.py` 与 `tests/test_ai_explanation_provider.py`，`13 passed`。
- 后端全量：`.\.venv\Scripts\python.exe -m pytest -q tests --basetemp E:\ican\.tmp\pytest-m54-full-escalated`，`209 passed in 9.00s`。
- Python 编译：`.\.venv\Scripts\python.exe -m compileall -q backend tests`，通过。
- 依赖完整性：`.\.venv\Scripts\python.exe -m pip check`，`No broken requirements found.`
- 前端：`npm run test`，3 个测试文件、19 项通过；`npm run typecheck` 通过；`npm run build` 通过，CSS 55.11 kB、JS 281.89 kB。
- 质量门禁：`git diff --check` 通过。
- 敏感信息：扫描仅发现既有测试占位符 `sk-test-only-placeholder`；未发现用户提供的真实 DeepSeek key 或其他实际凭据。

## 5. 已知限制与下一入口

解释模型没有事实权限，也不会替代人工判断；模型不可用时的规则解释是正式能力。M5-5 才把事件草稿、人工复核、目标确认和方案解释接入现有五页共享前端状态。M5-6 负责总验收；M6 负责形成部署版本，M7 负责 GitHub 发布和稳定公网地址。本阶段不提前接入真实生产数据、不自动执行调度、不引入第二套前端状态。
