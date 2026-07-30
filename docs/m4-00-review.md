# M4-0 实时运行契约审查记录

## 1. 审查结论

M4-0 已完成。运行会话、权威仿真时钟、任务/资源/事件状态、REST 控制、SSE 消息、revision 冲突、SQLite 恢复和错误语义已形成可执行 Pydantic 契约，并通过专项与全量测试。

本阶段没有实现运行路由、后台时钟、SQLite、自动事件、重规划或前端实时更新，因此当前网站仍是 M3 的一次性方案快照。

安全边界保持不变：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 冻结内容

### 2.1 运行会话

- 会话状态：`ready`、`running`、`paused`、`replanning`、`awaiting_confirmation`、`completed`、`failed`。
- 创建会话必须绑定场景版本和已保存初始方案，初始 revision 为 1，时间从场景窗口起点开始。
- 只有 running 状态的时钟可以推进；重规划和等待确认期间时间冻结。
- 候选方案只允许出现在 awaiting_confirmation，采用或拒绝后均返回 paused。

### 2.2 状态投影

- 任务状态冻结为待出发、前往服务点、现场等待、保障中、已完成、待协调和受扰动待确认。
- 前往、等待、保障中和已完成任务自动标记为 locked，滚动重规划不得改写。
- 资源状态冻结为空闲、移动、等待、服务中和不可用；位置只表达区域间进度，不伪造经纬度。
- 事件状态覆盖等待、触发、应用、重规划、待确认、完成和失败，并校验候选方案与失败码出现范围。

### 2.3 REST 与恢复

- 冻结创建、列表、详情、开始、暂停、倍速、重置、手动重规划、候选采用/拒绝和 stream 共 11 条路径。
- 补充 `GET /runtime-sessions`，避免 SQLite 已恢复但浏览器只能依赖本地 session ID。
- 所有控制命令使用 expected_revision；重置要求 `confirm_reset: true`；候选操作同时校验 candidate plan ID。
- 服务重启后 running/replanning 安全恢复为 paused，awaiting_confirmation 保留候选并继续冻结。

### 2.4 SSE

- revision 表示持久业务状态版本，sequence 表示每条消息顺序，二者不混用。
- tick 和 heartbeat 不增加 revision，但每条消息增加 sequence。
- 同一事务产生的多条消息共享新 revision，以 sequence 排序。
- Last-Event-ID 无法从 256 条缓冲恢复时，服务发送完整 snapshot。

## 3. 代码与文档

| 文件 | 作用 |
| --- | --- |
| `backend/app/runtime_models.py` | 运行枚举、请求、快照、投影与状态不变量 |
| `backend/app/runtime_stream_models.py` | SSE payload 判别联合、消息 envelope 和 sequence |
| `tests/test_runtime_contracts.py` | 时区、倍速、revision、状态一致性、候选方案和 SSE 契约测试 |
| `docs/m4-runtime-contract.md` | 状态机、时间边界、OpenAPI 草案、错误、恢复和实施约束 |
| `docs/m4-realtime-operations-plan.md` | M4 总进度与后续单元入口 |

## 4. Review 阶段发现与处理

| 发现 | 风险 | 处理 |
| --- | --- | --- |
| 原总计划要求客户端只接受更大 revision | tick/heartbeat revision 不变，会被错误丢弃 | 分离持久 revision 与流 sequence，并修改总计划 |
| 原路径没有会话列表 | 重启或换设备后无法从 SQLite 找回会话 | 增加分页会话列表及摘要模型 |
| 安全声明只在完整 snapshot | 会话列表成为不含边界的运行响应 | 列表响应也固定完整 safety notice |
| affected 状态未强制保留当前安排 | 无法判断应冻结或重排的任务 | 要求 assignment、resource 和 event IDs 同时存在 |
| 事件候选 ID/失败码可出现在任意状态 | 前端可能显示自相矛盾的处理过程 | 用模型验证限制其合法状态 |
| M3 同版本同算法计划去重 | 同一运行版本无法产生多次滚动候选 | M4-3 改按 session + revision 标识运行时候选 |

未发现阻塞 M4-1 的高、中或低优先级遗留缺陷。

## 5. 验证结果

| 检查 | 结果 |
| --- | --- |
| M4-0 专项测试 | `14 passed` |
| 后端全量回归 | `81 passed` |
| Python 编译 | `compileall` 通过 |
| Python 依赖 | `pip check` 无冲突 |
| Pydantic 严格性 | 未知字段、无时区时间、非法倍速和状态矛盾均拒绝 |
| 契约格式 | `git diff --check` 通过 |

M4-0 未修改前端代码，不重复执行 M3 已完成的前端构建和浏览器检查。

## 6. 已知边界

- `storage_scope: sqlite` 是已冻结的 M4 响应契约，数据库实现从 M4-1 开始。
- OpenAPI 路径目前只存在于契约草案，未实现的路由不会提前暴露在当前 `/docs`。
- 时间边界和状态优先级已经定义，但实际投影引擎属于 M4-2。
- SSE 载荷已经严格建模，但连接、缓冲和重连实现属于 M4-4。
- 当前 `8000/4173` 服务仍运行 M3 快照页面，本阶段没有将其伪装成实时系统。

## 7. 下一步

进入 M4-1：实现 SQLite 会话仓库、可注入时钟、会话创建/列表/详情，以及开始、暂停、倍速和重置控制。M4-1 复用本阶段模型，不提前实现 M4-2 状态投影或 M4-4 SSE。
