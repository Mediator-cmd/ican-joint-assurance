# Security Policy

## Scope

本仓库是匿名教学仿真项目。安全问题包括：密钥或个人信息意外进入仓库、API 越权修改运行状态、revision 保护失效、AI 绕过只读边界、请求体限制失效，以及部署配置暴露敏感数据。

## Reporting

请不要在公开 issue 中粘贴 API key、数据库、服务日志或旅客信息。公开仓库启用 GitHub 后，优先使用 GitHub Security Advisories 的私密报告入口；若该入口尚未启用，先通过仓库所有者配置的私密渠道报告，再公开复现步骤的脱敏版本。

报告至少应包含：受影响 commit、复现步骤、预期/实际行为、影响范围和已脱敏日志。项目维护者会先确认问题，再决定修复版本和公开时间。

## Secret handling

- 真实 AI key 只放在本机 DPAPI 配置或部署平台 Secret Manager。
- 不要提交 `.env`、`.runtime`、SQLite、日志、构建产物或截图缓存。
- 发现 key 泄露时，应立即撤销并轮换，而不是只删除 Git 当前文件。
