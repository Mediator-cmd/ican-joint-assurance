# M5-2 OpenAI-compatible 模型适配审查记录

## 1. 审查结论

M5-2 已完成。事件草稿 `auto` 模式现在可以在完整进程环境配置存在时调用可替换的 OpenAI-compatible chat-completions 提供方；模型不可用或输出不安全时无条件回退 M5-1 确定性规则。DeepSeek 是首个获准提供方，但提供方差异只停留在环境配置，业务层不依赖专用 SDK。

模型只做字段抽取，不生成事件 ID、规划方案或状态变更。所有成功结果仍经过原文证据、权威实体、确定性时间和 Pydantic 复核，响应固定要求人工确认且不能自动应用。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 实现范围

| 文件 | 作用 |
| --- | --- |
| `backend/app/ai_provider.py` | 安全环境配置、提供方协议、OpenAI-compatible 请求、有限模型抽取契约与错误分类 |
| `backend/app/ai_model_parser.py` | 将模型抽取重新绑定到原文、权威航班/登机口和确定性时间，构造 M5-0 草稿 |
| `backend/app/ai_services.py` | `auto` 模型尝试、成功来源和四类规则回退；`deterministic_only` 零调用 |
| `backend/app/main.py` | 正式模块级应用从进程环境构造可选提供方；测试工厂默认无模型并可注入假提供方 |
| `tests/test_ai_provider.py` | 环境、密钥遮蔽、最小请求、结构化响应、超时、HTTP 错误和非法输出测试 |
| `tests/test_api_ai_provider.py` | 模型成功、追问、零调用、回退、未知实体和语义冲突 API 测试 |

## 3. 配置与数据最小化

- 只有 `AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL` 同时有效时才启用提供方；`AI_TIMEOUT_SECONDS` 可选，默认 8 秒且限制为 0.1 至 30 秒。
- API key 使用 `SecretStr`，对象表示不显示明文。Base URL 只允许无内嵌凭据、query 或 fragment 的绝对 HTTP(S) 地址；模型标签限制长度和字符集。
- 适配器直接使用项目已有 `httpx2`，不新增专用 SDK。请求关闭流式输出、固定零温度和 JSON object 响应格式，不实现自动重试，避免同一文本产生隐式多次外发。
- 外发 user payload 只包含当前文本、参考时间/窗口、匿名航班 ID/展示号/当前登机口和登机口选项。场景 ID、运行会话、revision、计划、任务、资源、审计、数据库和本机路径都不外发。

## 4. 模型输出复核

- 模型 JSON 只能包含九个固定字段；额外字段、Markdown、空 choices、超大响应、值/quote 不成对或事件载荷互相冲突均判为非法输出。
- 每个 quote 必须是输入文本中的连续片段；flight ID 与 new gate ID 必须属于请求绑定的权威上下文，原登机口永远由后端事实派生。
- 文本明确出现多个航班时，模型不能选择其中一个；已知延误/登机口关键词不能被模型反向分类；阿拉伯数字分钟和明确登机口引用必须与规范化值一致。
- 模型只提取时间片段，最终时间仍由 M5-1 解析器结合权威 reference time 和场景窗口计算。模型无法提供可确定复核的时间时回退或追问，不信任自由生成时间戳。
- 完整结果最终进入 `EventDraftResponse`，继续由 M5-0 校验证据与 `FlightEvent` 一致，固定 `requires_human_confirmation=true`、`applies_automatically=false`。

## 5. 故障与回退

| 情况 | 是否尝试提供方 | 公开来源 | 回退原因 |
| --- | --- | --- | --- |
| 环境未完整配置 | 否 | `deterministic_rules` | `model_not_configured` |
| `deterministic_only` | 否 | `deterministic_rules` | 无 |
| 超时 | 是 | `deterministic_rules` | `model_timeout` |
| HTTP/传输失败 | 是 | `deterministic_rules` | `provider_error` |
| JSON、契约、原文或实体复核失败 | 是 | `deterministic_rules` | `invalid_model_output` |
| 模型抽取通过全部复核 | 是 | `language_model` | 无 |

提供方原始异常、HTTP 状态、响应正文、Base URL、密钥和内部提示词均不进入 API 响应或日志。模型故障不影响 M4 健康、运行会话、SSE、事件应用、规划和候选人工确认。

## 6. Review 发现与修正

首版已经验证 JSON 结构、quote 属于原文和实体 ID 属于权威列表，但“结构合法”仍不足以证明语义一致。例如模型可能把明确的“延误”反向标为登机口变更，或把原文“20 分钟”规范化为 30。审查后新增已知意图一致性、阿拉伯数字分钟一致性、明确登机口一致性和多航班禁止猜测检查；失败统一进入 `invalid_model_output` 并由规则重新解析。

测试应用与正式应用的配置入口也刻意分离：`create_app()` 默认无提供方，使全量回归不受开发机环境变量影响；Uvicorn 使用的模块级 `app` 才读取进程环境。测试通过显式假提供方覆盖所有模型路径，不需要真实网络或密钥。

## 7. 验证结果

| 检查 | 结果 |
| --- | --- |
| M5-2 提供方与 API 专项 | `19 passed in 1.05s` |
| M5-0 至 M5-2 聚焦 | `51 passed in 1.54s` |
| 后端全量回归 | `184 passed in 6.99s` |
| Python 编译 | `.venv\Scripts\python.exe -m compileall -q backend tests` 通过 |
| Python 依赖 | `.venv\Scripts\python.exe -m pip check`：No broken requirements found |
| 差异格式 | `git diff --check` 通过；仅有未修改静态 JSON 的既有 CRLF/LF 提示 |
| 密钥模式扫描 | 仓库中未发现 32 位十六进制 `sk-...` 密钥模式 |

## 8. 已知边界与下一入口

- 当前进程环境未配置 `AI_*`，本轮没有发起真实 DeepSeek 请求；真实提供方可用性不是自动化回归前提。用户提供的密钥没有持久化。
- 当前前端没有自然语言输入和草稿复核界面；该交互属于 M5-5。
- 当前草稿没有提交入口，不能自动或手动进入事件目录；人工复核提交与有限调度目标属于 M5-3。
- 当前没有方案事实解释服务；它属于 M5-4。
- 不接真实机场生产数据、个人信息或外部控制系统。

M5-3 是下一唯一入口：实现人审事件草稿提交与有限确定性目标配置。模型只能提出草稿或枚举建议；提交仍必须绑定最新 version/revision，并继续经过现有事件应用、CP-SAT、独立硬约束和候选人工确认链。
