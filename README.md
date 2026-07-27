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

- 阶段：M2 调度内核与运行态网站雏形已完成。
- 下一阶段：M3 FastAPI 业务接口与前后端联调。
- 规范文档：[docs/project-plan.md](docs/project-plan.md)
- 持续交接文档：[docs/project-progress.md](docs/project-progress.md)
- 最终展示蓝图：[docs/demo-blueprint.md](docs/demo-blueprint.md)

## 目录结构

```text
docs/
  project-plan.md       # 总体规划、边界和里程碑
  project-progress.md   # 持续更新的进度、决策、验证和交接记录
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
5. M4：运行态前端与演示流程。
6. M5-M7：AI 事件理解、规模测试、比赛材料和发布。

每个里程碑都要有可复现的测试、Git commit 和进度记录。不得把 `.env`、密钥、个人信息或未经授权的真实机场数据提交到仓库。

## 当前运行说明

当前版本提供可运行的 React 网站雏形。页面数据由 Python 调度引擎生成，包含“扰动前 FIFO”“事件后 FIFO”和“CP-SAT 优化”三种视图；M3 才会把静态 JSON 读取替换为 FastAPI 请求。

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

启动网站：

```powershell
Set-Location frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:4173`。当前回归基线为 `19 passed`，前端应同时通过 `npm run typecheck` 和 `npm run build`。

## Git 工作流

- `main`：可演示、可提交版本。
- `develop`：集成分支。
- `feature/<name>`：单项功能开发分支。

提交前至少执行与当前阶段匹配的自动化测试，并在 `docs/project-progress.md` 记录验证结果、已知风险和下一步入口。
