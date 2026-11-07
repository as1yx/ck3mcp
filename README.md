# CK3MCP — Crusader Kings III AI Agent & MCP Server

[English](#english) · [简体中文](#简体中文)

## English

**CK3MCP (`ck3mcp`) is an experimental Windows visual AI agent and local Model Context Protocol (MCP) server for Crusader Kings III (CK3).** It combines screenshots, a vision model, guarded mouse/keyboard input, and persistent action verification.

Version **0.4.5** is a prototype focused on **appointing a vacant council position**. It does not yet provide full-game autonomous play.

### Current status

- **Passed:** 18 offline regression tests, including mocked DeepSeek and OpenAI requests, and the local MCP stdio smoke test.
- **Observed in CK3 1.20.1.2:** a new non-Ironman game, paused council panel, and the Chancellor candidate list. All five council positions were occupied, so no appointment was performed.
- **Not accepted end to end:** native game input/screenshot/F12 behavior, provider integration, and autonomous council appointment still require real-game validation. Offline Runner tests use simulated game/model components.

See the [validation report](docs/VALIDATION.md) for the evidence and remaining limits.

### How it works

1. Capture the foreground `ck3.exe` window.
2. Ask a configured vision model for one structured action.
3. Validate the task stage and intended target; reserve verification budget.
4. Capture again and reject stale or changed scenes before input.
5. Persist action state to SQLite, execute once, and verify the resulting screenshot.
6. Perform a separate final verification before reporting completion.

The task stages are `open_council → choose_vacancy → choose_candidate → submit_assignment → verify_assignment`. The current design requires a separate candidate-selection and final-confirmation step. **If the game appoints immediately when selecting a candidate, stop: that interaction is not yet supported by the current stage design.**

### Requirements

- Windows and Python 3.12+; the supplied installer targets Python 3.12.
- A locally installed copy of Crusader Kings III.
- An API model supporting image input, Chat Completions, and JSON-object output. The official DeepSeek endpoint uses `max_tokens`. Other endpoints use `max_completion_tokens`.
- A backed-up, non-Ironman single-player test save, loaded and manually paused, with a vacant council position.

### Setup and run

Clone or download this repository. In PowerShell, inside its folder:

```powershell
.\install.ps1
```

The installer creates `.venv` and a missing `config.json`. Edit `config.json` to set your vision model ID and `base_url`. The default example uses the OpenAI endpoint and leaves the model ID for you to fill in.

For DeepSeek, copy the `model` and `base_url` values from [the DeepSeek example](config.deepseek.example.json) into your `config.json`. This selects `deepseek-flash` at `https://api.deepseek.com`. Use a DeepSeek API key for `CK3_API_KEY`. The same configuration works with the CLI and MCP server. Live DeepSeek API and CK3 gameplay validation are pending. [DeepSeek vision documentation](https://api-docs.deepseek.com/guides/vision/)

The API key is read from `CK3_API_KEY`, not from the configuration file. The startup script prompts for it with hidden input if it is absent:

```powershell
.\start-agent.ps1
```

After starting, switch to the paused CK3 window within eight seconds. The agent binds only to foreground `ck3.exe`; focus loss stops execution. Keep the mouse and keyboard available exclusively to the agent while it runs.

Changing the `task` text does not add support for construction, marriages, warfare, or other gameplay tasks.

### Stop and recovery

- Press **F12**, use **Ctrl+C** in the agent terminal, or create a `STOP` file in the active run directory.
- Stopping input does not itself pause the game. Native stop latency has not been validated.
- Unresolved actions block new input; the agent does not automatically replay them.

With all agent/MCP control processes stopped, inspect pending records:

```powershell
.\.venv\Scripts\ck3-agent.exe pending --config config.json
```

After manually checking the game and screenshots, record the actual outcome:

```powershell
.\.venv\Scripts\ck3-agent.exe reconcile ACTION_ID success --note 'Manually confirmed the intended appointment' --config config.json
```

Outcomes are `success`, `failed`, or `not_executed`. Reconciliation records a human conclusion; it does not inspect the game or repeat an action. Do not delete the action database to bypass unresolved records.

### MCP integration

For DeepSeek Harness, use the English-first bilingual [player instructions](agent.md) and [Cordis patch example](harness.cordis.example.yml). The patch explicitly forwards `CK3_API_KEY`; Harness otherwise filters credential environment variables from MCP children. Enable image input and attachment support in Harness. Live Harness integration remains unverified.

Start the local stdio server from the project directory:

```powershell
.\.venv\Scripts\ck3-mcp.exe --config config.json
```

For an MCP host, configure that executable and an absolute path to your configuration file. Tool order: `bind_game` → `get_game_state` / `screenshot` → `execute_action` → `verify_action`; after submission, call `verify_task`. `get_run_status` and `emergency_stop` provide status and cancellation.

The MCP server also needs a configured vision API: pause checks and result verification make internal model requests. External host-model usage is outside the local budget. Run only one CLI or MCP controller at a time; no remote HTTP control server is provided.

### Limits and data

- Visual facts are model claims, not authoritative game telemetry. Scene comparison and schema checks cannot eliminate recognition errors.
- No autonomous date advancement, automatic saving, Mod telemetry, warfare, succession, Ironman, multiplayer, or full-game autonomy.
- Local limits cover runtime, steps, calls, and estimated tokens; they are not a provider billing cap.
- Run screenshots, reports, and `events.jsonl` are stored in `runs/`; SQLite action state is shared across runs. Cleanup is manual.
- `config.json`, API secrets, runs, screenshots, and save files must not be committed. This repository includes source and example configuration only.

### Project files

- [AGENTS.md](AGENTS.md): development workflow and invariants, in Chinese.
- [CHANGELOG.md](CHANGELOG.md): historical changes, in Chinese.
- [Development brief](docs/开发任务书_v1.md): intended scope, not a statement of completed functionality.
- [Validation report](docs/VALIDATION.md): English and Chinese validation summary.

CK3MCP is an independent project and is not affiliated with or endorsed by Paradox Interactive. No CK3 game assets or save files are distributed.

## 简体中文

**CK3MCP（`ck3mcp`）是面向《十字军之王 III》（Crusader Kings III / CK3）的 Windows 视觉 AI Agent 与本地 MCP 服务原型。** 程序通过截图、视觉模型、鼠标键盘输入及持久化核验组成操作循环。

当前版本 **0.4.5** 仅聚焦于**任命一个空缺内阁职位**，尚未实现整局自主游玩。

### 当前验证状态

- **已通过：** 18 项离线回归测试，覆盖 DeepSeek 和 OpenAI 模拟请求。本地 MCP stdio 冒烟测试也已通过。
- **真实游戏已观察：** CK3 1.20.1.2 新建非铁人局、暂停状态、内阁面板与掌玺大臣候选列表。五个内阁职位均有任职者，因此未执行任命。
- **尚未完成端到端验收：** 项目原生游戏输入、截图和 F12、模型服务兼容性、自主任命仍待实测。离线 Runner 测试使用模拟游戏和模型。

详细证据与限制见[验证记录](docs/VALIDATION.md)。

### 工作方式

1. 截取前台 `ck3.exe` 窗口。
2. 视觉模型提出一个结构化动作。
3. 程序检查任务阶段和预期目标，预留核验额度。
4. 输入前重新截图；画面变化或过期则拒绝旧动作。
5. 动作状态先写入 SQLite，执行一次后截图核验。
6. 最终核验通过并落盘后，才报告视觉判断完成。

任务阶段为 `open_council → choose_vacancy → choose_candidate → submit_assignment → verify_assignment`。当前设计要求选择候选人与最终提交之间有独立确认步骤。**如果实际界面点击候选人就立即任命，应停止；当前阶段设计尚未支持该交互。**

### 安装与启动

需要 Windows、Python 3.12+、本地 CK3，以及支持截图输入、Chat Completions 和 JSON object 的模型服务。DeepSeek 官方地址使用 `max_tokens`，其他地址使用 `max_completion_tokens`。安装脚本使用 Python 3.12。

在项目目录的 PowerShell 中运行：

```powershell
.\install.ps1
```

脚本创建 `.venv` 和缺失的 `config.json`。编辑配置中的模型 ID 与 `base_url`。默认示例使用 OpenAI 地址，模型 ID 由你填写。

使用 DeepSeek 时，把[配置示例](config.deepseek.example.json)中的 `model` 和 `base_url` 填入现有 `config.json`，即使用 `deepseek-flash` 与 `https://api.deepseek.com`。将 `CK3_API_KEY` 设为 DeepSeek API 密钥。CLI 和 MCP 可使用同一份配置。真实 API 和游戏操作仍待实测。[DeepSeek 视觉文档](https://api-docs.deepseek.com/guides/vision/)

密钥从本机 `CK3_API_KEY` 读取。先备份非铁人单人测试存档，载入一个有空缺内阁职位的场景并手工暂停，再运行：

```powershell
.\start-agent.ps1
```

若环境变量尚未设置，脚本会隐藏输入密钥。启动后 8 秒内切回暂停的 CK3。程序只绑定前台 `ck3.exe`，失焦会停止；运行时应让 Agent 独占鼠标键盘。

修改 `task` 文本不能获得建筑、婚约、战争或其他任务能力。

### 停止与恢复

- F12、终端 Ctrl+C，或在实际运行目录创建 `STOP` 文件。
- 停止输入不代表游戏已暂停；原生停止延迟仍未实测。
- 未确认动作会拦截后续输入，不自动重放。

先停止其他 Agent/MCP 控制进程，再查看待核查记录：

```powershell
.\.venv\Scripts\ck3-agent.exe pending --config config.json
```

人工查看游戏与截图后，使用实际动作 ID 记录结论：

```powershell
.\.venv\Scripts\ck3-agent.exe reconcile ACTION_ID success --note '已人工确认目标人物在目标职位' --config config.json
```

结论可以是 `success`、`failed` 或 `not_executed`。命令只记录人工结论，不检查游戏，也不重放动作；不要删除数据库绕过未确认记录。

### MCP 使用

DeepSeek Harness 用户请使用英文在前的双语[游玩指令 agent.md](agent.md)和[Cordis 配置示例](harness.cordis.example.yml)。示例显式传入 `CK3_API_KEY`，否则 Harness 会过滤 MCP 子进程的凭据环境变量。Harness 还需支持图片输入并启用附件；真实 Harness 接入尚待验证。

从项目目录启动本地 stdio 服务：

```powershell
.\.venv\Scripts\ck3-mcp.exe --config config.json
```

在 MCP 宿主中填写此程序及配置文件的绝对路径。调用顺序为 `bind_game` → `get_game_state` / `screenshot` → `execute_action` → `verify_action`；提交后继续 `verify_task`。`get_run_status` 查询状态，`emergency_stop` 请求停止。

MCP 内部的暂停检查和结果核验也会调用视觉 API，需要配置密钥。外部宿主自身的模型用量不计入本地预算。不要同时运行 CLI 和 MCP 控制器；当前只提供本地 stdio。

### 限制与数据

- 视觉事实来自模型，不是游戏权威遥测；画面比较和结构检查不能消除识别错误。
- 不支持自主推进日期、自动存档、Mod 遥测、战争、继承、铁人、多人及整局自主。
- 时间、步骤、调用和估算 Token 上限仅约束本地流程，不是服务商账单硬上限。
- 截图、报告和 `events.jsonl` 位于 `runs/`；SQLite 动作数据库跨运行共用，数据目前手工清理。
- 不应提交实际 `config.json`、密钥、运行目录、截图或存档。本仓库仅分发源码和示例配置。

开发流程见 [AGENTS.md](AGENTS.md)，历史改动见 [CHANGELOG.md](CHANGELOG.md)，原始目标见[开发任务书](docs/开发任务书_v1.md)。任务书描述目标，不代表全部功能已完成。

CK3MCP 是独立项目，与 Paradox Interactive 无隶属或官方背书关系，不分发 CK3 游戏素材或存档。
