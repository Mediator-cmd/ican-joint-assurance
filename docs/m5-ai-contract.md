# M5-0 AI 辅助契约

## 1. 文档目的

本文件冻结 M5 的自然语言事件草稿、字段依据、缺失追问、目标建议、方案解释、来源透明度和无模型回退语义。M5-1 至 M5-6 必须复用 `backend/app/ai_models.py`，不得让模型提供方、路由或前端另建一套可绕过版本与人工确认的状态。

M5-0 只交付契约模型、测试和阶段计划，不公开 AI 路由、不调用外部模型、不读取密钥、不应用事件，也不修改 M4 的实时工作区。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 核心原则

1. 所有自然语言均是不可信输入；只有 Pydantic、场景实体和版本校验通过后才能形成事件草稿。
2. 草稿不是命令。`requires_human_confirmation=true`、`applies_automatically=false` 固定不可改。
3. 模型不是规划器。目标建议固定 `applied_to_planner=false`，方案解释固定 `modifies_plan=false`。
4. M4 的场景版本、运行 revision、仿真时间、当前/候选方案、CP-SAT、硬约束与人工确认继续是唯一权威链。
5. 无模型时使用确定性规则；规则不能理解时追问或拒绝，不猜测。
6. API 不回显原始输入、内部提示词、密钥、提供方 URL、原始异常、堆栈或本机路径。

## 3. 事件草稿请求

当前路径：`POST /api/v1/assistant/event-drafts`。M5-0 冻结契约时未提前公开该路由，M5-1 已按本契约实现。

`EventDraftRequest.context` 是判别联合：

- `scope=scenario`：携带 `scenario_id`、`expected_version` 和带时区 `reference_time`。
- `scope=runtime`：携带 `session_id` 和 `expected_revision`；服务从权威快照取得场景版本与仿真时间，不能信任前端自报时间。

`assistance_mode` 只允许：

- `auto`：模型可用时尝试模型，任何安全失败后回退规则。
- `deterministic_only`：禁止外部调用，只使用规则。

原始 `text` 限制为 1 至 1000 字符，不进入响应或默认持久化记录。

## 4. 事件草稿响应

`EventDraftResponse.status` 有三种：

| 状态 | `event` | 缺失字段/追问 | 语义 |
| --- | --- | --- | --- |
| `ready_for_review` | 完整 `FlightEvent` | 必须为空 | 已校验草稿，仍需人工复核 |
| `needs_clarification` | 必须为空 | 每个缺失字段恰有一个问题 | 信息不足，不能构造事件 |
| `unsupported` | 必须为空 | 必须为空 | 当前仅支持有限事件类型，并返回安全说明 |

M5 首轮事件字段限定为：事件类型、航班 ID、发生时间、延误分钟、原登机口和新登机口。事件 ID 由后端生成，不从文本信任读取。

完整草稿必须为每个必填字段提供 `ExtractedFieldEvidence`：规范化值、来源和可选原文片段。来自用户文本的证据必须保留有限原文片段；来自场景或运行快照的字段标记为权威上下文；相对时间的确定性换算标记为确定性派生。证据值与最终 `FlightEvent` 不一致时契约拒绝响应。

## 5. 来源与回退

`AssistanceTrace` 公开最小可诊断信息：

- `source=language_model` 时必须确实尝试提供方并显示非敏感模型标签，不能同时声称回退。
- `source=deterministic_rules` 可以表示主动规则模式或模型失败后的回退。
- 回退原因只允许：未配置、超时、提供方错误、模型输出未通过校验。
- 超时、提供方错误和非法输出必须对应真实调用尝试；未配置不能声称已调用。

提供方的原始错误、响应正文和内部模型配置不进入公开契约。

## 6. 目标建议

`PlanningObjectiveProfile` 只允许：

- `balanced`：沿用当前覆盖、优先级、等待与移动的确定性词典序基线。
- `critical_first`：关键任务优先。
- `minimum_wait`：在安全覆盖前提下降低等待。
- `minimum_change`：滚动阶段优先减少未来任务变化。

`ObjectiveRecommendation` 只表达建议、理由和有限原文依据，固定要求人工确认且尚未应用。M5-3 已在确定性优化器实现四种有限配置：显式目标必须确认后才能进入规划器；旧请求仍默认 `balanced`；`minimum_change` 只适用于有当前方案基线的运行滚动规划。建议本身仍不能描述成已经改变排班。

## 7. 方案解释请求与响应

计划路径：`POST /api/v1/assistant/plan-explanations`。请求上下文是判别联合：

- `scenario_plan`：场景 ID、场景版本、被解释方案和可选基线方案。
- `runtime_plan`：会话 ID、revision、被解释方案和可选基线方案。

基线与被解释方案不得相同。运行解释必须在读取时重新核对 revision 和方案 ID，避免页面对陈旧候选获取看似有效的说明。

服务先从权威数据构造 `ExplanationEvidence`。响应回显实际处理的 `focus` 和规范化 `question`，并先返回独立 `question_answer`：`not_asked` 表示没有补充问题，`answered` 必须引用问题相关权威事实，`insufficient_evidence` 表示当前上下文不足。被点名的任务、资源、航班、事件和方案必须优先进入最多 100 条证据，不能因默认排序或截断漏掉。

方案解释固定包含四个职责不同的分区：`summary` 只说明整体状态及其与问题的关系；`tradeoffs` 只说明目标、收益、代价和量化取舍；`task_changes` 只说明任务、资源、服务时间、路线或相对基线差异；`manual_handling` 只说明人员需要核对、协调或决定的事项。四个分区均必填且每条 claim 至少引用一个存在的 evidence ID；引用未知、重复或与问题无关的事实时契约拒绝模型结果并回退规则解释。证据类型限定为计划指标、任务分配、未安排任务、约束、事件、方案变化和目标配置。

解释可以包含摘要、取舍、下一步和未解决问题，但固定不能修改计划，并继续要求人工确认。

M5-4 实现说明：场景解释从请求指定的不可变场景版本和已保存方案读取；运行解释由 `RuntimeSessionService.get_explanation_context(session_id, expected_revision)` 一次性取得 revision 绑定的 `RuntimeSessionSnapshot` 与 `RuntimeProjectionSource` 深拷贝。请求中的方案 ID 必须属于该 revision 的当前方案或候选方案，旧 revision 返回 `assistant_revision_conflict`。解释服务不直接访问 SQLite 或从仓库猜测运行场景版本。

无 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 时，确定性规则直接返回完整解释。配置模型后，服务只发送经过实体和主题裁剪的有限事实、问题 grounding 和解释重点；模型响应必须通过结构、实体、事实白名单和问题相关性校验，否则回退规则解释。空间位置、坐标、平面图和实时轨迹在 `SPATIAL-FACT-*` 契约落地前固定返回 `question_not_grounded`，不调用模型猜测。成功的模型响应也不会改变 `modifies_plan=false`、`requires_human_confirmation=true` 或安全声明。

## 8. 计划错误语义

M5-1 起继续使用统一 `ApiErrorResponse`，计划错误码如下：

| HTTP | code | 场景 |
| --- | --- | --- |
| `404` | `assistant_context_not_found` | 场景、会话或方案不存在 |
| `409` | `assistant_version_conflict` | 场景版本已变化 |
| `409` | `assistant_revision_conflict` | 运行 revision 已变化 |
| `409` | `assistant_plan_context_mismatch` | 方案不属于指定场景/会话 |
| `400` | `planning_objective_not_supported` | 静态场景请求使用仅限滚动规划的最小变更目标 |
| `409` | `runtime_event_already_registered` | 运行事件 ID 已存在 |
| `409` | `runtime_event_time_conflict` | 事件时间早于权威仿真时间 |
| `400` | `runtime_event_not_applicable` | 事件实体与当前运行场景不一致 |
| `422` | `validation_error` | 请求或生成结果不符合契约 |

不能理解的自然语言通常返回 200 的 `needs_clarification` 或 `unsupported`，不是 500。模型未配置或失败也不直接返回 5xx；服务应回退规则，并在 trace 中说明安全原因。

## 9. M5-0 退出条件

- [x] 事件草稿请求绑定场景 version 或运行 revision。
- [x] 完整、追问和不支持三种响应状态无歧义。
- [x] 完整事件的每个必填字段都必须有一致证据。
- [x] 模型与规则来源、调用尝试和回退原因不能互相矛盾。
- [x] 目标建议不能自动进入规划器。
- [x] 场景与运行方案解释上下文均绑定版本事实。
- [x] 解释的每条摘要、取舍和下一步只能引用响应中存在的权威证据 ID。
- [x] 自动应用事件、修改方案和绕过人工确认在模型层被禁止。
- [x] 完整教学仿真安全声明固定在草稿与解释响应中。

满足以上条件后进入 M5-1。若实现发现契约缺陷，必须先更新本文件、`ai_models.py` 和契约测试并记录原因。

## 10. M5-3 已实现的提交契约

`POST /api/v1/assistant/event-drafts/submit` 接收完整 `EventDraftSubmissionRequest`：

- `draft` 必须仍是 `ready_for_review`，包含完整事件和一致字段依据；`confirm_event` 只能为字面量 `true`。
- `scope=scenario` 时，草稿 basis 的场景版本就是 `expected_version`，提交复用既有版本化事件应用服务，不接受规划目标字段。
- `scope=runtime` 时，草稿 basis 必须匹配当前 session、revision、场景和版本；还必须提交四选一 `objective_profile` 与 `confirm_objective=true`。
- 运行提交只允许 `ready` 或 `paused`。事件不得早于权威仿真时间，也不得引用场景外实体；当前时刻事件直接进入原事件边界与重规划链，未来事件只登记并等待权威时钟触发。
- 任何候选仍保持 `awaiting_confirmation`，活动方案不会被静默替换。服务不保存原始自然语言、内部提示词或第二份草稿状态。

四种目标均由确定性 CP-SAT 实现并独立调用 `validate_plan`。`Plan`、运行投影和运行快照公开有限 `objective_profile`；旧计划和旧 SQLite 投影缺字段时默认 `balanced`。详细证据见 [m5-03-review.md](m5-03-review.md)。

## 11. M5-4 解释实现退出记录

- [x] 场景与运行方案解释均绑定权威版本/revision。
- [x] 运行当前方案、候选方案、旧版本当前方案和基线差异均有专项覆盖。
- [x] 每条 claim 只允许引用后端生成且响应中存在的 `FACT-*` ID。
- [x] 模型成功、超时、提供方错误、非法输出和未知事实引用均安全回退。
- [x] 解释请求不增加 revision、不写审计、不应用事件、不改变方案。
- [x] 无模型时完整可用，OpenAI-compatible 配置只通过服务端环境变量读取。
