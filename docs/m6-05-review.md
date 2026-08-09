# M6-5 单 origin 部署版本审查

## 1. 审查结论

M6-5 已完成。项目现在具有经过真实 Linux 容器构建与运行验证的 provider-neutral 单 origin 部署包：一个非 root Uvicorn worker 同时提供 React 静态产物、`/api/v1` 和 SSE；SQLite 写入持久化卷；请求体、错误和秘密边界均有自动化与真实 HTTP 证据。该结论只表示代码库已经具备部署版本，不表示已推送 GitHub、选择托管商或取得公网地址。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 实现范围

- `backend/app/deployment.py` 提供严格非秘密配置、实际字节请求体限制、统一 413、静态文件缓存策略和不会吞掉 API 错误的 SPA 回退。
- `backend/app/production.py` 固定监听 `0.0.0.0`、平台 `PORT` 和一个 worker，不暴露多 worker 参数。
- `backend/app/main.py` 让全局生产应用读取 SQLite、前端目录和请求上限，同时保持测试工厂和本地 Vite 双进程开发方式。
- 多阶段 `Dockerfile` 在 Linux Node 中执行 `npm ci`/Vite 构建，最终 Python 镜像使用 UID 10001、`/data` 卷和同源健康检查。
- `backend/requirements-runtime.txt` 冻结容器实际验证的五个直接运行依赖；开发清单再叠加 pytest，最终镜像不包含测试框架。
- `.dockerignore` 排除 Git、`.env*`、`.runtime`、虚拟环境、Node 依赖、构建产物、测试、文档、数据库和日志；Dockerfile 只复制明确运行目录，不使用 `COPY .`。

## 3. 审查中发现并修正的问题

### 3.1 Windows 锁文件不能通过 Linux `npm ci`

首次容器构建发现 `package-lock.json` 缺少 Linux npm 对可选 WASM peer 的顶层 `@emnapi/core` 与 `@emnapi/runtime` 锁项。Windows 现有 `node_modules` 能构建，但干净 Linux 构建会直接失败。使用已缓存的 Linux Node 22 环境重新规范化锁文件后，Windows `npm ci` 与 Linux Docker `npm ci` 均通过；没有为此增加业务依赖或改动 `package.json`。

### 3.2 测试依赖进入最终镜像

初版 Dockerfile 直接安装 `backend/requirements.txt`，会把 pytest 带入生产层。现已拆出冻结的最小运行清单，最终镜像探针确认 `pytest` 不存在；本地开发入口继续正常安装 pytest。

### 3.3 审计镜像站不支持 npm audit

默认 npm 镜像对安全审计端点返回 404，这不是“零漏洞”证据。最终使用 npm 官方只读审计端点复核生产依赖，结果为 `found 0 vulnerabilities`；未执行自动升级或 `audit fix`。

## 4. 自动化证据

- M6-5 配置、同源静态服务、API 404 隔离、声明/流式请求体上限、重启持久化、单 worker 和容器契约专项均通过。
- 后端全量：`267 passed in 31.53s`，使用与最终镜像相同的直接 Python 依赖版本。
- 前端干净安装：Windows `npm ci` 成功；5 个测试文件、`32 passed`；`npm run typecheck` 与 Vite 生产构建通过。
- Python：`compileall`、`pip check` 通过；PowerShell 脚本语法和 `git diff --check` 通过。
- 前端生产依赖：npm 官方审计端点报告 0 项漏洞。

## 5. 真实部署证据

- Docker Desktop Linux 29.2.1 使用缓存基础镜像和干净构建上下文完成多阶段镜像 `ican-m6-5:local`；Linux `npm ci`、Vite 构建和 Python 运行依赖安装均成功。
- 容器健康状态为 `healthy`，`/api/v1/health` 返回 `ok`，同一端口 `/` 返回 React HTML，SPA 路由回退一致，未知 API 返回统一 JSON `not_found`。
- 镜像配置用户为 `jointassurance:jointassurance`，实际 UID 为 10001；`/data/runtime-sessions.sqlite3` 可写。
- 在第一个容器创建运行会话后删除容器，以同一命名卷启动替代容器，原 session ID、revision 和状态均可找回。
- 未注入 AI 环境变量时，辅助接口公开 `trace.source=deterministic_rules`、`provider_attempted=false`、`fallback_reason=model_not_configured`，且继续要求人工确认、禁止自动应用。
- 最终镜像健康和同源 HTTP 再次通过，镜像内 `pip show pytest` 确认不存在测试依赖。所有 M6-5 测试容器和测试卷已删除，本地验证镜像保留且未推送。

## 6. 安全与状态所有权

- 前端仍使用相对 `/api/v1` 和 SSE 地址，不读取 AI 变量，不增加 CORS。
- 请求体默认上限为 2 MiB；没有 `Content-Length` 的流式输入也按实际字节限制。413 只包含有限代码、中文消息和 request ID，不回显正文。
- 未处理异常继续只记录 request ID 和异常类型，不记录路径、正文、密钥或提供方错误细节。
- 单 origin 层只负责传输和静态文件；仿真时钟、revision、事件、方案、候选和人工决定仍由既有后端权威状态唯一拥有。
- 没有读取、输出、写入或提交真实 API key；本机 DPAPI 文件被构建上下文排除。

## 7. 已知边界与下一入口

- 当前只支持一个副本、一个 Uvicorn worker、一个 SQLite 文件和一个内存 SSE broker。多副本会破坏 SSE 补发与 SQLite 单实例假设，必须等新的消息总线/数据库架构阶段。
- 平台必须提供持久化卷并正确挂载 `/data`；只运行临时文件系统会在重建实例时丢失会话。
- M6-5 没有创建 GitHub 远程、推送源码或镜像、选择托管商、配置线上秘密或发布公网地址；这些仍属于 M7。
- 没有接真实机场生产数据、个人信息或外部控制系统。
- 下一唯一入口为 M6-5A：在本阶段同一快照、同一 origin、单实例和秘密边界上实现原创 2D 空间态势、方案路径与 `SPATIAL-FACT-*` AI 进程问答，不得创建第二业务状态。
