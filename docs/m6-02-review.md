# M6-2 有界规模规划与安全回退审查

## 1. 结论

M6-2 已完成。项目新增离线 `build_bounded_scale_plan()` 入口，小型和中型规范场景使用有界 CP-SAT，大型聚合场景在创建 `CpModel` 前触发固定规模保护，并返回带 `model_size_guard` 原因的确定性安全回退方案。

三个档位的功能验证均返回 `PlanStatus.EXECUTABLE`，分别完整覆盖 100/500/2000 项任务，独立 `validate_plan()` 的硬约束违规均为 0。该结论只证明规划路径和结果契约正确；M6-3 前没有正式预热、样本、p50/p95/最大值或 2/5/15 秒性能结论。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 建模前规模保护

`estimate_scale_model()` 只读取已经验证的规范 `ScaleScenarioArtifact`，在创建 CP-SAT 模型前计算兼容任务-资源对、旧全量两两排序对、候选裁剪后对数和最大区域行程。

| 档位 | 兼容对 | 有界候选对 | 旧两两排序对 | 最大行程 | guard |
| --- | ---: | ---: | ---: | ---: | --- |
| `small` | 750 | 300 | 15,250 | 6 分钟 | 否 |
| `medium` | 9,375 | 1,500 | 971,875 | 15 分钟 | 否 |
| `large_aggregate` | 150,000 | 6,000 | 62,425,000 | 30 分钟 | 是 |

固定策略为：最多 500 项任务、最多 1,250,000 个旧排序对、每项任务最多 3 个候选资源。`ScaleModelEstimate` 重新推导 `guard_triggered`，调用方不能手写覆盖；候选对数也不能超过兼容对或每任务上限。

大型场景同时超过任务和旧排序对限制，因此在 `_build_bounded_cp_sat_plan()` 调用前回退。专项测试将建模函数替换为失败哨兵，证明大型路径不会先建大模型再降级。

## 3. 有界 CP-SAT

小型和中型不再沿用旧优化器的“同一资源上全部任务对两两建立先后变量”。新规模路径采用：

- 每项任务按初始最短行程与资源 ID 固定选择最多 3 个类型/容量/状态匹配的候选；
- 每个候选使用可选区间，同一资源通过 `AddNoOverlap` 约束，变量和区间数量与裁剪后候选对近似线性增长；
- 每个服务区间预留全场最大行程缓冲，求解后按实际前序终点重新计算最短路，保证真实移动可在服务开始前完成；
- 单 worker、固定随机种子和有限求解时间保持可复现；有限加权目标按关键任务、总任务、优先级、等待和资源稳定次序排列；
- 确定性安全起始解只作为 CP-SAT hint，解决中型对称候选从弱空白解起步的问题，不直接作为返回方案；最终资源和开始时间仍读取求解器结果。

方案返回前按真实移动时间构造现有 `Assignment`，再调用独立 `validate_plan()`。任何约束违规、`INFEASIBLE` 或 `MODEL_INVALID` 都直接失败，不能被伪装为超时回退。只有求解器返回 `UNKNOWN` 才具有 `time_limit` 语义；当前规范大型档位会更早命中 `model_size_guard`。

## 4. 显式确定性回退

大型回退复用现有确定性 FIFO 核心，并在独立复核通过后将计划明确标记为：

- `execution_path=deterministic_fallback`；
- `fallback_reason=model_size_guard`；
- `plan.algorithm=deterministic_scale_fallback_v1`；
- `requires_human_confirmation=true`；
- 完整安全声明、实际分配/待协调指标和零硬约束违规。

小型和中型不允许回退。其 CP-SAT 超时会抛出 `ScalePlanningError`，不会调用确定性回退或把失败样本包装为通过结果。

## 5. 结果证据契约

`ScalePlanningResult` 严格绑定规范 `ScaleProfile`、M6-1 冻结场景指纹、规范场景 ID/version、模型估算、实际执行路径、有限回退原因和现有 `Plan`。模型拒绝：

- 档位与冻结指纹或计划场景身份不一致；
- 指标计数与实际 assignment/unassigned 列表不一致；
- guard 已触发却声称 CP-SAT，或未触发却声称 `model_size_guard`；
- 当前大型 guard 场景声称 `time_limit`；
- 小型/中型回退、隐藏回退原因、非法算法名、`PlanStatus.INVALID`、非零违规或关闭人工确认。

M6-1 的 `ScaleScenarioArtifact` 同步收紧：即使篡改内容后重新计算摘要，只要不等于该档位冻结 SHA-256，仍会被拒绝，避免 M6-3 静默替换基准输入。

## 6. 验证与阶段边界

M6-0 至 M6-2 聚焦测试为 `28 passed in 6.43s`，覆盖三档精确模型估算、小/中完整 CP-SAT、大型建模前 guard、完整安全回退、相同小型输入确定性、小/中禁止回退、严格时间上限、指纹/场景身份/人工确认和篡改拒绝。

| 检查 | 结果 |
| --- | --- |
| 后端全量回归 | `242 passed in 16.56s` |
| 前端回归 | 3 个测试文件，`24 passed` |
| TypeScript 与生产构建 | `npm run typecheck` 通过；Vite 8.1.5 构建 1791 个模块，CSS 64.45 kB、JS 302.14 kB |
| Python 环境 | `compileall` 通过；`pip check` 返回 `No broken requirements found.` |
| 质量与敏感信息 | PowerShell 语法、新代码纯 ASCII、`git diff --check` 通过；工作区（排除 `.runtime`）key-like 文件计数为 0 |

聚焦测试耗时包含 Python 启动、三档场景生成、多次功能求解和断言，不符合 M6 计时口径，不能用作性能证据。本单元未修改现有 `build_optimized_plan()`、公开 API、实时滚动规划、SQLite、SSE、前端或 AI 链路。

下一唯一入口为 M6-3：实现离线基准运行器，在相同冻结场景上执行预热与多个有序样本，比较 FIFO/有界规划，并从实际样本生成 p50、nearest-rank p95、最大值、执行路径和回退状态。M6-3 前不得发布性能达标结论。
