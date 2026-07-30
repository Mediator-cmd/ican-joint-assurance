# M4-0 实时运行契约

## 1. 文档目的

本文件冻结 M4 的运行会话、权威仿真时钟、状态机、REST 控制、SSE 推送、并发修订、持久化恢复和错误语义。M4-1 至 M4-5 必须复用这里定义的模型与路径，不得各自创建第二套时间、状态或消息格式。

M4-0 只交付契约模型和测试，不启动后台时钟、不创建运行路由、不写 SQLite，也不把当前快照页面描述为已经实时运行。

所有运行响应继续固定包含：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 权威数据边界

| 数据 | 唯一权威来源 | 前端职责 |
| --- | --- | --- |
| 仿真时间 | 后端运行会话时钟 | 显示服务端时间，可在两次 tick 间平滑显示但不得自行决定状态 |
| 当前场景版本 | 运行会话记录 | 展示，不自行递增 |
| 当前执行方案 | `active_plan_id` | 展示方案和任务状态，不自行替换 |
| 候选方案 | `candidate_plan_id` | 显示差异并提交采用/拒绝命令 |
| 任务/资源/事件状态 | 后端确定性投影 | 根据枚举与中文标签呈现，不随机生成变化 |
| 调度结果 | FIFO/CP-SAT 与独立约束检查 | 不在浏览器复制调度算法 |
| 实时连接状态 | SSE 连接与轮询恢复器 | 明确显示在线、恢复中或离线只读 |

`backend/app/runtime_models.py` 是 M4 运行契约的代码事实来源。本文解释状态和交互语义；若实现需要改变字段或路径，必须先修改本文、模型和契约测试，再进入后续代码。

## 3. 会话创建与初始状态

`CreateRuntimeSessionRequest` 只接收：

- `scenario_id`
- `scenario_version`
- `active_plan_id`
- `speed`：`1`、`5` 或 `15`，默认 `1`

服务在创建前必须确认：

1. 场景和指定版本存在。
2. 方案属于该场景和版本。
3. 方案硬约束违规数为 `0`；允许使用无违规的部分方案，未安排任务显示为“待协调”。
4. 场景数据分类为 `synthetic` 或 `anonymized_replay`。

创建成功后：

- `status = ready`
- `revision = 1`
- `simulation_time = scenario.window_start`
- `active_plan_id = initial_plan_id`
- `candidate_plan_id = null`
- 时钟不推进
- 所有事件回到等待发生状态

同一场景允许创建多次独立运行会话，便于重置演示和比较不同运行记录；会话 ID 使用 `RUN-...`，不能由前端指定。

## 4. 权威仿真时钟

### 4.1 时间计算

运行中时间按以下关系计算：

```text
simulation_time = simulation_anchor
                + monotonic_elapsed_seconds * speed
```

- `simulation_anchor` 是最近一次开始、继续或倍速切换时的仿真时间。
- 经过时间使用进程内单调时钟，系统时间校准不能让仿真倒退。
- API 和 SQLite 只保存带时区的业务时间，不保存不能跨进程使用的单调时钟值。
- 暂停、重规划、等待确认、完成和失败状态下，`is_advancing = false`。
- 倍速切换前先计算并固定当前仿真时间，再重建锚点，不能产生跳跃。

### 4.2 推进与边界

后台循环不得直接把时间跳过业务边界。每次推进到目标时间前，先寻找不晚于目标时间的最近边界：

1. 待发生事件时间。
2. 任务移动开始、到达、服务开始和服务结束时间。
3. 资源可用时段开始或结束。
4. 场景窗口结束。

到达边界时先把 `simulation_time` 固定为该准确时刻，再产生状态转换。所有时间区间采用左闭右开 `[start, end)`；结束时刻立即属于下一状态。

多个事件具有相同 `occurred_at` 时，作为一个原子批次应用，只生成一个新场景版本并触发一次重规划。事件触发后时钟冻结，状态进入 `replanning`，不会在求解或等待用户期间继续推进。

### 4.3 tick 频率

- 运行中每 1 秒墙钟时间发送一次 `runtime.tick`。
- `15x` 下每个 tick 约推进 15 秒仿真时间，但业务边界仍精确处理。
- 非运行状态不发送 tick，只发送状态事件和每 15 秒一次的 heartbeat。
- tick 不写 SQLite，也不触发 CP-SAT。

## 5. revision 与 stream sequence

M4 使用两个不能混用的单调编号：

| 编号 | 含义 | 何时增加 | 是否持久化 |
| --- | --- | --- | --- |
| `revision` | 运行会话可修改业务状态的版本 | 控制命令、事件应用、候选方案产生、采用/拒绝、完成记录等持久变化 | 是 |
| `sequence` | 当前 SSE 流发出的消息顺序 | 每条 snapshot、tick、transition、heartbeat 都增加 | 否 |

`runtime.tick` 和 heartbeat 可以具有相同 `revision`，但必须具有不同 `sequence`。客户端按 `stream_id + sequence` 去重，不能用“只接受更大 revision”过滤 tick。

一次原子业务变更可以产生多条 SSE。例如事件批次提交可连续发送 `event.applied`、`replan.started` 和 snapshot；这些消息共享该事务产生的新 revision，以不同 sequence 排序。revision 不按消息条数重复增加。

每个后端生命周期为每个会话分配一个 `STREAM-...` 流标识，SSE `id` 格式为：

```text
{stream_id}:{sequence}
```

服务重启后使用新的 `stream_id`。如果浏览器携带的 `Last-Event-ID` 属于当前流且仍在 256 条内存缓冲中，服务补发缺失消息；否则立即发送完整 `runtime.snapshot`，不返回不可恢复错误。

## 6. 会话状态机

### 6.1 用户命令

| 当前状态 | 命令 | 下一状态 | 规则 |
| --- | --- | --- | --- |
| `ready` / `paused` | start | `running` | 按当前仿真时间重建单调时钟锚点 |
| `running` | pause | `paused` | 先固定准确仿真时间 |
| `ready` / `running` / `paused` | speed | 原状态 | 运行中先重建锚点；其他状态只保存速度 |
| `paused` | replan | `replanning` | 手动重规划必须先暂停 |
| `awaiting_confirmation` | accept | `paused` | 原子替换 active plan，清空 candidate |
| `awaiting_confirmation` | reject | `paused` | 保留 active plan，清空 candidate |
| `paused` / `awaiting_confirmation` / `completed` / `failed` | reset | `ready` | 必须提交 `confirm_reset: true` |

不在表内的命令返回 `409 runtime_invalid_transition`。重复点击开始、暂停或采用不会静默成功；客户端应读取最新快照后再决定下一步。

### 6.2 自动转换

| 当前状态 | 条件 | 下一状态 |
| --- | --- | --- |
| `running` | 事件到达 | `replanning`，时钟冻结在事件时刻 |
| `replanning` | 候选方案通过独立约束检查 | `awaiting_confirmation` |
| `replanning` | 可恢复求解失败 | `paused`，保留当前方案并显示回退提示 |
| `replanning` | 持久化或一致性故障 | `failed` |
| `running` | 无未来任务和事件 | `completed` |

`failed` 只用于无法安全继续的会话级错误。普通无可行解不应让整个会话失败，而是回到暂停状态并提供人工协调清单。

## 7. 任务、资源与事件状态

### 7.1 任务状态优先级

对有安排的任务，在同一仿真时刻按以下顺序判定：

1. `simulation_time >= service_ended_at`：`completed`
2. `service_started_at <= time < service_ended_at`：`in_service`
3. `travel_ended_at <= time < service_started_at`：`waiting`
4. `travel_started_at <= time < travel_ended_at`：`en_route`
5. 任务受已发生事件影响且候选方案待确认：`affected`
6. 其他情况：`pending`

无安排任务为 `unassigned`，通过 `affected_by_event_ids` 表达是否同时受扰动影响，不用 `affected` 覆盖“待协调”事实。

`en_route`、`waiting`、`in_service` 和 `completed` 的 `is_locked` 为真；滚动重规划不得改写这些任务。零分钟移动没有 `en_route` 区间，在同一边界直接进入 waiting 或 in_service。

### 7.2 资源状态

- 资源可用时段外或场景标记不可用：`unavailable`。
- `travel_started_at <= time < travel_ended_at`：`moving`。
- `travel_ended_at <= time < service_started_at`：`waiting`。
- `service_started_at <= time < service_ended_at`：`serving`。
- 其余可用时间：`idle`。

移动位置只表示区域间进度：`from_zone_id`、`to_zone_id` 和 `progress_pct`。系统没有经纬度数据时不得绘制或声称真实车辆轨迹。

### 7.3 事件状态

```text
pending -> triggered -> applied -> replanning
        -> awaiting_confirmation -> resolved
        -> failed
```

同一事件 ID 在同一会话最多应用一次。`awaiting_confirmation` 必须携带候选方案 ID；`failed` 必须携带安全错误码。

## 8. REST OpenAPI 草案

统一前缀：`/api/v1`。M4-0 冻结路径和模型，M4-1 起逐步实现；未实现路由不能提前出现在公开 OpenAPI 中。

| 方法与路径 | 请求 | 成功响应 | 用途 |
| --- | --- | --- | --- |
| `POST /runtime-sessions` | `CreateRuntimeSessionRequest` | `201 RuntimeSessionSnapshot` | 创建独立运行会话 |
| `GET /runtime-sessions` | `scenario_id?`、`status?`、`offset=0`、`limit=20` | `200 RuntimeSessionListResponse` | 找回 SQLite 中的会话 |
| `GET /runtime-sessions/{session_id}` | 无 | `200 RuntimeSessionSnapshot` | 获取权威快照，也是轮询回退入口 |
| `POST /runtime-sessions/{session_id}/start` | `RuntimeRevisionRequest` | `200 RuntimeSessionSnapshot` | 开始或继续 |
| `POST /runtime-sessions/{session_id}/pause` | `RuntimeRevisionRequest` | `200 RuntimeSessionSnapshot` | 暂停并固定时间 |
| `POST /runtime-sessions/{session_id}/speed` | `SetRuntimeSpeedRequest` | `200 RuntimeSessionSnapshot` | 设置 `1x/5x/15x` |
| `POST /runtime-sessions/{session_id}/reset` | `ResetRuntimeSessionRequest` | `200 RuntimeSessionSnapshot` | 明确确认后重置 |
| `POST /runtime-sessions/{session_id}/replan` | `ReplanRuntimeSessionRequest` | `202 RuntimeSessionSnapshot` | 异步启动手动滚动重规划 |
| `POST /runtime-sessions/{session_id}/candidate/accept` | `CandidateDecisionRequest` | `200 RuntimeSessionSnapshot` | 采用当前候选方案 |
| `POST /runtime-sessions/{session_id}/candidate/reject` | `CandidateDecisionRequest` | `200 RuntimeSessionSnapshot` | 拒绝当前候选方案 |
| `GET /runtime-sessions/{session_id}/stream` | `Last-Event-ID?` 请求头 | `200 text/event-stream` | 推送快照、时间和状态变化 |

除创建、列表、查询和 stream 外，所有控制命令必须携带 `expected_revision >= 1`。服务在同一事务中核对 revision 和允许状态；过期请求或非法状态不产生部分写入。

候选采用/拒绝还必须提交当前 `candidate_plan_id`，防止页面在新候选产生后误操作旧方案。重置必须提交字面量 `confirm_reset: true`。

## 9. 快照契约

`RuntimeSessionSnapshot` 包含：

- 会话、场景初始/当前版本、初始/当前/候选方案 ID。
- 会话状态、中文状态标签和持久化 `revision`。
- `RuntimeClockSnapshot`。
- 任务、资源、航班和事件投影。
- 面向普通用户的 `RuntimeGuidance`：现在发生什么、是否需要操作、建议下一步。
- 仅在 `failed` 状态出现的安全错误摘要。
- 创建/更新时间、`storage_scope: sqlite` 和完整安全声明。

关键不变量：

1. 只有 `running` 的 clock 可以推进。
2. 只有 `awaiting_confirmation` 可以携带 `candidate_plan_id`。
3. 只有 `failed` 可以携带会话 failure。
4. 同一快照内任务、资源、航班和事件 ID 各自唯一。
5. 当前场景版本不能小于初始版本。

## 10. SSE 消息契约

SSE 线格式：

```text
id: STREAM-DEMO-001:42
event: task.transition
data: {RuntimeStreamEvent 的 JSON}
```

`RuntimeStreamEvent` 固定包含 `stream_id`、`sequence`、`session_id`、`revision`、`emitted_at` 和带判别字段的 payload。payload 不使用任意字典，未知字段和未知事件类型必须拒绝。

| event | payload 模型 | revision 规则 |
| --- | --- | --- |
| `runtime.snapshot` | `RuntimeSnapshotStreamPayload` | 当前持久 revision |
| `runtime.tick` | `RuntimeTickStreamPayload` | 不增加 revision |
| `task.transition` | `TaskTransitionStreamPayload` | 派生状态可保持 revision；完成记录增加 revision |
| `event.applied` | `EventAppliedStreamPayload` | 增加 revision |
| `replan.started` | `ReplanStartedStreamPayload` | 增加 revision |
| `replan.ready` | `ReplanReadyStreamPayload` | 增加 revision，且违规数固定为 0 |
| `replan.failed` | `ReplanFailedStreamPayload` | 增加 revision |
| `plan.accepted` | `PlanAcceptedStreamPayload` | 增加 revision |
| `plan.rejected` | `PlanRejectedStreamPayload` | 增加 revision |
| `runtime.completed` | `RuntimeCompletedStreamPayload` | 增加 revision |
| `heartbeat` | `HeartbeatStreamPayload` | 不增加 revision |

连接建立时先发送 snapshot。SSE 连续重连失败后，前端每 2 秒调用一次 `GET /runtime-sessions/{session_id}`；SSE 恢复后停止轮询。若 API 与静态文件都不可用，页面进入错误态；若只有运行 API 不可用但静态演示可用，必须显示“离线演示，时间不会推进”，并禁用运行控制。

## 11. 错误契约

继续使用 M3 的 `ApiErrorResponse` 和 `X-Request-ID`，不回显堆栈、本机路径、原始请求或个人信息。

| HTTP | code | 场景 |
| --- | --- | --- |
| `400` | `runtime_plan_scenario_mismatch` | 初始或候选方案不属于会话场景 |
| `400` | `runtime_plan_has_violations` | 方案未通过独立硬约束检查 |
| `404` | `runtime_session_not_found` | 会话不存在 |
| `404` | `runtime_plan_not_found` | 初始或候选方案不存在 |
| `409` | `runtime_revision_conflict` | `expected_revision` 已过期，响应提示当前 revision |
| `409` | `runtime_invalid_transition` | 当前状态不允许该命令 |
| `409` | `runtime_plan_version_mismatch` | 方案版本与创建会话的场景版本不一致 |
| `409` | `runtime_candidate_mismatch` | 提交的候选 ID 不是当前待确认方案 |
| `422` | `validation_error` | 倍速、revision、确认字面量或请求结构非法 |
| `500` | `runtime_persistence_error` | SQLite 事务无法安全完成；公开响应只带请求 ID |

revision 冲突、非法状态、候选不一致和数据库失败必须原子失败，不得留下“状态已变但审计未写”或“方案已替换但 revision 未增加”的半更新。

## 12. SQLite 恢复语义

M4-1 持久化会话控制状态、关键边界、事件批次、候选方案、人工决定和审计，不逐秒保存 tick。

后端重启时：

- `running` 会话固定到最近持久边界，改为 `paused`，revision 增加并记录 `service_restarted`。
- `replanning` 会话改为 `paused`，清除未完成候选，显示重新计算提示。
- `awaiting_confirmation` 保留候选和冻结时间，继续等待用户决定。
- `ready`、`paused`、`completed` 和 `failed` 保持原状态。
- 所有恢复会话使用新的 `stream_id`，首次连接发送完整 snapshot。

单机比赛版本固定使用一个 Uvicorn worker。多 worker、分布式锁和 PostgreSQL 适配不属于 M4 MVP。

## 13. 后续实施约束

### M4-1

- 实现 SQLite 仓库、可注入单调/墙钟时钟和会话控制路由。
- 只实现创建、列表、查询、开始、暂停、倍速和重置。
- M4-1 不实现任务/资源投影、自动事件、重规划或 SSE。

### M4-2 至 M4-5

- M4-2 按第 7 节实现纯状态投影和边界调度。
- M4-3 实现事件批次、执行事实冻结、运行时候选方案和人工确认。
- 运行时候选方案必须按 `runtime_session_id + revision` 区分多次滚动计算，不能沿用 M3“同一场景版本同一算法只生成一次”的静态去重规则。
- M4-4 按第 10 节实现 SSE、缓冲、重连与轮询恢复。
- M4-5 只消费本契约，不在前端创建平行时钟、revision 或任务状态机。

## 14. M4-0 退出条件

- [x] 运行状态、任务状态、资源状态和事件状态枚举已冻结。
- [x] 时区、倍速、左闭右开边界、事件自动暂停和恢复语义无歧义。
- [x] REST 路径、请求模型、成功状态码和错误码已冻结。
- [x] revision 与 SSE sequence 已分离，重连与全量快照规则明确。
- [x] 候选方案只在等待确认状态出现，采用/拒绝均防止陈旧操作。
- [x] SQLite 只保存关键状态，不逐秒写 tick。
- [x] Pydantic 模型拒绝未知字段、无时区时间和不一致状态。
- [x] 契约测试覆盖模型、状态不变量和 SSE 判别载荷。

满足以上条件后进入 M4-1，不再重新讨论同一组字段和路径；若实现发现契约缺陷，必须先更新本文件和测试并记录原因。
