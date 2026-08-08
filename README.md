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

- 阶段：M6 规模测试、体验收敛与部署准备（M6-0 已完成）。
- 下一单元：M6-1 确定性合成规模场景工厂。
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
- M4-5C 一键启停修正审查：[docs/m4-05c-review.md](docs/m4-05c-review.md)
- M4-6 总验收计划：[docs/m4-06-plan.md](docs/m4-06-plan.md)
- M4-6 总验收审查：[docs/m4-06-review.md](docs/m4-06-review.md)
- M5 AI 辅助实施计划：[docs/m5-ai-assistance-plan.md](docs/m5-ai-assistance-plan.md)
- M5-0 AI 辅助契约：[docs/m5-ai-contract.md](docs/m5-ai-contract.md)
- M5-0 契约审查：[docs/m5-00-review.md](docs/m5-00-review.md)
- M5-1 规则解析审查：[docs/m5-01-review.md](docs/m5-01-review.md)
- M5-2 模型回退审查：[docs/m5-02-review.md](docs/m5-02-review.md)
- M5-3 人工提交与目标配置审查：[docs/m5-03-review.md](docs/m5-03-review.md)
- M5-4 方案事实与可追溯解释计划：[docs/m5-04-plan.md](docs/m5-04-plan.md)
- M5-4 方案事实与可追溯解释审查：[docs/m5-04-review.md](docs/m5-04-review.md)
- M5-5 前端辅助交互计划：[docs/m5-05-plan.md](docs/m5-05-plan.md)
- M5-5 前端辅助交互审查：[docs/m5-05-review.md](docs/m5-05-review.md)
- M5-6 AI 辅助总验收计划：[docs/m5-06-plan.md](docs/m5-06-plan.md)
- M5-6 AI 辅助总验收审查：[docs/m5-06-review.md](docs/m5-06-review.md)
- M6 规模与部署准备计划：[docs/m6-scale-deployment-plan.md](docs/m6-scale-deployment-plan.md)
- M6-0 规模与基准契约：[docs/m6-scale-contract.md](docs/m6-scale-contract.md)
- M6-0 契约审查：[docs/m6-00-review.md](docs/m6-00-review.md)
- M6-0B 真实 AI 激活审查：[docs/m6-00b-real-ai-activation-review.md](docs/m6-00b-real-ai-activation-review.md)
- M6-0A 一键启动陈旧 PID 恢复审查：[docs/m6-00a-startup-recovery-review.md](docs/m6-00a-startup-recovery-review.md)

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

M5-2 已提供可替换的 OpenAI-compatible 结构化输出适配器。`POST /api/v1/assistant/event-drafts` 在 `auto` 模式且进程环境完整配置 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 时先尝试模型；未配置、超时、提供方错误或输出未通过结构、原文证据、权威实体与时间核对时，自动回退 M5-1 确定性规则。`deterministic_only` 始终零外部调用。草稿生成本身不写状态；只有用户通过 `POST /api/v1/assistant/event-drafts/submit` 回传完整草稿并显式确认事件后，才会进入原有 version/revision 保护链。运行事件还必须人工确认四选一的有限调度目标，生成的候选仍需人工采用或保留。

M5-3 的确定性目标只允许 `balanced`、`critical_first`、`minimum_wait` 和 `minimum_change`。旧 CP-SAT 请求未显式选择目标时继续精确使用原 `balanced` 行为；`minimum_change` 只用于已有当前方案的运行滚动规划。所有目标仍由 CP-SAT 求解并经过独立硬约束复核，模型不能提供任意权重、自由排班或自动采用候选。

M5-4 新增只读 `POST /api/v1/assistant/plan-explanations`。服务从场景版本或运行 revision 的后端权威事实生成确定性中文解释；摘要、取舍和建议下一步都带 `FACT-*` 来源。配置 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 后，OpenAI-compatible 模型只做表达增强，任何超时、错误或事实引用不合法都会回退规则解释。无 AI 配置时核心实时闭环和完整解释照常可用，密钥不会进入 GitHub、前端或响应。

M5-5 已把上述能力接入现有五页共享运行工作台。“事件影响”页可输入匿名教学事件、查看缺失追问和逐字段来源，在确认事件字段及四选一确定性目标后提交；“方案依据”页可切换当前/候选方案、解释重点和匿名问题，并查看规则/模型来源、结论和 `FACT-*` 引用。输入、revision、方案或解释条件变化后旧结果立即标记过期；事件不会自动提交，候选不会自动采用。新板块按内容自然展开，不使用卡片内部滚动，前端不包含 API 密钥、提供方 URL 或硬编码模型名。

M5-6 已完成模型/无模型、超时/错误/非法输出、未知实体、非法事实引用、提示词注入和同一会话连续三轮总验收。自动测试证明模型只增强事件抽取与事实表达；真实浏览器在每轮都要求事件与有限目标双重确认，候选只在人工采用后成为当前方案。无模型配置时页面明确显示规则回退，核心闭环、中文方案任务书和 `FACT-*` 解释保持完整可用。真实模型密钥只允许通过后端进程环境或部署 secret manager 注入，不能进入仓库、前端或响应。

M6-0 已冻结三个匿名合成规模档位：100 任务/20 资源/5 区域、500/50/10、2000 个聚合任务/200/20，对应 2/5/15 秒测试目标。严格契约要求每次基准公开实际 CP-SAT 或有限回退路径、完整任务覆盖和零硬约束违规，并从样本计算 p50/p95/最大值；当前只完成契约，尚未生成规模场景或声称性能达标。M6 负责形成部署版本，M7 才负责 GitHub 发布和稳定公网地址。

前端不自行推进时钟、推导业务状态或运行规划器。SSE 连续失败后，每 2 秒读取一次 REST 权威快照，流恢复后停止轮询；所有修改命令携带当前 revision，冲突时只刷新最新状态，不自动重放旧命令。只有运行 API 不可用时才显示原有三套静态快照，并明确标为“离线只读，时间不会推进”，所有运行控制禁用。

### Windows 一键启停

- 双击项目根目录的 `启动联保智调.cmd`：刷新演示数据，先启动 FastAPI，等待健康检查通过后再启动 Vite，并优先使用 Microsoft Edge 打开页面。
- 双击项目根目录的 `停止联保智调.cmd`：按“前端 → 后端”顺序只停止由状态文件记录的本项目服务，不会结束其他 Node/Python 进程，也不会关闭 Edge。
- 双击项目根目录的 `配置AI.cmd`：在本机遮蔽输入 OpenAI-compatible API key、base URL 和模型名；密钥使用当前 Windows 用户 DPAPI 加密写入忽略提交的 `.runtime/ai-config.json`，随后自动重启服务。默认 DeepSeek 模型为 `deepseek-v4-flash`。
- 后端默认从 `8000-8020` 选择端口，前端默认从 `4173-4199` 选择端口；端口占用时自动换用范围内空闲端口，实际 URL 以 `.runtime/server-state.json` 为准。
- 运行状态和本地日志保存在忽略提交的 `.runtime/` 目录。启动脚本已运行时再次双击会复用已通过健康检查的服务组，只打开现有页面，不创建第二组进程。
- 状态文件保存明确的 Python/Node 可执行路径；旧版误记为系统 DLL 时，只允许按项目 Python 和当前 Node 可信路径兼容校验，其他进程身份不匹配仍会拒绝操作。
- 若服务已停止后旧 PID 被其他程序复用，启动脚本会把该记录识别为陈旧状态并安全重建服务组；停止脚本只跳过该无关进程并清理陈旧状态，不会按 PID 误杀。
- AI 配置变化会使启动脚本重启后端，避免继续复用未配置模型的旧进程；配置文件解密失败会阻止启动并要求重新配置。后端只在启动子进程时短暂注入密钥，前端、状态 API、日志和 Git 均不接触明文。
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
- 辅助 API 支持确定性规则和可选 OpenAI-compatible 模型抽取；模型只接收当前文本、参考时间窗、匿名航班和登机口最小事实，密钥只从进程环境读取，响应公开安全来源与回退原因。草稿只能在用户明确确认事件和运行目标后提交，不能由模型自动提交。
- 真实模型验证：模型请求成功时，事件草稿或方案解释响应的 `trace.source` 必须为 `language_model`，并带实际配置的 `trace.model_label`；任何超时、提供方错误或非法结构仍回退确定性规则。DeepSeek 官方 OpenAI 格式文档见 [Your First API Call](https://api-docs.deepseek.com/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode)。
- Swagger 中的完整操作顺序、请求体、重复提交处理和 PowerShell 示例见 [docs/m3-openapi-guide.md](docs/m3-openapi-guide.md)。
- M3 阶段能力、验证证据和已知边界见 [docs/m3-review.md](docs/m3-review.md)。

启动网站：

```powershell
Set-Location frontend
npm install
npm run dev
```

浏览器访问启动脚本输出的前端 URL。当前后端回归基线为 `218 passed`，前端回归基线为 `24 passed`，并应同时通过 `npm run typecheck` 和 `npm run build`。

## Git 工作流

- `main`：可演示、可提交版本。
- `develop`：集成分支。
- `feature/<name>`：单项功能开发分支。

提交前至少执行与当前阶段匹配的自动化测试，并在 `docs/project-progress.md` 记录验证结果、已知风险和下一步入口。
