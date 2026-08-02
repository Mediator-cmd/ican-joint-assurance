# 联保智调

航班扰动下特殊旅客地面保障资源优化与推演平台。

## 项目定位

联保智调面向机场地面保障人员，使用匿名化仿真场景演示以下闭环：

1. 录入航班延误、登机口变更、资源减少等运行事件；
2. 将事件转换为可校验的结构化数据；
3. 在摆渡车、轮椅、服务人员等资源约束下生成任务计划；
4. 发生新事件后滚动重规划，并解释计划变化原因；
5. 对比 FIFO 先到先服务基线与优化方案的量化结果。

本项目不是通用聊天机器人、旅行规划工具或真实机场生产控制系统。比赛版本只使用匿名化、历史回放或仿真数据。

> **安全边界：仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。**

## 当前状态

- 阶段：M4 实时运行与动态重规划（M4-5B 方案依据完整展示修正已完成）。
- 下一单元：M4-6 端到端、性能、长时间、故障与三轮连续演示验收。
- 规范文档：[docs/project-plan.md](docs/project-plan.md)
- 持续交接文档：[docs/project-progress.md](docs/project-progress.md)
- 最终展示蓝图：[docs/demo-blueprint.md](docs/demo-blueprint.md)
- M3 阶段总复盘：[docs/m3-review.md](docs/m3-review.md)
- OpenAPI/Swagger 操作指南：[docs/m3-openapi-guide.md](docs/m3-openapi-guide.md)
- M4 实时运行计划：[docs/m4-realtime-operations-plan.md](docs/m4-realtime-operations-plan.md)
- M4-0 运行时契约：[docs/m4-runtime-contract.md](docs/m4-runtime-contract.md)
- M4-0 契约审查：[docs/m4-00-review.md](docs/m4-00-review.md)
- M4-1 实现审查：[docs/m4-01-review.md](docs/m4-01-review.md)
- M4-2 实现审查：[docs/m4-02-review.md](docs/m4-02-review.md)
- M4-3 实现审查：[docs/m4-03-review.md](docs/m4-03-review.md)
- M4-4 实现审查：[docs/m4-04-review.md](docs/m4-04-review.md)
- M4-5 实施计划：[docs/m4-05-plan.md](docs/m4-05-plan.md)
- M4-5 实现审查：[docs/m4-05-review.md](docs/m4-05-review.md)
- M4-5A 修正计划：[docs/m4-05a-plan.md](docs/m4-05a-plan.md)
- M4-5A 修正审查：[docs/m4-05a-review.md](docs/m4-05a-review.md)
- M4-5B 展示修正审查：[docs/m4-05b-review.md](docs/m4-05b-review.md)

## 目录结构

```text
docs/
  project-plan.md       # 总体规划、边界和里程碑
  project-progress.md   # 持续更新的进度、决策、验证和交接记录
  m3-openapi-guide.md   # Swagger 业务闭环操作说明
  m3-review.md          # M3 阶段总复盘与退出条件
  m4-runtime-contract.md # M4 运行时模型、REST/SSE 与状态机契约
  scenarios/            # 文档用场景说明
frontend/               # React + TypeScript + Vite 运行态控制台
backend/app/            # 数据模型、事件应用、FIFO、CP-SAT 和约束检查
data/scenarios/         # 匿名化 JSON 仿真场景
tests/                  # 数据校验、后端、前端和端到端测试
scripts/                # 场景校验、数据生成和发布辅助脚本
```

## 技术路线

- 前端：React、TypeScript、Vite、ECharts。
- 后端：Python、FastAPI、Pydantic。
- 调度：确定性 FIFO 基线与 Google OR-Tools CP-SAT 优化方案。
- 存储：开发阶段 SQLite，保留 PostgreSQL 适配空间。
- AI：仅负责自然语言事件理解、缺失字段追问和结果解释；调度结果必须经过确定性优化和硬约束校验。

## 开发顺序

1. M0：仓库和文档基线。
2. M1：`Scenario`、`FlightEvent`、`ServiceTask`、`Resource`、`Zone` 数据模型及最小 JSON 场景。
3. M2：FIFO 基线、约束校验和优化内核。
4. M3：后端业务 API。
5. M4：仿真实时运行、动态重规划与演示流程。
6. M5-M7：AI 事件理解、规模测试、比赛材料和发布。

每个里程碑都要有可复现的测试、Git commit 和进度记录。不得把 `.env`、密钥、个人信息或未经授权的真实机场数据提交到仓库。

## 当前运行说明

当前版本提供可运行的 React + FastAPI 本地演示。正常在线时，五个工作页共享同一个 SQLite 运行会话和权威 `RuntimeSessionSnapshot`，通过 SSE 自动接收仿真时钟、任务、资源、航班、事件和候选方案变化；页面提供开始、暂停、`1x/5x/15x`、重置、手动重规划以及候选采用/保留操作。

当前合成场景包含 08:00 至 08:52 的 6 条固定、一次性扰动以及 10 项匿名保障任务。每条事件到时由后端原子应用并生成独立滚动候选，场景在 09:30 正常完成，不循环播放、不随机制造业务变化。方案页直接展示后端当前/候选计划的中文算法、任务、资源、服务时段、路线、指标、人工协调原因和真实差异；方案 ID 仅作为次级审计信息，全部板块按内容自然展开且不使用内部滚动。

前端不自行推进时钟、推导业务状态或运行规划器。SSE 连续失败后，每 2 秒读取一次 REST 权威快照，流恢复后停止轮询；所有修改命令携带当前 revision，冲突时只刷新最新状态，不自动重放旧命令。只有运行 API 不可用时才显示原有三套静态快照，并明确标为“离线只读，时间不会推进”，所有运行控制禁用。

### Windows 一键启停

- 双击项目根目录的 `启动联保智调.cmd`：刷新演示数据，先启动 FastAPI，等待健康检查通过后再启动 Vite，并优先使用 Microsoft Edge 打开页面。
- 双击项目根目录的 `停止联保智调.cmd`：按“前端 → 后端”顺序只停止由状态文件记录的本项目服务，不会结束其他 Node/Python 进程，也不会关闭 Edge。
- 后端默认从 `8000-8020` 选择端口，前端默认从 `4173-4199` 选择端口；端口占用时自动换用范围内空闲端口，实际 URL 以 `.runtime/server-state.json` 为准。
- 运行状态和本地日志保存在忽略提交的 `.runtime/` 目录。启动脚本已运行时再次双击会复用已通过健康检查的服务组，只打开现有页面，不创建第二组进程。
- 启动失败会回收本次已创建的服务；停止前会校验项目路径、服务名、PID、进程名和启动时间，状态异常时保留文件并停止操作。

首次安装后端依赖：

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = (Resolve-Path .tmp).Path
$env:TMP = $env:TEMP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

生成演示数据并运行后端测试：

```powershell
.\.venv\Scripts\python.exe scripts\generate_demo_output.py
.\.venv\Scripts\python.exe -m pytest -q
```

启动当前 FastAPI 服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

- 健康检查：`http://127.0.0.1:8000/api/v1/health`
- OpenAPI 文档：`http://127.0.0.1:8000/docs`
- 场景列表：`http://127.0.0.1:8000/api/v1/scenarios`，首次启动自动提供一个仿真示例。
- 场景 API 支持结构化导入、可规划摘要、数据缺口提示、分页和当前/历史版本查询。
- 事件与计划 API 支持版本化结构事件应用、FIFO/CP-SAT 计划创建、不可变计划查询，以及普通语言结果摘要和人工确认提示。
- 运行 API 支持事件到时自动暂停、同刻事件批处理、滚动候选生成，以及使用 revision 和候选 ID 保护的采用/拒绝；`/stream` 提供共享 sequence、类型化消息、断线补发和完整快照回退，普通 GET 快照可作为短轮询入口。
- Swagger 中的完整操作顺序、请求体、重复提交处理和 PowerShell 示例见 [docs/m3-openapi-guide.md](docs/m3-openapi-guide.md)。
- M3 阶段能力、验证证据和已知边界见 [docs/m3-review.md](docs/m3-review.md)。

启动网站：

```powershell
Set-Location frontend
npm install
npm run dev
```

浏览器访问启动脚本输出的前端 URL。当前后端回归基线为 `130 passed`，前端回归基线为 `19 passed`，并应同时通过 `npm run typecheck` 和 `npm run build`。

## Git 工作流

- `main`：可演示、可提交版本。
- `develop`：集成分支。
- `feature/<name>`：单项功能开发分支。

提交前至少执行与当前阶段匹配的自动化测试，并在 `docs/project-progress.md` 记录验证结果、已知风险和下一步入口。
