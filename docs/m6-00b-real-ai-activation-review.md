# M6-0B 真实 AI 激活审查

## 1. 结论

M6-0B 已完成：项目后端已经通过 OpenAI-compatible HTTP 适配器实际调用 DeepSeek，事件草稿和方案事实解释均完成真实模型冒烟。当前真实配置只服务于本机匿名合成教学场景；没有接入真实机场生产数据、个人信息或外部控制系统。

模型只增强两类表达：从用户输入中抽取待人工复核的事件字段，以及根据后端已经冻结的方案事实生成带 `FACT-*` 引用的解释。事件提交、调度目标、航班/登机口实体、时间事实、硬约束、候选采用和当前方案状态仍由后端确定性链路拥有；模型不能自动写入业务状态，也不能越过人工确认。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 实际激活链路

- `配置AI.cmd` 调用 `scripts/configure-ai.ps1`，以隐藏输入接收 API key、OpenAI-compatible base URL 和模型名。
- API key 使用当前 Windows 用户 DPAPI 保护后写入忽略提交的 `.runtime/ai-config.json`。该文件不在 Git 中，前端、状态 API、日志和响应不读取明文。
- `scripts/start-project.ps1` 启动后端子进程时短暂注入 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL`，随后恢复启动脚本父进程环境；运行状态只保存配置文件指纹。配置指纹变化会使旧服务组重启，避免复用未配置模型的后端。
- GitHub 或公网部署使用平台 Secret Manager 或进程环境变量；本机 DPAPI 文件不是部署方式。未创建 GitHub 远程、未发布公网地址。

默认模型为 `deepseek-v4-flash`，可在配置入口修改。适配器仍保持提供方无关，不引入 DeepSeek 专用 SDK。

## 3. 兼容性修复

真实请求发现并修复了两项兼容问题：

1. DeepSeek V4 默认启用 thinking。短结构化请求会把 `max_tokens` 消耗在 `reasoning_content`，导致最终 `content` 为空；对 DeepSeek 主机或 `deepseek-*` 模型显式发送 `thinking: {"type":"disabled"}`。
2. 原响应模型把业务严格模型错误地复用于 OpenAI 响应信封，拒绝了合法的 `id`、`usage`、`role`、`finish_reason` 等元数据；现在信封只读取所需字段并忽略标准元数据，业务输出模型仍保持 `extra="forbid"`。

## 4. 真实调用证据

所有输入均为匿名合成教学数据，场景参考日期为 `2026-08-01`。

| 检查 | 结果 |
| --- | --- |
| 事件抽取请求 | HTTP 200；输入为 `SIM102 在 08:12 确认延误 20 分钟` |
| 事件抽取追踪 | `trace.source=language_model`、`trace.provider_attempted=true`、`trace.model_label=deepseek-v4-flash`、`trace.fallback_reason=null` |
| 事件草稿 | `status=ready_for_review`；类型为航班延误，航班 `FL-SIM102`，延误 20 分钟 |
| 方案解释请求 | HTTP 200；方案 `PLAN-TERMINAL-DISTURBANCE-01-V1-CP-SAT` |
| 方案解释追踪 | `trace.source=language_model`、模型为 `deepseek-v4-flash`，返回 20 条权威证据和 8 个合法 `FACT-*` 引用 |
| 方案解释安全边界 | `modifies_plan=false`、`requires_human_confirmation=true`；引用全部通过后端事实校验 |
| 浏览器页面 | 显示“模型辅助解释 · deepseek-v4-flash”，摘要、取舍、建议均带 `FACT-*`，并显示“仍需人工确认” |
| 浏览器控制台 | 0 error、0 warning；仅有 Vite/React 开发信息 |
| 后端全量回归 | `219 passed in 10.17s` |
| 前端回归 | 3 个测试文件，`24 passed` |
| TypeScript 与生产构建 | `npm run typecheck` 通过；Vite 8.1.5 构建 1791 个模块，CSS 64.45 kB、JS 302.14 kB |
| Python 环境 | `compileall` 通过；`pip check` 返回 `No broken requirements found.` |
| 启动脚本与差异 | 0 个 PowerShell 语法错误、0 个 PowerShell 非 ASCII 字符、`git diff --check` 通过 |
| 秘密边界 | 工作区（排除 `.runtime`）key-like 文件计数为 0；`.runtime/ai-config.json` 未被跟踪 |

真实模型结果只保存在当前后端会话内存中，不作为静态演示数据写回仓库。

## 5. 回退与安全验证

- 未配置模型、超时、提供方错误、非 JSON 或契约非法输出仍回退确定性规则，并公开回退来源与原因。
- 原文 quote、匿名实体、时间窗、事件类型、四选一调度目标、version/revision、硬约束和人工确认继续由后端校验。
- 模型输出中的未知实体、非法 `FACT-*` 引用或“立即执行/自动采用”等越权文本不能改变候选、当前方案或运行状态。
- 前端只调用同源 `/api/v1`，不包含 API key、提供方 URL、模型名或专用 SDK。

## 6. 阶段状态

M6-0B 已收口。M6-1 仍未开始，下一入口是确定性规模场景工厂与 100/500/2000 档位的数据完整性测试；不得在 M6-1 之前发布性能结论、接入真实机场生产流、创建 GitHub 远程或部署公网。
