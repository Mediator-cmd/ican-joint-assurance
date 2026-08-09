# M6 规模与基准契约

本文件定义 M6 规模测试和性能证据的公共语义。它不公开新的生产 API，也不允许性能脚本改变运行会话或真实业务状态。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 1. 档位

`ScaleTier` 只能是：

| 值 | 任务/资源/区域 | 目标秒数 | 回退是否可作为通过结果 |
| --- | --- | ---: | --- |
| `small` | 100 / 20 / 5 | 2.0 | 否 |
| `medium` | 500 / 50 / 10 | 5.0 | 否 |
| `large_aggregate` | 2000 / 200 / 20 | 15.0 | 是 |

每个 `ScaleProfile` 固定 `data_classification=synthetic`。调用 `get_scale_profile()` 总是返回新的模型副本，调用方无法污染后续基准的规范档位。

## 2. 单次样本

`ScaleBenchmarkSample` 固定记录：档位、从 1 连续编号的轮次、完整规划耗时、实际执行路径、有限回退原因、计划状态、任务覆盖和硬约束违规数。

- `execution_path=cp_sat` 时不得携带回退原因。
- `execution_path=deterministic_fallback` 只在 `large_aggregate` 可接受，且必须是 `model_size_guard` 或 `time_limit`。
- 分配数和待协调数之和必须恰好等于规范任务数。
- `PlanStatus.INVALID` 与任何硬约束违规都不能成为性能证据；部分方案必须显式列出待协调任务。

## 3. 报告

`ScaleBenchmarkReport` 固定记录非敏感环境口径（`windows/linux/macos` 有限平台枚举、数字格式的 Python/OR-Tools 版本、逻辑 CPU 数、单 worker）、预热次数、有序样本和 p50/p95/最大值。

- 报告档位必须逐字段等于规范 `ScaleProfile`，所有样本必须属于同一档位且编号连续。
- p50 使用中位数；p95 使用有序样本的 nearest-rank 95%；最大值取样本最大耗时。三项都必须从样本重新计算，不能手写覆盖。
- `target_met` 只在最大值不超过该档位目标秒数时为真；`fallback_used` 必须与样本实际执行路径一致。
- 报告固定 `all_hard_constraints_valid=true` 和完整安全声明，未知字段、无时区生成时间、非规范档位、主机名等未定义字段均被拒绝。

## 4. 计时和报告边界

规划计时包含模型构建、求解、方案构造和独立硬约束复核；不包含场景工厂、JSON、网络、前端渲染或依赖冷启动。基准运行器不得查询外部网络、读取密钥、将原始事件文本写入报告，或向公开 API 暴露可被滥用的性能端点。

## 5. 后续实现约束

M6-1 只实现确定性合成场景工厂。M6-2 才实现规模保护和回退；它们必须保持 `Plan`、`validate_plan()`、CP-SAT 硬约束和人工确认的既有所有权。M6-3 才生成真实报告。任何实际性能数据在 M6-3 前均不存在，不能由此契约推断。

## 6. M6-1 冻结场景摘要

M6-1 使用固定 `2026-08-01` 教学日期、`simulation` 模式、`synthetic` 分类和空事件集生成规模场景。相同档位必须保持以下 SHA-256 内容摘要：

| 档位 | SHA-256 |
| --- | --- |
| `small` | `7dc3827de6a1d702f73a2383473008f04170ba3ed1491e1817bcab73bb0bcf11` |
| `medium` | `5775f0c58d2d64e47cca1a8879ae14077ea65236a66f18a15816ce244a73d649` |
| `large_aggregate` | `a5e0bae31aa7a91b70568df58326fe93a850ce26d3701234a39e14e620c9c70e` |

摘要只证明输入内容可复现，不是性能签名或生产数据证明。任何有意修改生成规律都必须同步更新专项测试、审查记录和摘要版本；不得静默改变基准输入。

## 7. M6-2 有界规划契约

规模规划入口固定先计算 `ScaleModelEstimate`，不得在 guard 之后才构造超限 CP-SAT 模型。当前限制为 500 项任务、1,250,000 个旧全量排序对和每项任务最多 3 个有界候选资源。

- `small` 和 `medium` 的规范估算不触发 guard，只允许 `execution_path=cp_sat` 和 `bounded_scale_cp_sat_v1`，不得回退。
- `large_aggregate` 在建模前触发 guard，只允许公开 `execution_path=deterministic_fallback`、`fallback_reason=model_size_guard` 和 `deterministic_scale_fallback_v1`。
- `time_limit` 只表示未触发 guard 的大型求解器返回 `UNKNOWN`；模型无效、不可行、代码错误或硬约束失败不得标记为超时。
- 每个结果必须绑定规范档位、M6-1 冻结指纹、规范场景 ID/version、实际执行路径、有限回退原因和完整 `Plan`，并固定需要人工确认和安全声明。
- 返回结果必须覆盖全部任务，`PlanStatus.INVALID`、非零硬约束违规、指标/列表不一致、场景身份错配或静默回退一律拒绝。

本契约仍不是性能结论。M6-3 必须从独立计时样本证明实际时间目标，不能把 M6-2 的功能测试时间当作 p50/p95/最大值。
