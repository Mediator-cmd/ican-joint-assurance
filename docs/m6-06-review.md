# M6-6 规模、体验与部署总验收审查

## 1. 结论

M6-6 已完成。M6-0 至 M6-5A 的规模、性能、单 origin、AI 只读、空间态势和多视口能力完成统一复核；M6 保持匿名教学仿真定位，不接真实机场生产数据，不创建 GitHub 远程，不发布公网地址。M7 仍需用户单独授权。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 自动化与性能证据

- 项目 `.venv` 后端全量回归：`306 passed in 33.84s`。
- M6-6 聚焦回归：空间连续验收、空间问答、规模基准和部署契约 `36 passed in 16.32s`。
- 前端：8 个测试文件、`42 passed`；`npm run typecheck` 通过；Vite 生产构建通过，1797 个模块，CSS 91.34 kB（gzip 17.57 kB），JS 341.40 kB（gzip 98.92 kB）。
- Python `compileall` 通过；`pip check` 返回 `No broken requirements found.`；PowerShell 的启动、停止和 AI 配置脚本均无解析错误；敏感信息扫描未发现 Git 跟踪文件中的 key-like 值；`git diff --check` 通过。
- 正式 `docs/m6-benchmark-report.json` 通过严格 M6-3 契约。另用 `warmup=0、measured=1` 临时样本复跑三档：小型 `1.024124s`、中型 `3.122831s`、大型聚合 `2.885840s`，全部达标、硬约束违规为 0；小/中型路径为 `cp_sat`，大型为 `deterministic_fallback + model_size_guard`。临时文件已删除，正式报告未被覆盖。

## 3. 启停、恢复与模型边界

- 标准停止脚本按身份精确停止前端及后端进程；重复停止安全返回无服务；重新启动后后端健康接口 `200`、前端入口 `200`，重复启动复用同一服务组，不产生第二组进程。
- 正式 SQLite 会话 `RUN-2639C8D8CCC848A6` 在服务重启后仍为 `V7 / R66 / 09:30 / completed`。已有 M6-5A-3 证据覆盖六事件连续处理、候选采用/保留、旧 revision `409`、SSE 转 REST 恢复和完成后不回绕。
- 当前真实 DeepSeek 只读冒烟返回 `trace.source=language_model`、`model_label=deepseek-v4-flash`，问题 `task4现在在哪里，由哪个资源执行？` 仅引用 `SPATIAL-FACT-TASK-004-ACTIVE`，聚焦 `TASK-004 / WC-01`。调用前后 revision `66`、状态 `completed`、场景版本 `7` 均不变；响应固定 `modifies_runtime=false`、`requires_human_confirmation=true`。
- 无模型、提供方超时、错误和非法结构的确定性回退由既有 M5/M6 自动化覆盖；AI 不应用事件、不采用候选、不推进时钟、不控制资源。

## 4. 浏览器与资源态势

- 调度总览桌面 `1440x1000`：显示 `10/10` 任务、`5/5` 资源、`6/6` 事件和完整安全声明；空间态势来自当前 `R66` 权威快照，候选路线为真实变化集合。
- 资源态势桌面 `1440x1000`、中等 `1024x768`、移动 `390x844` 均无横向溢出；页面宽度分别为 `1440/1440`、`1024/1024`、`390/390`。资源清单、详情、执行衔接和保障区域均显示，详情与执行衔接保持正常间距。
- 任务、事件和方案依据桌面 `1440x1000` 以及方案依据移动 `390x844` 均无文档级横向溢出。正常在线控制台只有 React DevTools 信息提示，没有应用 error/warning。
- 本轮截图保存在忽略目录 `output/playwright/`：`m6-6-overview-desktop-1440x1000.png`、`m6-6-resources-1024x768.png`、`m6-6-resources-390x844.png`。

## 5. 部署与环境限制

- Dockerfile、`.dockerignore`、运行依赖和单 origin 配置契约通过现有 `tests/test_deployment.py`；此前 M6-5 独立容器证据已证明 Linux 构建、非 root UID 10001、`/data` 持久化、健康检查、容器替换恢复和无模型启动。
- 本轮 Docker CLI 可用，但 Docker Desktop Linux daemon 未运行，因此没有重新构建或启动容器；该项记录为环境阻塞，不把静态契约当作本轮真实容器冒烟。M6-5 的已验证容器证据仍然有效。
- 本机 `.runtime/ai-config.json`、SQLite 会话、服务状态和日志保持在忽略目录，未读取密钥明文，未提交任何本机配置或缓存。

## 6. M6 退出条件

| 条件 | 结论 |
| --- | --- |
| 固定规模、性能报告和安全回退 | 通过 |
| M4/M5/M6 后端与前端回归 | 通过 |
| 故障、SQLite 重启、SSE/REST 恢复和陈旧 revision | 通过 |
| 无模型与真实模型只读边界 | 通过 |
| 五页、多视口和空间态势 | 通过 |
| 单 origin、单 worker、非 root、持久化和秘密隔离 | 既有容器证据通过；本轮真实 Docker 复验受 daemon 环境限制 |
| M6 总验收记录与下一入口 | 通过 |

M6 已收口。下一阶段为 M7 的 GitHub 远程、发布准备和公网地址规划，必须在用户明确授权后执行；真实机场生产数据、生产控制系统和未经授权的机场底图仍不在范围内。

