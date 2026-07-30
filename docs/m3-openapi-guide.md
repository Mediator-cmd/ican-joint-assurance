# M3 OpenAPI 与 Swagger 操作指南

## 1. 用途与边界

本指南用于在不打开前端、不编写额外程序的情况下，通过 FastAPI 自带的 Swagger 页面复核 M3 业务闭环：

```text
查询场景 -> 生成 FIFO -> 应用事件 -> 生成 CP-SAT -> 比较方案 -> 查询审计
```

当前接口处理匿名化、历史回放或仿真数据。所有页面和响应遵守以下边界：

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

M3 使用进程内仓库。场景版本、已生成计划和审计记录只在当前后端进程中存在；后端重启后会重新加载内置仿真场景。这不是数据丢失缺陷，而是 M3 已明确的阶段边界，SQLite 持久化属于 M4。

## 2. 打开接口文档

推荐双击项目根目录的 `启动联保智调.cmd`。脚本会先启动后端，再启动前端，并将实际端口写入 `.runtime/server-state.json`。

默认地址：

- Swagger：`http://127.0.0.1:8000/docs`
- OpenAPI JSON：`http://127.0.0.1:8000/api/v1/openapi.json`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

若 `8000` 已被占用，应以 `.runtime/server-state.json` 中后端服务的 `url` 为准，在其后追加 `/docs`。也可只启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Swagger 中每次调用的基本步骤均为：展开接口，点击 `Try it out`，填写参数或请求体，再点击 `Execute`。

## 3. 内置演示场景

后端启动时自动提供以下匿名化仿真场景：

| 字段 | 值 |
| --- | --- |
| 场景 ID | `SCN-TERMINAL-DISTURBANCE-01` |
| 初始版本 | `1` |
| 待处理事件 | `EVT-SIM102-DELAY`、`EVT-SIM218-GATE` |
| 初始规模 | 2 个航班、5 项任务、4 项资源、3 个区域 |
| 数据分类 | `synthetic` |

建议在一个新启动的后端进程中按下列顺序执行。若接口返回 `409`，先根据错误中的 `code` 和提示查询当前场景或已有方案，不要盲目重复提交。

## 4. Swagger 完整闭环

### 4.1 检查服务和场景

1. 执行 `GET /api/v1/health`，确认 `status` 为 `ok`。
2. 执行 `GET /api/v1/scenarios`，找到内置场景 ID。
3. 执行 `GET /api/v1/scenarios/{scenario_id}`，填写 `SCN-TERMINAL-DISTURBANCE-01`。
4. 确认 `current_version` 为 `1`，并记录 `pending_event_ids`。

### 4.2 生成版本 1 的 FIFO 原规则方案

执行 `POST /api/v1/scenarios/{scenario_id}/plans`，场景 ID 使用内置场景，请求体为：

```json
{
  "expected_version": 1,
  "algorithm": "fifo",
  "max_time_seconds": 2
}
```

成功状态码为 `201`。记录响应中的 `plan.plan_id` 作为基线方案 ID。内置场景的预期结果为完成 `4/5` 项任务、紧急任务完成率 `50%`、硬约束违规 `0`。

### 4.3 应用两项结构化事件

执行 `POST /api/v1/scenarios/{scenario_id}/events/apply`：

```json
{
  "expected_version": 1,
  "event_ids": [
    "EVT-SIM102-DELAY",
    "EVT-SIM218-GATE"
  ],
  "events": []
}
```

成功后 `current_version` 变为 `2`，两项事件进入 `applied_event_ids`。同一事件不能重复应用；重复请求返回 `409 event_already_applied`，且不会再次累计延误。

### 4.4 生成版本 2 的 CP-SAT 系统优化建议

再次执行 `POST /api/v1/scenarios/{scenario_id}/plans`：

```json
{
  "expected_version": 2,
  "algorithm": "cp_sat",
  "max_time_seconds": 2
}
```

成功后记录新的 `plan.plan_id` 作为候选方案 ID。预期结果为完成 `5/5` 项任务、紧急任务完成率 `100%`、硬约束违规 `0`。响应中的 `requires_human_confirmation` 始终为 `true`，它在 M3 只是提示和策略字段；M3 尚无确认接口或运行状态切换，实际确认流程属于 M4。

### 4.5 比较两套已保存方案

执行 `POST /api/v1/scenarios/{scenario_id}/comparisons`，将前两步返回的真实方案 ID 填入请求体：

```json
{
  "baseline_plan_id": "在 4.2 中返回的方案 ID",
  "candidate_plan_id": "在 4.4 中返回的方案 ID"
}
```

成功状态码为 `201`。`candidate_minus_baseline` 表示“候选方案减基线方案”，不是相反方向。内置场景应显示已分配任务 `+1`、待协调任务 `-1`、紧急任务完成率 `+50` 个百分点，两个方案违规数均为 `0`。

### 4.6 查询方案和审计

- `GET /api/v1/scenarios/{scenario_id}/plans`：列出本场景已保存方案，可按 `version` 或 `algorithm` 筛选。
- `GET /api/v1/plans/{plan_id}`：回查完整方案、普通语言结论和人工确认提示。
- `GET /api/v1/scenarios/{scenario_id}/audit-records`：查询审计时间线。

完整闭环的审计动作顺序应为：

```text
scenario_imported -> plan_created -> events_applied -> plan_created -> comparison_created
```

方案比较只新增审计记录，不修改场景版本或已保存方案。

## 5. PowerShell 可复现示例

以下示例假设使用一个刚启动、尚未执行过规划的默认后端：

```powershell
$apiBase = "http://127.0.0.1:8000/api/v1"
$scenarioId = "SCN-TERMINAL-DISTURBANCE-01"

$baselineBody = @{
    expected_version = 1
    algorithm = "fifo"
    max_time_seconds = 2
} | ConvertTo-Json
$baseline = Invoke-RestMethod -Method Post -Uri "$apiBase/scenarios/$scenarioId/plans" -ContentType "application/json" -Body $baselineBody

$eventsBody = @{
    expected_version = 1
    event_ids = @("EVT-SIM102-DELAY", "EVT-SIM218-GATE")
    events = @()
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiBase/scenarios/$scenarioId/events/apply" -ContentType "application/json" -Body $eventsBody

$candidateBody = @{
    expected_version = 2
    algorithm = "cp_sat"
    max_time_seconds = 2
} | ConvertTo-Json
$candidate = Invoke-RestMethod -Method Post -Uri "$apiBase/scenarios/$scenarioId/plans" -ContentType "application/json" -Body $candidateBody

$comparisonBody = @{
    baseline_plan_id = $baseline.plan.plan_id
    candidate_plan_id = $candidate.plan.plan_id
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiBase/scenarios/$scenarioId/comparisons" -ContentType "application/json" -Body $comparisonBody

Invoke-RestMethod -Uri "$apiBase/scenarios/$scenarioId/audit-records"
```

若实际后端端口不是 `8000`，只需修改 `$apiBase`。不要把响应中的动态方案 ID 替换为文档中的占位文字。

## 6. 错误响应与恢复方法

所有公开错误都使用统一结构：

```json
{
  "error": {
    "code": "version_conflict",
    "message": "面向用户的安全说明",
    "details": [],
    "request_id": "REQ-..."
  }
}
```

| 状态码 | 常见原因 | 正确处理 |
| --- | --- | --- |
| `400` | 场景缺少规划条件、事件批次不适用、方案不能比较 | 按 `message` 检查业务前提 |
| `404` | 场景、版本、事件或方案不存在 | 先调用列表或详情接口确认 ID |
| `409` | 版本过期、事件重复、同版本同算法方案已存在 | 刷新场景或查询已有方案，不重复写入 |
| `422` | JSON 字段、类型、范围或请求体结构错误 | 根据 `details.location` 修改输入 |
| `500` | 服务无法完成请求 | 使用响应头或正文中的 `request_id` 定位日志，不依赖内部异常文本 |

事件应用和计划创建等依赖场景版本的写操作，必须使用详情接口返回的最新 `current_version` 作为 `expected_version`。方案比较只提交两套已保存方案的 ID，不使用 `expected_version`。遇到版本冲突时先重新读取状态，不能简单把版本号加一后重试。

## 7. `/demo` 与业务接口的区别

`GET /api/v1/demo` 返回与前端离线 JSON 完全兼容的确定性演示载荷，用于当前 M3 五页工作台加载和故障回退验证。它不会修改场景仓库，也不代表一次正在推进的运行会话。

需要验证版本、事件、计划、比较和审计时，应使用 `/scenarios`、`/plans`、`/comparisons` 和 `/audit-records` 业务接口。仿真时钟、自动状态变化、事件到时触发重规划和 SSE 推送属于 M4，不应从 M3 `/demo` 接口推断为已完成。
