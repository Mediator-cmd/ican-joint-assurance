# M6-5 单 origin 部署版本实施计划

## 1. 目标

把 M6-4 已验证的 React、FastAPI、SQLite、SSE 和可选 OpenAI-compatible AI 能力打包为 provider-neutral 的单 origin 部署版本。部署进程必须同时提供前端静态产物和 `/api/v1`，在一个非 root 容器、一个 Uvicorn worker、一个 SQLite 持久化卷内运行，并在没有 AI 环境变量时保持完整确定性闭环。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 固定技术方案

### 2.1 单 origin 与静态产物

- Vite 继续使用相对同源的 `/api/v1` 和 SSE 地址，不增加部署提供方 URL 或 CORS 通配配置。
- FastAPI 在 API 路由之后提供 `frontend/dist` 中的构建产物；真实静态文件按原路径返回，前端路由回到 `index.html`。
- `/api`、`/api/v1` 及其未知子路径永远保留 JSON API 错误语义，不能被 SPA 回退替换为 HTML。
- `index.html` 禁止缓存，带内容摘要的 `/assets` 文件允许长期缓存；本地 Vite 开发链保持不变。

### 2.2 运行配置与状态所有权

- `APP_RUNTIME_DATABASE_PATH` 指定 SQLite 文件；容器固定为 `/data/runtime-sessions.sqlite3`，`/data` 声明为持久化卷。
- `APP_FRONTEND_DIST_PATH` 指定前端构建目录；容器固定为 `/app/frontend/dist`。
- `APP_MAX_REQUEST_BODY_BYTES` 指定 API 请求体上限，默认 `2097152` 字节，允许的安全范围固定为 64 KiB 至 16 MiB。
- `PORT` 只决定监听端口；生产入口固定 `0.0.0.0` 和一个 worker，不提供多 worker 开关。
- 前端静态服务、请求限制和部署配置不创建第二时钟、第二规划器、第二会话状态或第二 SQLite 写入路径。

### 2.3 安全与秘密

- 多阶段容器先构建 React，再安装冻结的最小后端运行依赖；最终镜像不包含 Node、pytest、源码依赖缓存、本机 `.runtime`、DPAPI 配置或真实 `.env`。
- 最终进程使用专用非 root 用户；健康检查访问同源 `/api/v1/health`。
- 请求体限制同时检查 `Content-Length` 和实际接收字节，超限返回带 `X-Request-ID` 的统一 `413 request_too_large`，不回显请求正文。
- 未处理异常继续只记录 request ID 和异常类型，响应不暴露路径、堆栈、环境变量或提供方错误。
- AI 只读取后端进程中的 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`、`AI_TIMEOUT_SECONDS`；示例和镜像只声明变量名，不写入实际密钥。

## 3. 工作清单

- [x] 新增严格部署设置模型和单 worker 生产入口。
- [x] 让全局 FastAPI 应用从部署环境读取 SQLite、前端目录和请求体限制。
- [x] 实现有界 API 请求体中间件与统一 413 响应。
- [x] 实现安全的同源静态文件与 SPA 回退，隔离未知 API 路径。
- [x] 增加多阶段 `Dockerfile`、`.dockerignore`、非 root 用户、持久化卷和健康检查。
- [x] 扩展 `.env.example` 与 README 的本地容器/平台 Secret Manager 说明。
- [x] 增加配置、静态路由、API 隔离、请求上限、错误脱敏、持久化和容器契约测试。
- [x] 完成前后端回归、生产构建、真实单 origin HTTP、容器构建/运行和敏感信息检查。
- [x] 形成 M6-5 审查、更新持续进度并创建独立提交。

## 4. 验收场景

1. 干净构建后访问 `/` 能取得 React 页面，页面通过同一 origin 访问健康检查、REST 和 SSE，不依赖 `127.0.0.1` API 常量。
2. 前端路由返回 `index.html`；静态资产正确返回；未知 `/api/v1/...` 仍返回统一 JSON 404。
3. 小于等于上限的 JSON 请求正常进入 FastAPI；声明或实际请求体超过上限时返回 413，响应和日志不包含正文。
4. 使用临时持久化路径创建运行会话，重启单 worker 后仍能找回，并按既有规则把中断状态恢复为暂停。
5. 无 AI 环境变量时健康检查、页面、事件规则解析、方案解释和运行闭环可用；有 AI 时密钥只在后端环境注入。
6. 容器以非 root 用户、一个 Uvicorn worker启动，`/data` 可写，健康检查通过；镜像上下文不含 `.git`、`.runtime`、`.env`、缓存或密钥。

## 5. 禁止事项

- 不创建或推送 GitHub 远程，不选择托管商，不申请公网地址；这些属于 M7。
- 不把 Windows 一键启动改成生产容器入口，也不把 DPAPI 本机配置复制到容器。
- 不开放通配 CORS，不把 API key、base URL 或模型调用放入浏览器。
- 不启用多个 worker、多个副本、共享内存 SSE、PostgreSQL 或消息总线；这些需要新的状态架构阶段。
- 不接真实机场生产数据、个人信息或外部控制系统。
- 不提前实现 M6-5A 的机场空间态势、路径或空间 AI 问答。

## 6. 风险与应对

| 风险 | 应对 |
| --- | --- |
| SPA 回退吞掉 API 404 | API 路径显式排除，并加入 JSON 404 专项测试 |
| Chunked 请求绕过 `Content-Length` | 在调用 FastAPI 前有界读取实际请求消息并按字节计数 |
| SQLite 写入容器临时层 | 容器固定绝对路径 `/data/runtime-sessions.sqlite3` 并声明持久化卷 |
| 平台误启多 worker 或多副本 | 生产入口不暴露 worker 数，文档明确单实例边界，容器 CMD 固定一个 worker |
| 镜像泄露本机 AI 配置 | `.dockerignore` 排除 `.runtime`、`.env*` 和缓存；容器测试检查构建契约 |
| 静态缓存导致旧页面调用新 API | `index.html` 使用 `no-store`，摘要资产使用 immutable 缓存 |
