# M4-4 计划：SSE 实时流、消息缓冲与断线恢复

## 1. 目标

在不创建第二套仿真时钟、任务状态机或业务 revision 的前提下，将现有 `RuntimeSessionService` 权威快照转换为浏览器可持续消费的 SSE 流。每个会话在单个后端生命周期内共享一个 `stream_id`、单调 `sequence` 和最多 256 条消息缓冲；断线后能按 `Last-Event-ID` 精确补发，无法补发时立即回到完整快照。

所有完整运行快照继续固定包含：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 本单元边界

### 2.1 实现

- `GET /api/v1/runtime-sessions/{session_id}/stream`，响应类型为 `text/event-stream`。
- 每会话一个共享、进程内 `stream_id` 和严格递增 `sequence`。
- 256 条环形消息缓冲及 `Last-Event-ID` 补发。
- 新连接、旧流、非法 ID、超出缓冲范围时发送权威 `runtime.snapshot`。
- 运行中按 1 秒节流发送 `runtime.tick`；非运行状态每 15 秒发送 heartbeat。
- 根据相邻权威快照和 SQLite 控制审计生成任务转换、事件应用、重规划、候选决定和完成消息。
- 多连接共享同一序列，重复采样不重复发布相同业务变化。
- 已有 `GET /runtime-sessions/{session_id}` 作为两秒短轮询回退入口，并用测试锁定其无流状态依赖。
- SSE 标准帧、禁止代理缓冲的响应头、客户端断开退出和服务错误后安全关闭。

### 2.2 不实现

- 不修改五页 React 工作台，不增加浏览器 `EventSource`、连接状态条或两秒轮询循环；这些属于 M4-5。
- 不持久化 `stream_id`、`sequence`、tick、heartbeat 或消息缓冲。
- 不增加第二套业务 revision、前端时钟、任务状态机或规划器。
- 不支持多 worker、分布式消息队列、跨进程补发或真实机场生产推送。

## 3. 共享流状态

新增 `RuntimeStreamBroker`，以 `session_id` 为键维护线程安全的内存状态：

| 字段 | 语义 |
| --- | --- |
| `stream_id` | 本服务生命周期内该会话的流标识，格式为 `STREAM-...` |
| `next_sequence` | 下一条消息编号，只随发布成功增加 |
| `buffer` | `deque(maxlen=256)`，保存已发布的类型化 `RuntimeStreamEvent` |
| `last_snapshot` | 最近一次权威快照，用于检测业务和投影变化 |
| `last_audit_id` | 最近已转换的 SQLite 控制审计，防止重复发布 |
| `last_tick_at` | 运行中 tick 的墙钟节流依据 |
| `last_heartbeat_at` | 非运行状态 heartbeat 的墙钟节流依据 |

所有修改在同一 `RLock` 内完成。多个 SSE 连接可以并发采样，但同一业务审计、同一快照变化和同一节流窗口只发布一次。

## 4. 消息生成顺序

每次采样先由 `RuntimeSessionService.get_session()` 物化准确业务边界，再读取该会话控制审计：

1. 将新增审计按 `audit_id` 排序转换为业务消息。
2. 比较前后任务状态，按 `task_id` 生成 `task.transition`。
3. 若业务字段或任务/资源/航班/事件投影变化，发送完整 `runtime.snapshot`。
4. 会话为 `running` 且距上次 tick 至少 1 秒时发送 `runtime.tick`。
5. 非运行状态且距上次 heartbeat 至少 15 秒时发送 heartbeat。

一个采样批次中的消息按上述顺序获得连续 sequence。业务消息使用对应审计的 `revision_after`；snapshot、tick 和 heartbeat 使用当前快照 revision。tick 与 heartbeat 不写 SQLite，不增加业务 revision。

审计动作映射如下：

| 审计 action | SSE event |
| --- | --- |
| `event_batch_applied` | `event.applied` + `replan.started(automatic_event)` |
| `replan_started` | `replan.started(manual)` |
| `candidate_created` | `replan.ready` |
| `replan_failed` | `replan.failed` |
| `candidate_accepted` | `plan.accepted` |
| `candidate_rejected` | `plan.rejected` |
| `runtime_completed` | `runtime.completed` |

创建、开始、暂停、倍速、重置和服务恢复没有独立 payload 类型，通过紧随其后的 `runtime.snapshot` 表达。

## 5. 重连与缓冲语义

SSE `id` 固定为 `{stream_id}:{sequence}`。

- 没有 `Last-Event-ID`：发送新的完整 snapshot。
- ID 属于当前流，且对应 sequence 仍可从缓冲连续恢复：补发所有更大 sequence。
- ID 属于旧流、格式非法、sequence 超前或已早于缓冲：发送新的完整 snapshot。
- 服务重启后 broker 为空，首次连接产生新 stream ID 和完整 snapshot；SQLite 会话恢复语义保持不变。
- 补发只读取缓冲，不重新增加 sequence，也不改变消息的 `emitted_at` 或 revision。

## 6. SSE HTTP 语义

- `Content-Type: text/event-stream; charset=utf-8`
- `Cache-Control: no-cache, no-transform`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`
- 每帧包含 `id`、`event`、单行 JSON `data` 和空行终止符。
- 建立流前先验证会话存在；不存在仍使用统一 JSON `404 runtime_session_not_found`。
- 流建立后的读取或持久化错误不泄漏路径、堆栈或原始请求，连接安全结束，由客户端进入轮询恢复。

## 7. 轮询回退

M4-4 不在尚未改造的前端中提前实现连接状态机，但必须保证 M4-5 可直接采用以下流程：

1. SSE 连续连接失败后，每 2 秒调用一次现有权威快照接口。
2. 快照接口继续物化事件边界，返回与 SSE snapshot 相同的 `RuntimeSessionSnapshot`。
3. SSE 恢复后携带最后一个已处理的 SSE ID；收到补发或完整 snapshot 后停止轮询。
4. 浏览器只按 `stream_id + sequence` 去重，不能用 revision 丢弃 tick。

## 8. 验收矩阵

- [x] 首次连接第一条消息为完整 snapshot，安全声明保持不变。
- [x] 同一会话的两个连接共享 stream ID 和 sequence，不重复发布同一审计。
- [x] tick 与 heartbeat 不增加 SQLite revision 或控制审计。
- [x] 运行中 tick 最快每秒一次，非运行 heartbeat 最快每 15 秒一次。
- [x] 任务状态变化产生有准确前后状态和时刻的 transition。
- [x] 事件批次、自动/手动重规划、候选、采用/拒绝、失败和完成消息类型正确。
- [x] 当前流且缓冲存在时精确补发，不重新编号。
- [x] 旧流、非法 ID、超前 ID 和缓冲淘汰均回到完整 snapshot。
- [x] 缓冲始终不超过 256 条。
- [x] 服务重启产生新 stream ID，运行会话仍由 SQLite 找回。
- [x] SSE 响应头、帧格式、OpenAPI 和 404 错误统一。
- [x] REST 快照在 SSE 不可用时仍可独立轮询且不依赖 broker。
- [x] 专项、M4 组合、全量 pytest、compileall、pip check 和 diff check 通过。

## 9. 实施顺序

- [x] 实现共享 broker、缓冲、编号、采样与消息派生。
- [x] 实现 SSE 帧编码和异步连接循环。
- [x] 在应用工厂注入单例 broker，并接入公开路由。
- [x] 补齐 broker、重连、缓冲、节流、业务消息和 API 测试。
- [x] 完成真实 Uvicorn 流、重连、轮询和清理验收。
- [x] 编写 M4-4 review，更新 README、M4 总计划和持续交接文档。
