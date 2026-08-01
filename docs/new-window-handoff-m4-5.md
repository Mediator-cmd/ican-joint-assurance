# 新窗口无遗漏交接提示词（M4-5 前置）

将下面整段提示词发送给新的 Codex 窗口。当前用户要求是“先完成接管核验，不要立即进入 M4-5”；只有用户随后明确说“开始 M4-5”或同义指令，才允许修改业务代码。

---

你现在接管本地项目“联保智调”。这是一个参加 2026 iCAN 交通出行方向竞赛的机场地面特殊旅客保障资源调度教学仿真项目。请严格按以下事实和顺序接管，不要依赖聊天记忆猜测项目状态。

## 一、仓库与当前基线

- 仓库绝对路径：`E:\ican`
- 当前分支：`main`
- 当前业务基线：`fc01cba feat: implement M4-4 runtime streaming`
- `fc01cba` 已独立提交 M4-4；交接文档可能位于其后的纯文档提交，因此实际 HEAD 必须用 Git 复核。
- M4-0 至 M4-4 已完成；M4-5 尚未开始，没有半成品代码需要继承。
- 本仓库不存在 `docs/gpt_project_completed_summary.md`。不要从 `E:\空管AI作品\SkyMind-ATC` 或其他仓库带入源码结构、阶段编号或结论。

## 二、接管后的第一轮操作

先只读检查并向我复述结论，不要修改文件，不要启动 M4-5：

1. `Set-Location -LiteralPath 'E:\ican'`
2. `git status --short`、`git log -5 --oneline`、`git diff --check`
3. 以 UTF-8 完整阅读 `docs/project-progress.md`，它是持续交接的权威入口。
4. 阅读 `docs/project-plan.md`、`docs/m4-realtime-operations-plan.md`、`docs/m4-runtime-contract.md`。
5. 阅读 `docs/m4-04-review.md` 和 `docs/m4-04-plan.md`，核对 M4-4 实现与验收边界。
6. 再检查 `frontend/src/App.tsx`、`frontend/src/api.ts`、`frontend/src/types.ts`、`frontend/src/styles.css`、`frontend/vite.config.ts`。
7. 检查 `backend/app/runtime_models.py`、`runtime_stream_models.py`、`runtime_services.py`、`runtime_stream.py`、`api.py`、`main.py`。
8. 检查根目录 `启动联保智调.cmd`、`停止联保智调.cmd` 及 `scripts/start-project.ps1`、`scripts/stop-project.ps1`。
9. 如 Git 状态与本提示不一致，先区分用户改动和已提交事实；不得 reset、checkout 或覆盖未知改动。

完成上述检查后，先报告：实际 HEAD、工作区是否干净、M4-4 是否与文档一致、M4-5 的实施边界、准备先改哪些文件。未经我确认，不要开始代码修改。

## 三、产品定位与不可改变的边界

- 产品名称：联保智调。
- 场景：航班延误、登机口变更等扰动下，对摆渡车、轮椅和服务人员进行特殊旅客地面保障调度。
- 核心差异：确定性调度、硬约束复核、人工确认和完整证据链优先；AI 只做输入理解与结果解释，不能成为聊天套壳或绕过约束直接排班。
- 数据只允许合成教学数据或匿名化回放，不保存旅客姓名、证件号、联系方式等个人信息。
- 不接管真实机场生产系统，不输出真实放行、登机、改签或车辆控制指令。
- 所有运行界面、响应、报告和文档必须完整保留：`仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。`
- “实时”是后端权威、可复现的教学仿真实时，不得包装成已接入真实机场流数据。

## 四、用户体验约束

- 目标用户无需理解 SSE、revision、CP-SAT 或后端状态机；页面必须一眼回答“现在发生什么、系统在做什么、需要我决定什么”。
- 继续使用五个切换工作页，不恢复把所有内容堆在一个长滚动页面的设计。
- 字体已按用户要求整体放大；最小可见字号维持至少 14px，主要操作和正文通常为 16px 或 18px，不得退回小字密集界面。
- 用语要专业、清楚、经过修饰，但不能堆术语，也不能过度大白话。
- 页面应安静、实用、工作导向；避免营销式大卡片、装饰性渐变、嵌套卡片和无功能动画。
- 桌面 `1440x900` 与移动 `390x844` 都不得出现文字重叠、控件裁切或文档级横向溢出。
- 图标按钮优先使用现有 `lucide-react`，陌生图标必须有 tooltip/可访问名称。
- 启动脚本优先打开 Microsoft Edge，不要改成 Chrome；停止脚本只能精确停止本项目双服务，不关闭浏览器或其他 Python/Node 进程。

## 五、M4-4 已确认事实

- 每个运行会话一个进程内 `RuntimeStreamBroker`，共享 `stream_id + sequence` 和 256 条环形缓冲。
- SSE 路由：`GET /api/v1/runtime-sessions/{session_id}/stream`。
- 业务状态来自 SQLite audit revision 和完整 `RuntimeSessionSnapshot`；消息层没有第二时钟、任务状态机、事件引擎或规划器。
- 同一流可按 `Last-Event-ID` 精确补发；非法、过旧、超前、已淘汰或旧 stream ID 返回完整 snapshot。
- SSE 失败时现有 REST `GET /runtime-sessions/{session_id}` 可独立承担短轮询恢复。
- 单 worker、进程内 stream 状态；服务重启生成新 stream ID，但 SQLite 会话、事件、候选和 revision 保留。
- 没有 SSE 连接时不运行独立后台 tick；重新连接或 REST 查询会先物化权威状态。
- M4-4 验证：专项 31 passed，M4 组合 62 passed，后端全量 129 passed，compileall 和 pip check 通过；真实 Uvicorn 首帧、连续补发、旧流回退、REST 一致性和重启恢复均已验收。

## 六、当前前端事实

- `App.tsx` 是五页和选择状态的共享中心，当前仍以 M3 三套固定方案快照为主。
- `api.ts` 先请求 `/api/v1/demo`，失败后读取 `public/demo-output.json`；静态回退已明确显示为本地演示数据。
- M4-5 开始前，前端尚无运行会话类型、运行控制、SSE 消费、两秒轮询、候选采用/拒绝或动态状态映射。
- `styles.css` 末部已有大字号可读性覆盖层，修改时要检查规则顺序，避免早期小字号规则重新生效。
- Vite 通过 `/api` 代理后端；根目录一键启动会先等待 FastAPI 健康，再启动 Vite 并打开 Edge。

## 七、收到“开始 M4-5”后的固定范围

1. 先创建 `docs/m4-05-plan.md`，写清状态所有权、用户流程、验收矩阵和非目标，再开始代码。
2. 基于优化初始方案查找该场景最近的 SQLite 会话；没有时创建，不得每次刷新生成新会话。
3. 五页共享同一 `RuntimeSessionSnapshot`、连接状态和选择状态，不创建平行时钟或前端任务状态机。
4. 消费 SSE：`runtime.snapshot` 原子替换完整快照，`runtime.tick` 只替换后端 clock；按 `stream_id + sequence` 去重。
5. SSE 连续失败时明确显示恢复状态，并每 2 秒读取 REST 快照；SSE 恢复后停止轮询。
6. 静态 JSON 只能是“离线只读，时间不会推进”，所有运行控制必须禁用。
7. 增加始终可见的仿真时间、开始/暂停、`1x/5x/15x`、连接状态、场景版本、重置和合适的手动重规划入口。
8. 所有修改命令带当前 `expected_revision`；409 冲突先重新读取权威快照，不得覆盖新状态。
9. 总览展示当前事实/系统动作/用户决定；任务按实时状态组织；事件展示处理时间流和航班动态；资源展示位置、移动进度、当前/下一任务和下一可用时间。
10. 方案依据页显示当前执行方案和待确认候选，提供明确“采用候选”和“保留当前方案”；候选确认前绝不静默替换 active plan。
11. 不因筛选、切页、查看详情或普通 tick 触发规划器；已完成和执行中的任务继续由后端冻结。
12. M4-5 只做前端运行接入与对应测试/文档，不提前宣称完成 M4-6 的长时、性能、故障和三轮总验收。

## 八、验证与收口要求

- 前端：`npm test`、`npm run typecheck`、`npm run build`。
- 后端：项目 `.venv` 运行全量 pytest，基线不得低于 129 项；再运行 `compileall` 和 `pip check`。
- 浏览器：使用真实双服务，优先 Edge；验证 1440x900、390x844、五页切换、开始/暂停/倍速、事件自动暂停、候选采用/保留、SSE 恢复和离线只读。
- 验收前检查控制台错误、文字/控件重叠、文档宽高、状态倒退、重复事件和多个页面的一致性。
- 若环境阻止常规浏览器自动化，可核验 `C:\Users\ASUS\AppData\Local\ClawChrom\ClawChrom.exe` 是否可用作 Playwright 回退，但不能声称它绕过环境策略。
- 更新 `docs/m4-05-review.md`、`docs/project-progress.md`、`docs/m4-realtime-operations-plan.md` 和 README，只记录实际通过的事实。
- 手工改文件使用 `apply_patch`；不要改动无关文件，不要处理既有 `frontend/public/demo-output.json` 的 CRLF 警告。
- 完成后独立提交 M4-5，并报告 commit、测试结果、真实服务/浏览器证据、已知边界和 M4-6 唯一下一入口。

---

这份提示词不等于授权开始 M4-5。接管窗口必须先完成只读核验并等待用户明确指令。
