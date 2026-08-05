# M5-1 确定性中文事件草稿审查记录

## 1. 审查结论

M5-1 已完成。后端现在可以把有限中文航班延误或登机口变更描述解析成绑定权威场景 version 或运行 revision 的待人工复核草稿；字段不足时逐项追问，事件类型冲突或意图不支持时安全拒绝。

本单元完全不依赖语言模型，没有读取模型配置、发起外部网络调用或持久化原始输入。草稿固定不可自动应用，M4 的权威时钟、事件目录、SQLite 运行状态、CP-SAT、硬约束检查、当前/候选方案和人工确认链均未改变。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 实现范围

| 文件 | 作用 |
| --- | --- |
| `backend/app/ai_event_parser.py` | 纯确定性中文意图、航班、时间、延误分钟与新登机口解析 |
| `backend/app/ai_services.py` | 核对当前场景 version 或运行 revision，构造权威解析上下文与来源标记 |
| `backend/app/api.py` | 公开只读 `POST /api/v1/assistant/event-drafts` 与统一辅助错误语义 |
| `backend/app/main.py` | 注入单例辅助服务并登记 OpenAPI `assistant` 标签 |
| `tests/test_ai_event_parser.py` | 14 条完整、缺失、歧义、冲突和不支持解析样例 |
| `tests/test_api_ai_event_drafts.py` | 场景/运行上下文、错误映射、来源、原文不回显与无副作用测试 |

## 3. 确定性语义

- 事件类型只允许延误与登机口变更；一段文本同时包含两类事件时不拆分、不猜测，要求分别提交。
- 航班只在权威 `flight_id` 与 `display_code` 中匹配；没有唯一结果时返回航班选项追问。
- `HH:MM`、中文时刻和显式日期使用绑定上下文的日期/时区；“刚刚”“5 分钟前/后”等相对时间只按权威参考时间换算。场景时间窗外的结果不形成事件。
- 延误分钟只取与延误语义相邻的明确数字。登机口变更的新登机口必须属于权威选项且不同于当前登机口；原登机口只从场景航班或运行航班投影取得。
- 完整事件的每个必填字段都有与最终 Pydantic `FlightEvent` 一致的证据。用户原文只保留最小字段片段，不返回完整输入。

## 4. 上下文、来源与只读边界

- 场景请求先读取当前快照并比较 `expected_version`；历史或未来版本均返回 `409 assistant_version_conflict`，不在陈旧版本上生成看似有效的草稿。
- 运行请求通过现有权威快照核对 session/revision；参考时间来自 `clock.simulation_time`，当前登机口来自 `flights` 投影，前端不能自报这些事实。
- M5-1 的 `auto` 因未配置模型而返回 `source=deterministic_rules`、`fallback_reason=model_not_configured`；`deterministic_only` 不发起调用且不声明回退。
- 接口只构造内存响应。无副作用测试确认场景快照、场景审计、SQLite 运行记录与运行审计在请求前后完全一致。

## 5. Review 发现与修正

全量回归首次出现 1 项预期变更：OpenAPI 旧测试把标签集合固定为 M4 的六个标签。新增 `assistant` 标签后，该断言按新公开契约更新，并额外固定 `/api/v1/assistant/event-drafts` 必须存在；重跑后 165 项全部通过。

pytest 在受限沙箱内无法扫描系统临时目录，聚焦和全量测试改在获准的普通进程环境执行。该问题不涉及产品代码；测试产生的项目临时目录已清理。

## 6. 验证结果

| 检查 | 结果 |
| --- | --- |
| M5-1 解析与 API 聚焦 | `21 passed in 1.08s` |
| 后端全量回归 | `165 passed in 6.90s` |
| Python 编译 | `.venv\Scripts\python.exe -m compileall -q backend tests` 通过 |
| Python 依赖 | `.venv\Scripts\python.exe -m pip check`：No broken requirements found |
| 差异格式 | `git diff --check` 通过；仅有未修改静态 JSON 的既有 CRLF/LF 提示 |

## 7. 已知边界与下一入口

- 当前规则只覆盖有限中文表达，不把它描述成通用自然语言理解；更自由的措辞应进入 M5-2 模型适配器后再由同一 Pydantic 与实体校验链约束。
- 当前没有人工复核提交接口，草稿不能进入事件应用链；该闭环属于 M5-3。
- 当前没有方案事实构造与解释服务或前端辅助交互；它们分别属于 M5-4 与 M5-5。
- 不接真实机场生产数据、旅客个人信息或控制系统。

M5-2 是下一唯一入口：接入可替换的 OpenAI-compatible/DeepSeek 结构化输出适配器。密钥只允许从本地进程环境或忽略提交的本地配置读取；未配置、超时、提供方错误或非法输出必须回退 M5-1 规则，模型结果仍不能绕过 Pydantic、场景实体、version/revision 和人工确认。
