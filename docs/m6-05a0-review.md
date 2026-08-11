# M6-5A-0 空间契约与后端投影审查

## 1. 审查结论

M6-5A-0 已完成。默认匿名航站楼场景现在具有严格、可公开部署的本地 SVG 空间布局，以及绑定 `session_id + expected_revision` 的只读空间态势接口。每个运行任务、资源和事件必须在同一修订下得到唯一投影，任一对象缺失、重复或引用非法区域/路径都会使响应校验失败。

本增量没有创建地图专用时钟、随机动画、循环事件、第二规划器或新的 SQLite 状态。前端地图与空间 AI 尚未在本增量实现，分别属于 M6-5A-1 和 M6-5A-2。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 契约与布局

- 新增 `SpatialLayout`、`RuntimeSpatialView`、任务路线、资源标记、事件标记、覆盖率和 `SPATIAL-FACT-*` 严格模型。
- 坐标固定为 0-1 规范化笛卡尔坐标；几何路径只用于展示，未替代场景 `travel_minutes`、任务时窗或硬约束。
- 原创底图为 `frontend/public/assets/anonymous-hub-layout.svg`，分类为 `anonymous_training_simulation`，许可证为 `project-original`，SHA-256 为 `0512a7f8e9001e0c987519df948d37c8dfb85348f87672b64eafc207f638bd44`。
- `.gitattributes` 固定 SVG 使用 LF，避免 Windows、GitHub 与 Linux 容器检出时因行尾变化导致资产摘要漂移。
- 第一版只登记 `SCN-TERMINAL-DISTURBANCE-01` 的 `TRANSFER-DESK`、`GATE-W03`、`GATE-E01`。其他场景返回 `spatial_layout_not_available`，不随机摆放或借助 AI 猜测。
- 东西登机口之间通过中转服务台执行确定性图遍历；路径方向、连续性、起终区域、布局端点和 normalized 坐标均由模型校验。

## 3. 一一对应与状态所有权

- `GET /api/v1/runtime-sessions/{session_id}/spatial?expected_revision=N` 调用现有 `get_explanation_context()`，一次取得同一修订的快照与 `RuntimeProjectionSource`。
- 默认场景每个修订必须投影 10 条当前任务路线、5 个资源位置和 6 个一次性事件标记；未分配任务也作为“待协调需求”保留，不能从地图消失。
- 资源显示位置由后端沿登记路径按权威 `from_zone_id / to_zone_id / progress_pct` 插值。暂停或断线不会在浏览器自行推进。
- 当前方案永远保留一条 `active` 路线；候选只为相对当前方案在分配、资源、路线或时序上真实变化的任务增加 `candidate` 路线。
- 首个延误事件虽产生候选 ID，但其方案空间/时序事实与当前方案相同，因此候选路线数为 0；系统没有为了视觉效果伪造虚线。第二个登机口变更产生真实路线变化，候选路线与变化任务集合严格相等。
- 空间投影是即时派生的只读视图，不写入 SQLite，不修改 revision，不应用事件，不采用候选，也不改变现有 SSE/REST 恢复语义。

## 4. API 与证据

- 陈旧 `expected_revision` 复用现有 `runtime_revision_conflict` 409 语义。
- 空间事实包含会话/修订/仿真时间、完整覆盖率、每条当前任务路线、实际候选变化、资源位置和事件定位；事实 ID 全部以 `SPATIAL-FACT-*` 开头。
- `requires_human_confirmation=true`、`modifies_runtime=false` 和完整安全声明是响应契约常量，模型或前端不能覆盖。
- 新 `spatial` OpenAPI 标签已加入公开契约；原有错误响应和 `/api` 路由边界保持不变。

## 5. 验证证据

- M6-5A-0 专项：`6 passed in 1.07s`，覆盖底图摘要、图遍历、非法资产/端点、未知布局、10/5/6 覆盖、旧 revision、无变化候选和真实登机口变更候选。
- 后端全量：加强路线连续性契约后的最终结果为 `290 passed in 34.34s`。
- 前端既有回归：6 个测试文件、`33 passed`；`npm run typecheck` 与 Vite 生产构建通过，原创 SVG 正常进入发布产物。
- Python：`compileall -q backend tests scripts` 通过；`pip check` 返回 `No broken requirements found.`。
- Git：`git diff --check` 通过。
- pytest 在受限 Windows 沙箱内创建 `tmp_path` 时仍会遇到已知 ACL 限制；专项和全量测试均在获准的普通进程环境使用仓库内独立 `--basetemp` 完成，业务测试全部通过。

## 6. 下一入口

下一步进入 M6-5A-1：在调度总览渲染响应式 2D SVG 态势图，绑定当前 snapshot revision、SSE/REST 更新和现有任务/资源/事件选择。当前方案使用实线，候选使用虚线；前端不得计算业务状态、推进资源、随机移动或创建第二份运行快照。
