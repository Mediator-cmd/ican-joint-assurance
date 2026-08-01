# M4-3 计划：事件落地、滚动重规划与人工确认

## 1. 目标

在后端权威仿真时钟到达事件边界时，准确冻结时间并原子应用同一时刻的事件批次；保留已经完成或正在执行的事实，生成经过独立硬约束复核的滚动 CP-SAT 候选方案，并提供手动重规划、采用候选和保留当前方案的 REST 闭环。

所有响应继续固定包含：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 本单元边界

### 2.1 实现

- 事件在准确仿真边界生效，不能被墙钟推进跨过。
- 相同 `occurred_at` 的未应用事件合并为一个原子批次和一个新场景版本。
- `completed`、`in_service`、`waiting`、`en_route` 任务的既有安排被冻结。
- 未开始及待协调任务进入滚动 CP-SAT 计算。
- 候选方案通过独立 `validate_plan()` 后才进入 `awaiting_confirmation`。
- `POST /replan`、`POST /candidate/accept`、`POST /candidate/reject`。
- expected revision、候选 ID、防重复事件、失败回退和重启恢复。

### 2.2 不实现

- SSE、浏览器轮询恢复与消息缓冲。
- 前端运行控制、候选差异界面和前端时钟。
- 真实机场接口、真实坐标或车辆控制。

## 3. 持久化运行聚合

M4-2 的 `RuntimeProjectionSource` 要从严格同版本的单一 `scenario + plan` 扩展为可跨版本恢复的运行聚合。SQLite 仍通过 `runtime_sessions.projection_source_json` 保存完整聚合，并和会话字段、revision、控制审计在同一事务更新。

聚合必须同时保留：

| 数据 | 用途 |
| --- | --- |
| 初始场景与初始方案 | reset 的唯一恢复来源 |
| 当前事件应用后场景 | 航班、任务约束和场景版本的当前事实 |
| 当前确认执行方案 | 人工确认前持续驱动任务和资源投影 |
| 当前候选方案 | 等待采用或拒绝，并支持跨重启恢复 |
| 完整事件目录与应用版本 | 防重复、同批次版本证据和事件状态投影 |
| 冻结任务 ID | 标识不可被后续滚动规划改写的执行事实 |
| 事件与候选/决定证据 | 在采用或拒绝后仍可恢复事件生命周期 |

当前执行方案允许早于当前场景版本；候选方案若存在，必须属于当前场景版本。初始场景与初始方案必须同版本。旧 M4-2 JSON 缺少新增字段时，以原有 `scenario + plan` 补齐初始状态，保持数据库向后兼容。

## 4. 事件边界事务

1. 根据单调时钟计算本次可到达的目标仿真时间。
2. 查找目标时间内最早的未应用事件时刻；若存在，只推进到该准确时刻。
3. 将相同时刻的所有未应用事件按 `event_id` 稳定排序组成批次。
4. 使用事件前的当前执行方案投影任务状态，冻结已完成、途中、等待和保障中的任务。
5. 在当前场景副本上只应用本批新事件；冻结任务的路线和时间窗不被事件回写。
6. 一个批次只增加一次场景版本；为批内每个事件记录相同 `version_after`。
7. 同一 SQLite 事务保存冻结时间、`replanning`、新场景、revision 和审计。
8. 释放运行时钟锚点后执行滚动求解，不在求解期间推进时间。

事件内容与当前场景不一致时，不写入部分场景版本；会话进入 `failed` 并保留可复核错误摘要。revision 冲突时放弃本次写入并重新读取权威记录。

## 5. 滚动候选方案

滚动求解输入由当前场景、当前执行方案、冻结时刻和冻结任务组成：

1. 冻结任务完整保留原 assignment ID、资源、路线和四个时间边界。
2. 每项资源根据最后一个冻结安排推导下一可用时间与后续起始区域；没有冻结安排的资源不得早于当前仿真时间出发。
3. 从残余场景移除冻结任务，仅对未开始和待协调任务运行确定性单线程 CP-SAT。
4. 合并冻结安排与新安排，重新计算完整方案指标。
5. 对当前完整场景执行独立 `validate_plan()`；违规数不为 0 的结果不得成为候选。
6. 候选 ID 包含 `runtime_session_id + replan revision`，允许同一场景版本多次手动计算。

普通无可行解或求解超时属于可恢复失败：保留当前执行方案，回到 `paused`，清除候选并记录 `replan_failed` 审计；不把会话升级为不可恢复的 `failed`。

## 6. 人工确认语义

- 手动重规划仅允许从 `paused` 发起；先持久化 `replanning`，再生成候选。
- 候选就绪后进入 `awaiting_confirmation`，当前执行方案保持不变。
- 采用候选时同时校验 expected revision 和 candidate plan ID；原子切换 active plan、清空候选并进入 `paused`。
- 拒绝候选时保留 active plan、清空候选并进入 `paused`。
- 采用或拒绝后，触发该候选的事件记录进入 `resolved`；拒绝不回滚已经发生的航班/场景事件。
- reset 从持久化初始场景和初始方案恢复，清除运行期间的候选、冻结和事件决定证据。

## 7. 路由与错误

| 路由 | 状态码 | 关键保护 |
| --- | --- | --- |
| `POST /api/v1/runtime-sessions/{session_id}/replan` | 202 | 仅 paused；expected revision |
| `POST /api/v1/runtime-sessions/{session_id}/candidate/accept` | 200 | awaiting；revision；candidate ID |
| `POST /api/v1/runtime-sessions/{session_id}/candidate/reject` | 200 | awaiting；revision；candidate ID |

新增运行域错误 `runtime_candidate_mismatch`；其余继续复用 `runtime_revision_conflict`、`runtime_invalid_transition`、`runtime_plan_has_violations` 和 `runtime_persistence_error`。

## 8. 验收矩阵

- [x] 事件前一秒、准确边界和边界后一秒均不会越界或重复应用。
- [x] 同时事件只生成一个场景版本和一次自动重规划。
- [x] 两次并发边界物化只有一个成功写入。
- [x] 已完成和执行中 assignment 在候选中逐字段不变。
- [x] 事件修改场景但候选未确认时，当前任务仍显示原 assignment 路线。
- [x] 候选采用后 active plan 切换；拒绝后保持原 active plan。
- [x] 陈旧 revision 和错误 candidate ID 不产生任何部分写入。
- [x] 手动重规划可在同一场景版本生成新的唯一候选。
- [x] 求解失败回到 paused；一致性失败进入 failed；持久化失败拒绝部分写入并返回错误。
- [x] awaiting confirmation 重启后保留候选；replanning 重启后安全回到 paused。
- [x] reset 精确恢复初始场景、方案、事件和投影。
- [x] OpenAPI 只新增 M4-3 三条路由，不提前暴露 SSE。
- [x] 专项、全量 pytest、compileall、pip check 和 diff check 通过。

## 9. 实施顺序

- [x] 扩展运行聚合与向后兼容验证。
- [x] 实现滚动候选生成器和独立约束复核。
- [x] 实现事件批次边界物化与失败回退。
- [x] 实现手动重规划、采用和拒绝服务。
- [x] 接入路由和错误映射。
- [x] 补齐仓库、服务、路由、并发和重启测试。
- [x] 完成真实 Uvicorn 演示验收、审查记录和交接文档。
