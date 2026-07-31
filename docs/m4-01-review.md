# M4-1 运行会话与权威时钟审查记录

## 1. 审查结论

M4-1 已完成。后端现在可以从已保存的仿真场景和无硬约束违规方案创建独立运行会话，并通过 SQLite 持久化关键运行边界。开始、暂停、继续、倍速切换和确认重置均由后端状态机和 `expected_revision` CAS 控制；运行中的仿真时间由单调时钟计算，不逐秒写入数据库。

本阶段没有实现任务、资源、航班和事件的动态投影，没有实现自动事件、滚动重规划、候选方案确认或 SSE。前端仍是 M3 快照式工作台，不能把 M4-1 API 描述为完整实时演示。

所有运行响应继续固定包含：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 已实现范围

### 2.1 SQLite 仓库

- 新增 `backend/app/runtime_repository.py`，使用标准库 `sqlite3`、`RLock` 和 `BEGIN IMMEDIATE`。
- `runtime_sessions` 保存场景版本、初始/当前方案、状态、revision、仿真时间边界、倍速、失败摘要和创建/更新时间。
- `runtime_control_audit` 保存创建、开始、暂停、倍速、重置、窗口完成和服务重启等关键动作；tick 不产生审计写入。
- 更新必须满足 `WHERE session_id = ? AND revision = ?`，状态和审计在同一事务中提交；陈旧写入只返回冲突，不留下部分更新。
- `GET /runtime-sessions` 支持场景、状态、offset、limit 筛选；服务启动时可从列表重新发现会话。

### 2.2 权威时钟

- 新增 `backend/app/runtime_services.py`，墙钟和单调时钟均可注入。
- 运行中使用“最近持久仿真时间 + 单调经过时间 × 倍速”计算快照时间；系统墙钟回拨不会让仿真时间倒退。
- 暂停时固定当前仿真时间，倍速切换先固定旧倍速时间再重建锚点，重置回到场景窗口起点。
- 到达场景窗口终点时只持久化一次 `runtime_completed`，并将状态变为 `completed`；列表筛选会先结算已到终点的运行会话。
- 服务重启将 `running/replanning` 原子恢复为 `paused` 并增加 revision；`awaiting_confirmation` 在本阶段虽未生成，但若已存在会保留候选和冻结时间。

### 2.3 REST 与错误

M4-1 公开以下六组路径：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/v1/runtime-sessions` | 创建运行会话 |
| `GET` | `/api/v1/runtime-sessions` | 分页查找 SQLite 会话 |
| `GET` | `/api/v1/runtime-sessions/{session_id}` | 获取权威快照 |
| `POST` | `.../{session_id}/start` | 开始或继续 |
| `POST` | `.../{session_id}/pause` | 暂停并固定时间 |
| `POST` | `.../{session_id}/speed` | 设置 `1x/5x/15x` |
| `POST` | `.../{session_id}/reset` | `confirm_reset: true` 后重置 |

公开 OpenAPI 新增 `runtime` 标签，但没有提前暴露 `replan`、候选采用/拒绝或 `stream` 路径。

统一错误沿用 M3 的 `ApiErrorResponse` 和 `X-Request-ID`，并新增：

- `runtime_session_not_found`
- `runtime_plan_not_found`
- `runtime_plan_scenario_mismatch`
- `runtime_plan_version_mismatch`
- `runtime_plan_has_violations`
- `runtime_revision_conflict`
- `runtime_invalid_transition`
- `runtime_persistence_error`

## 3. 关键不变量

1. 新会话从 `ready`、revision `1` 和场景 `window_start` 开始。
2. 只有 `running` 快照的 clock 标记为 advancing。
3. 所有控制命令携带 `expected_revision`；每个持久动作恰好增加 1 个 revision。
4. 已暂停时间不受后续墙钟经过影响；倍速切换不产生时间跳跃。
5. 方案必须属于请求场景和版本，且硬约束违规数为 `0`。
6. 会话列表和快照都带有 SQLite 存储标识和完整安全声明。
7. 服务重启只恢复到最近持久边界，不假装恢复未写入数据库的逐秒 tick。

## 4. 验证证据

| 检查 | 结果 |
| --- | --- |
| M4-0 契约测试 | `14 passed` |
| M4-1 仓库、时钟与 API 专项 | 通过；覆盖 SQLite、CAS、回拨、窗口终点、重启和错误映射 |
| 后端全量回归 | `95 passed` |
| Python 编译 | `python -m compileall -q backend tests` 通过 |
| 依赖检查 | `pip check`：No broken requirements found |
| 真实 Uvicorn HTTP | 创建 → 开始 → 约 1.2 秒墙钟推进约 18 秒仿真 → 暂停通过 |
| 真实重启恢复 | 重启后列表找回同一会话，`running` revision `4` 恢复为 `paused` revision `5` |
| 差异格式 | `git diff --check` 通过 |

专项测试使用仓库内忽略目录作为 pytest 临时目录；系统默认临时目录受当前 Windows 沙箱 ACL 限制，不能作为业务失败依据。

## 5. 已知边界与风险

- 当前 SQLite 默认文件为 `.runtime/runtime-sessions.sqlite3`，该目录已被 `.gitignore` 忽略；比赛单机版本仍固定单 Uvicorn worker。
- M3 场景和方案仓库仍是进程内存储。M4-1 可以恢复运行控制状态，但 M4-2/M4-3 若需跨重启读取方案细节，还需继续持久化场景版本和方案投影。
- 没有后台 worker 持续写 tick；客户端查询快照时按单调时钟计算当前时间，SSE 属于 M4-4。
- 任务/资源/事件数组在 M4-1 返回空列表，属于已声明的投影边界，不代表场景中没有业务对象。

## 6. 下一步

进入 M4-2：实现基于当前仿真时间的任务、资源、航班和事件确定性状态投影，覆盖每个左闭右开边界，并让快照中的空数组变为可复核的动态状态。M4-3 之前不得实现事件自动重规划或候选方案静默替换。
