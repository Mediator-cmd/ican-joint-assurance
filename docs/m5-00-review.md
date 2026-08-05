# M5-0 AI 辅助契约审查记录

## 1. 审查结论

M5-0 已完成。自然语言事件草稿、字段依据、缺失追问、有限调度目标建议、方案解释、来源透明度和无模型回退已经形成严格 Pydantic 契约，并通过专项与全量回归。

本单元只冻结 M5 的安全边界和数据结构，没有公开新路由、调用外部模型、读取密钥、修改事件/规划逻辑或改造前端。M4 的场景版本、运行 revision、权威时间、CP-SAT、独立硬约束检查、当前/候选方案和人工确认保持不变。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 已冻结契约

### 2.1 事件草稿

- 请求必须绑定场景 `expected_version` 和带时区参考时间，或绑定运行 session 与 `expected_revision`。
- `auto` 允许后续尝试模型并安全回退；`deterministic_only` 明确禁止外部调用。
- 响应只允许 `ready_for_review`、`needs_clarification`、`unsupported` 三种互斥状态。
- 完整延误事件必须有事件类型、航班、时间和延误分钟依据；登机口变更必须另有原/新登机口依据。
- 每项依据记录规范化值和来源；最终事件与证据不一致时模型校验失败。
- 草稿固定 `requires_human_confirmation=true`、`applies_automatically=false`。

### 2.2 来源与回退

- 语言模型结果必须对应一次真实提供方尝试并显示非敏感模型标签。
- 确定性结果不能冒充模型；超时、提供方错误和非法输出回退必须对应真实调用尝试。
- 未配置模型不能声称已经调用；公开回退原因使用有限枚举，不泄露原始异常或配置。

### 2.3 目标与解释

- 目标建议只允许均衡、关键任务优先、最小等待、最小变更四个有限枚举。
- 目标建议固定要求人工确认，且 `applied_to_planner=false`；当前只有 `balanced` 对应既有确定性基线。
- 方案解释绑定不可变场景版本，或绑定运行 session、revision 和具体方案 ID。
- 后端证据使用稳定 evidence ID；摘要、每项取舍和下一步均为独立 claim，每条 claim 至少引用一个响应中存在的 evidence ID。
- 解释固定 `modifies_plan=false`、`requires_human_confirmation=true`。

## 3. Review 发现与修正

首版解释响应使用全局 `cited_evidence_ids`。它只能证明整份响应引用过某项事实，不能证明每一条摘要、取舍或建议都有依据。审查后改为 `ExplanationClaim`：每条 claim 自带非空 evidence ID 列表，响应级校验确保所有引用都存在，重复或未知引用均被拒绝。

同时补充了登机口变更完整证据测试、未知请求字段拒绝和安全声明不可覆盖测试，防止后续路由接受 `execute_immediately` 一类越权字段或返回弱化的生产控制语义。

## 4. 代码与文档

| 文件 | 作用 |
| --- | --- |
| `backend/app/ai_models.py` | M5 公共请求/响应、来源、回退、事件依据、追问、目标与解释契约 |
| `tests/test_ai_contracts.py` | 判别上下文、状态互斥、证据一致性、回退、人工确认和安全声明测试 |
| `docs/m5-ai-assistance-plan.md` | M5-0 至 M5-6 顺序、状态所有权、非目标和退出条件 |
| `docs/m5-ai-contract.md` | M5-0 字段、路径草案、错误、回退和解释语义 |

## 5. 验证结果

| 检查 | 结果 |
| --- | --- |
| M5-0 契约专项 | `11 passed in 0.40s` |
| 后端全量回归 | `144 passed in 7.18s` |
| Python 编译 | `.venv\Scripts\python.exe -m compileall -q backend tests` 通过 |
| Python 依赖 | `.venv\Scripts\python.exe -m pip check`：No broken requirements found |
| 差异格式 | `git diff --check` 通过；仅有未修改静态 JSON 的既有 CRLF/LF 提示 |

## 6. 已知边界

- 当前没有自然语言解析服务或 API，不能把契约模型描述为已具备 AI 事件录入。
- 当前没有模型提供方、提示词、外部网络调用或密钥配置。
- 四种目标中只有 `balanced` 对应现有优化器行为，其他目标必须在 M5-3 由确定性算法实现和测试后才能使用。
- 方案解释目前只有契约，没有事实构造器或解释服务。
- M5 仍只处理匿名化、历史回放或合成教学数据，不接真实机场生产流或个人信息。

## 7. 下一入口

M5-1 是下一唯一入口：实现不依赖模型的确定性中文规则解析器，覆盖至少 10 条延误、登机口变更、相对/绝对时间、缺失字段、歧义和不支持输入样例；服务只返回事件草稿或追问，不应用事件。
