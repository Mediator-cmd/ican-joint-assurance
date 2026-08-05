# M5-3 人工复核提交与确定性目标配置审查记录

## 1. 结论

M5-3 已完成。可复核事件草稿只有在用户显式确认后才能进入权威事件链；运行事件还必须确认四选一的确定性调度目标。场景 version、运行 revision、仿真时间、当前/候选方案、CP-SAT、独立硬约束复核和人工采用继续由 M4 原服务拥有。

本单元没有实现方案解释或前端 AI 交互，没有调用真实外部模型，没有接入真实机场生产数据，也没有让 AI 直接应用事件、生成自由排班或采用候选。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 人工复核提交

新增 `POST /api/v1/assistant/event-drafts/submit`，请求回传完整 `EventDraftResponse`，不增加服务器草稿存储：

- 草稿必须为 `ready_for_review`，事件和逐字段依据必须通过既有 Pydantic 契约，`confirm_event` 只能为 `true`。
- 场景提交复用 `ScenarioService.apply_events()`，以草稿 basis 的场景版本作为 `expected_version`；版本变化、重复事件或事件批次不可应用时沿用统一错误语义。
- 运行提交重新核对 session、revision、场景 ID 和当前场景版本，只允许 `ready` 或 `paused`，并要求 `objective_profile` 与 `confirm_objective=true`。
- 事件时间早于权威仿真时间时拒绝，未知航班/登机口等实体不匹配时拒绝；两类失败均不修改 revision、事件目录或目标。
- 当前时刻事件复用原子事件边界、滚动重规划和候选链。未来事件只登记到 SQLite 投影，权威仿真时钟到达该独立边界时才应用并生成候选。

候选始终先通过 CP-SAT 和独立硬约束复核，再停在 `awaiting_confirmation`。提交草稿不能静默替换当前执行方案，后续仍由用户采用或保留。

## 3. 有限调度目标

目标枚举集中在 `backend/app/planning_objectives.py`，只允许：

| 目标 | 确定性词典序 | 可用上下文 |
| --- | --- | --- |
| `balanced` | P1 关键任务、任务覆盖、优先级得分、等待/移动/资源排序成本 | 静态与滚动；旧请求默认值 |
| `critical_first` | P1 关键任务、优先级得分、任务覆盖、等待/移动/资源排序成本 | 静态与滚动 |
| `minimum_wait` | P1 关键任务、任务覆盖、总等待、优先级得分、移动/资源排序成本 | 静态与滚动 |
| `minimum_change` | P1 关键任务、任务覆盖、相对当前方案的资源变更数、优先级得分、其他成本 | 仅有当前方案基线的滚动规划 |

旧 CP-SAT 请求不提交目标时，算法名、计划 ID、分配结果和“系统优化建议”显示名保持原 `balanced` 行为。显式目标必须人工确认；静态场景请求 `minimum_change` 返回 `planning_objective_not_supported`，模型不能提供任意权重或目标函数代码。

## 4. 状态所有权与兼容性

- `Plan.objective_profile` 是方案自身的有限目标事实；旧计划缺字段时默认 `balanced`。
- `RuntimeProjectionSource.objective_profile` 是运行会话当前目标的唯一所有者，并随 SQLite JSON 持久化；旧投影缺字段时默认 `balanced`。
- 人工重规划可以显式确认新目标；未显式选择时沿用会话当前目标。自动事件、候选采用或保留均不建立平行目标状态。
- 重置会话恢复初始方案及其目标，不保留上一轮临时目标。
- M4 `/api/v1/demo` 是旧离线只读契约，继续排除新增目标字段并与 `frontend/public/demo-output.json` 逐字一致；实时业务响应正常公开目标。

## 5. 错误与并发语义

| HTTP | code | 结果 |
| --- | --- | --- |
| `422` | `validation_error` | 未确认事件/目标、非法枚举或草稿契约不完整；不改状态 |
| `409` | `assistant_version_conflict` | 场景版本变化；要求重新生成草稿 |
| `409` | `assistant_revision_conflict` | 运行 revision 变化；要求刷新权威快照 |
| `409` | `runtime_event_already_registered` | 运行事件 ID 已登记 |
| `409` | `runtime_event_time_conflict` | 事件试图让仿真时间回退 |
| `400` | `runtime_event_not_applicable` | 事件实体不属于当前运行事实 |
| `400` | `planning_objective_not_supported` | 静态场景使用仅限滚动的最小变更 |

运行提交最终仍由仓库 CAS 更新保护；服务读取草稿上下文后出现并发变化时不会覆盖新 revision。

## 6. 验证证据

| 验证 | 结果 |
| --- | --- |
| M5-3 专项 | `12 passed in 1.57s` |
| M4/M5 聚焦回归 | `124 passed in 5.07s` |
| 后端全量 | `196 passed in 8.24s` |
| Python 编译 | `python -m compileall -q backend tests scripts` 通过 |
| Python 依赖 | `pip check` 返回 `No broken requirements found.` |
| 前端回归 | `19 passed` |
| TypeScript | `npm run typecheck` 通过 |
| 前端生产构建 | Vite 构建通过；CSS 55.11 kB，JS 281.89 kB |

专项覆盖默认 `balanced` 精确兼容、三种静态目标、滚动 `minimum_change`、旧投影兼容、双重人工确认、version/revision 冲突、时间倒退、实体不匹配、当前/未来事件、唯一候选和候选硬约束为 0。全量回归同时锁定旧 `/api/v1/demo` 静态契约。

## 7. 已知边界

- M5-4 尚未实现，当前没有 `POST /api/v1/assistant/plan-explanations` 的事实证据构造和解释服务。
- M5-5 尚未实现，现有前端还没有自然语言输入、草稿复核、目标选择或方案解释控件；M4 五页运行工作台保持原样。
- 本单元没有真实调用外部模型。模型密钥仍只允许从本地进程环境读取，且不得进入源码、文档、测试、日志、审计、前端或 API 响应。
- 事件提交仍限定匿名化、历史回放或合成教学场景；不接真实机场生产流、个人信息或控制系统。

## 8. 下一入口

M5-4 是下一唯一入口：对不可变场景计划以及运行当前/候选方案构造有限 `ExplanationEvidence`，核对场景 version 或运行 revision 和方案归属，再由确定性规则或可选模型组织引用 evidence ID 的解释。解释固定 `modifies_plan=false`、`requires_human_confirmation=true`，不能应用事件、修改方案或成为采用候选的隐式命令。
