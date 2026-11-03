# CK3MCP validation / 验证状态

## English

Recorded on 2026-11-01, with publication documentation prepared on 2026-11-03. Version: 0.4.4.

### Completed checks

- Python 3.12.14; 33 installed dependencies passed compatibility checks.
- All 17 offline regression tests passed in 7.846 seconds. The suite compiles project modules in memory and covers budgets, reservation rollback, cancellation, cleanup after logging errors, target/schema rejection, progress detection, SQLite restart behavior, and simulated Runner flows.
- SQLite tests use real temporary databases; game and model behavior in Runner tests is simulated. These tests make no paid model requests and issue no game input.
- A real MCP stdio subprocess initialized successfully, registered the expected eight tools, and rejected `get_run_status` before binding.
- Publication check on 2026-11-03: the portable scripts were rerun against the publication copy; all 17 regression tests passed in 3.432 seconds and the MCP smoke test passed again. No game input or model API calls were made.

### Supervised game observations

CK3 1.20.1.2 was launched and a new non-Ironman 1066 Munster game was created. The game remained paused on 1066-09-15. An external Computer Use tool opened the character panel, council panel, and Chancellor candidate list.

All five council positions were occupied. The candidate list displayed appointment/swap buttons, but no dismissal, appointment, or swap was performed. The list was closed and the incumbent council was preserved. Whether appointment requires a separate confirmation is still unverified.

The scenario did not meet the prototype's vacant-position precondition. External Computer Use interaction does not validate the project's native Windows controller.

### Not yet validated

- Native WindowsGame input, screenshot capture, F12 behavior, and stop latency.
- A paid vision-model API integration and its request/response compatibility.
- A complete autonomous council appointment and independent final verification.
- Full-game autonomous play; this is outside the implemented scope.

### Reproduce the offline checks

From the repository directory, after installing dependencies:

```powershell
.\.venv\Scripts\python.exe -B tests\offline_regression.py
.\.venv\Scripts\python.exe -B tests\mcp_smoke.py
```

The smoke test starts an actual stdio server with the example configuration but never binds the game. It needs no API key. Both scripts locate the source relative to their own path.

## 简体中文

2026-11-01 已完成依赖检查、17 项离线回归测试和真实 MCP stdio 冒烟测试。SQLite 使用真实临时数据库；Runner 中的游戏和模型为模拟对象。测试不控制游戏，也不调用付费模型。

真实 CK3 1.20.1.2 中已新建 1066 年芒斯特非铁人测试局，保持暂停并查看内阁及掌玺大臣候选列表。五个内阁职位均有现任者，未开除、指派或交换。该场景不满足填补空缺职位的前置条件；独立任命确认步骤仍未验证。

项目原生 Windows 控制器、截图、F12、模型服务接入和完整自主任命均未验收。外部 Computer Use 工具操作成功不能替代这些验证。完整中文历史记录见[验证记录](验证记录-2026-11-01.md)。

离线复现命令见上方；两个脚本均从所在位置定位源码，不再依赖开发者的本机路径。
