# M4-5C Windows 一键启停进程身份修正审查记录

## 1. 审查结论

M4-5C 已完成。根目录一键启动入口可以重新识别已有服务、全新启动前后端并打开 Microsoft Edge；一键停止入口也可以通过同一套安全校验精确停止本项目服务。

本次问题不是网页或 API 启动失败，而是 Windows 在进程刚创建时将前端可执行路径错误记录成 `C:\Windows\System32\ntdll.dll`。服务实际为健康的 `node.exe`，但后续启动和停止均因路径不一致而拒绝操作。脚本现改为保存明确的 Python/Node 启动路径，并对已有非 `.exe` 错误记录使用项目可信路径完成兼容校验。

本单元只修复本地启停可靠性，不计入 M4-6，也不改变任何运行时业务状态、调度逻辑或安全边界。

> 仅供教学仿真与辅助决策使用，不构成真实机场运行、放行、登机、改签或车辆控制指令。

## 2. 根因证据

- 旧状态文件记录前端 PID `3716`、进程名 `node`，但 `executablePath` 为 `C:\Windows\System32\ntdll.dll`。
- 同一 PID 的实际进程路径为 `E:\nodejs\node.exe`，启动时间与状态文件一致，前端 `4173` 和后端 `8000` 均处于健康状态。
- 原启动脚本稳定复现 `Runtime process identity does not match the recorded service`，因此不会执行浏览器打开步骤。
- 停止脚本使用相同路径校验，也会在该错误状态下拒绝停止，用户无法通过标准入口恢复。

## 3. 修复内容与安全性

- `New-ServiceRecord` 不再从刚启动的 `System.Diagnostics.Process.Path` 猜测路径，而是由调用方明确传入 `.venv\Scripts\python.exe` 或当前 `node.exe`。
- 新状态文件始终保存规范化的实际 `.exe` 启动路径。
- 已有状态若保存的是 `.dll` 等非可执行路径，校验只回退到项目 `.venv` Python 或当前 PATH 解析出的 Node 路径。
- 项目根目录、服务名、PID、进程名、启动时间和实际可执行文件仍必须一致；未知服务、重复记录、PID 复用或真正的路径不匹配仍会拒绝操作。
- 启动失败回收、健康检查、端口选择、日志保存和停止顺序均未改变。

## 4. 验证证据

| 检查 | 结果 |
| --- | --- |
| 故障复现 | 旧脚本对现有状态返回进程身份不匹配错误，未替换任何进程 |
| PowerShell 语法 | `start-project.ps1`、`stop-project.ps1` 及其他脚本均可由 `ScriptBlock::Create` 解析 |
| 旧状态兼容 | 修复后同一旧状态可识别双服务并返回 `[OK] Backend and frontend are already running` |
| 一键停止 | 根目录停止入口精确结束前端 PID `3716`、后端 PID `27772`，删除状态文件并释放 4173/8000 |
| 一键启动 | 根目录启动入口刷新演示数据，后端健康、前端就绪，并成功打开 `http://127.0.0.1:4173` 的 Microsoft Edge 窗口 |
| 新状态记录 | 后端记录 `E:\ican\.venv\Scripts\python.exe`，前端记录 `E:\nodejs\node.exe` |
| 重复启动 | 后端 PID `16232`、前端 PID `23388` 均保持不变，服务组被复用而非重复创建 |
| 在线状态 | 后端健康为 `ok`，前端 HTTP 200，状态为 `ready` |
| 差异格式 | `git diff --check` 通过；只有既有 LF/CRLF 转换提示 |

## 5. 下一入口

正式服务继续运行在前端 `http://127.0.0.1:4173`、后端 `http://127.0.0.1:8000`。M4-6 仍是下一唯一执行入口。
