# M4-4 SSE 实时流、消息缓冲与断线恢复审查记录

## 1. 审查结论

M4-4 已完成。后端现在公开可持续消费的类型化 SSE 流；同一会话的多个连接共享 stream ID、sequence 和 256 条内存缓冲。浏览器断线后可以精确补发，无法补发时会收到完整权威 snapshot，现有 REST 快照可独立承担短轮询恢复。

审查后没有遗留的高、中、低优先级代码问题。

所有完整 snapshot 继续固定包含：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 已实现范围

### 2.1 共享流与消息派生

- `RuntimeStreamBroker` 由应用工厂创建一次，以 session ID 隔离流状态，并用 `RLock` 保证多连接采样、发布与读取的原子性。
- sequence 只在消息发布时递增，tick、heartbeat 和 snapshot 均使用当前持久 revision，但不修改 SQLite。
- 业务消息依据新增控制审计的 `revision_after` 产生；任务转换和投影 snapshot 依据相邻权威快照产生，不在消息层重算任务状态。
- 运行中 tick 最快每秒一次；非运行状态 heartbeat 最快每 15 秒一次。任务、资源、航班或事件投影变化时发送完整 snapshot，保证后续前端不需要自建平行状态机。

### 2.2 重连、缓冲与轮询

- SSE ID 固定为 `{stream_id}:{sequence}`，当前流的连续缺口直接从环形缓冲补发，不重新编号或改写时间。
- 无 Last-Event-ID、格式非法、旧流、超前 sequence 或缓冲已淘汰时，发布新的完整 snapshot。
- 服务重启后 broker 自然产生新 stream ID；SQLite 会话、候选和业务 revision 不受影响。
- REST `GET /runtime-sessions/{session_id}` 不依赖 broker，SSE 失败时仍能物化准确边界并返回同一快照契约。

### 2.3 HTTP 边界

- 流路由在返回 `StreamingResponse` 前验证会话和 SQLite，因此缺失会话仍获得统一 JSON 404。
- 响应使用 `text/event-stream`，并设置 `no-cache, no-transform`、`keep-alive` 和 `X-Accel-Buffering: no`。
- 阻塞的 SQLite/规划读取通过线程池执行，异步事件循环只负责连接、节流和发送。
- 客户端断开后生成器退出；流建立后的持久化读取失败不会泄漏路径、堆栈或原始请求，而是安全关闭并交给轮询恢复。

## 3. 审查发现与修正

1. 若消息层仅比较当前 revision，会错误丢弃共享 revision 的 tick 和 heartbeat。实现始终按 stream ID + sequence 排序去重，业务消息才使用 SQLite audit revision。
2. 单靠审计摘要无法安全重建完整投影。实现只用结构化审计字段决定消息类型和 revision，实体状态、事件 ID、候选和任务转换继续来自严格的 `RuntimeSessionSnapshot`，不解析中文摘要。
3. 可恢复重规划失败最初会沿用通用“已暂停” guidance，信息不足。现固定发送“未生成可安全采用候选、已保留当前执行方案”的明确消息，并保留 `fallback_available=true`。
4. 主契约仍把“SQLite 持久化失败”描述为可进入 `failed`，与 M4-3 已实现的事务语义冲突。现校正为：只有成功保存的一致性故障进入 `failed`；数据库不可写时回滚并返回 `runtime_persistence_error`。
5. 真实验收中，Windows `Start-Process` 首次把带空格的 curl 头参数拆开，导致服务未收到 Last-Event-ID 并正确回退 snapshot；改为无空格头参数后连续补发验证通过。
6. 首次重启验收只终止了外层执行会话，残留 Uvicorn 子进程继续持有旧 broker。随后使用沙箱外端口 PID 与完整命令行核对，精确停止旧进程，再验证新 stream ID；最终无端口或进程残留。

## 4. 验证证据

| 检查 | 结果 |
| --- | --- |
| M4-4 专项 | `31 passed` |
| M4 组合回归 | `62 passed in 2.39s` |
| 后端全量回归 | `129 passed in 4.98s` |
| Python 编译 | `.venv\Scripts\python.exe -m compileall -q backend tests` 通过 |
| 依赖检查 | `.venv\Scripts\python.exe -m pip check`：No broken requirements found |
| 差异格式 | `git diff --check` 通过；仅报告未修改静态 JSON 的既有 CRLF 转换提示 |
| 首次真实连接 | 第一帧为 `runtime.snapshot`，包含完整安全声明和四类运行投影 |
| 精确补发 | 同一流携带 sequence 1 重连，连续收到 sequence `2-11`，未重新编号 |
| 旧流回退 | 携带 `STREAM-OLD-001:1` 时只返回当前流 sequence 13 的 snapshot |
| REST 轮询 | 与 SSE snapshot 一致：`awaiting_confirmation`、revision `4`、场景 v2 和同一候选 |
| 真实重启 | 新流 `STREAM-D35CBCC6FAC948E0:1`；SQLite 状态、时间、版本和候选保持 |
| 环境清理 | 8013 无监听、无验收 Python 进程，17 个专用临时文件已删除 |

## 5. 已知边界

- 流状态和 256 条缓冲仅存在于单个 Uvicorn worker；M4 比赛版固定单 worker，不提供跨进程消息队列。
- 没有连接时不运行独立后台 tick worker；重新连接或 REST 轮询会先读取权威会话并物化准确业务边界，再补发可恢复消息或发送完整 snapshot。
- 长时间断线、服务重启或快速跨过多个临时状态时，完整 snapshot 是最终恢复依据；客户端不能假设每条历史语义消息永久可得。
- 前端尚未消费 SSE，也没有连接状态、自动轮询、运行控制或候选操作；这些全部属于 M4-5。
- 当前服务只推送匿名化仿真数据，不接入真实机场生产消息或控制链路。

## 6. M4-5 交接

M4-5 应直接消费现有 `RuntimeSessionSnapshot` 和 `RuntimeStreamEvent`：首次进入时创建或找回会话，连接 SSE 后按 stream ID + sequence 去重，连续失败时每 2 秒读取 REST 快照，恢复后停止轮询。五页工作台共享同一 snapshot、当前选择和连接状态；前端不得自行推进时钟、推导任务状态、应用事件或运行优化器。
