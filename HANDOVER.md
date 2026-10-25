# Brawlhalla 对手习惯分析器 v2 — 交接文档

> 最后更新：2026-10-25（v17.6）
> 数据规模：293 个回放文件 / 262 场有效 1v1 / 208 个对手 / 记忆 9 条事实 /
> 知识库：机制 10 条 + 69 英雄 + 16 武器 + 18 页 wiki 原文
>
> 本文档以**代码现状**为准（v17.6，含用户 v17.6 代码审查修复），
> 全部"已验证"结论均在 2026-10-25 用文内命令复跑过。

---

## 0. 30 秒速览

**一句话**：解析 Brawlhalla 的 `.replay` 文件（只含按键输入流），统计每个对手的
"真闪避 vs 地面 dash 位移"习惯画像与受压反应模式，输出 HTML 报告，并提供一个
带长期记忆、可自动调用 16 个本地数据工具的 AI 打法教练（聊天窗口 + MCP）。

四个关键路径：

| 项 | 路径 |
|---|---|
| 源码目录 | `<项目目录>\` |
| 原始数据（回放） | `%USERPROFILE%\BrawlhallaReplays\`（293 个 .replay） |
| 数据产物 | `<数据目录>\out-v2\`（报告/缓存/会话/记忆/配置）+ `<数据目录>\kb\`（知识库） |
| 程序入口 | 桌面 `Brawlhalla Analyzer.lnk` → `<安装目录>\dist-v2\BrawlhallaAnalyzerV2\BrawlhallaAnalyzerV2.exe`；CLI 入口见第 5 节 |

**接手后第一件事**：跑第 9 节的三条基线命令，确认成绩与文档一致，再动任何代码。

---

## 1. 环境坑（全部实测）

| 编号 | 坑 | 后果（具体报错） | 规避方法（具体做法） |
|---|---|---|---|
| 1 | 托管 Python（`C:\Users\用户\.workbuddy\binaries\python\versions\3.13.12\python.exe`，实际版本 3.13.14）**没有 tkinter** | `import tkinter` → `ModuleNotFoundError: No module named 'tkinter'` | GUI 开发/冒烟用系统 Python `C:\Users\用户\AppData\Local\Programs\Python\Python313\python.exe`（3.13.6，带 tkinter + PIL） |
| 2 | 长任务前必须重定向 TEMP | 原始 TEMP 指向 8.3 短名路径（`C:\Users\MR3B6F~1.MOU\...`），Python tempfile/sqlite 会报错 | 命令前加前缀 `TMP='D:\bh-tmp' TEMP='D:\bh-tmp' TMPDIR='D:\bh-tmp'`（`D:\bh-tmp` 目录必须存在） |
| 3 | **pywebview 必须独占进程主线程** | 与 tkinter 同进程：tkinter 后台线程 + webview 主线程以外的组合全部失败（`WebViewException: pywebview must be run on a main thread`；hidden 保活窗口方案直接挂死） | 聊天窗口做成**独立 exe**（`ChatWindow.exe`），主程序 subprocess 拉起。不要尝试同进程方案，全部试过 |
| 4 | Windows SO_REUSEADDR 允许重复绑定端口 | 聊天服务双实例（桌面自动启动 + 手动点击竞态）随机吞掉连接，页面请求无响应 | `chat_server.run_server()` 先用 socket 探测端口，已监听则直接 return（幂等，已实现，勿删） |
| 5 | 打包期间 exe 进程未退出会锁 DLL | PyInstaller 报 `PermissionError: [WinError 5] 拒绝访问。: '..._internal\libffi-3.dll'` | 重打包前先 `taskkill //F //IM BrawlhallaAnalyzerV2.exe`（和 ChatWindow.exe） |
| 6 | PyInstaller 的 `--icon` 相对路径按 spec 目录解析 | `FileNotFoundError: Icon input file <安装目录>\build-v2\app.ico not found` | icon 用**绝对路径** |
| 7 | cmd 按 GBK 读 .bat，中文注释乱码后被当命令执行 | 启动器报错 | .bat 保持纯 ASCII + 首行 `chcp 65001 >nul`（现已无 .bat，此坑留给未来） |
| 8 | 深色控制台/管道下 Python 打印中文乱码（GBK） | UnicodeEncodeError 或乱码 | 脚本开头 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` |
| 9 | DeepSeek 概率性行为（模型侧，不是环境） | ① 跳过工具直接编数据；② 工具调用死循环；③ 禁工具后输出内联 `<tool_call>`/DSML 残留 | 三层防护已在 `agent.py`（强制兜底注入 / 缓存回结果 / `_sanitize_reply` 清洗），详见 v10.1/v14/v17.4/v17.5 条目，**勿删防护** |
| 10 | 停止生成会留下**畸形会话结构**（半截 JSON 的 tool_calls） | 下一轮请求 400 卡死或模型行为错乱 | `agent._sanitize_history()` 每次请求前自愈（已实现，勿删） |
| 11 | ZCode 内置浏览器对带中文参数的 URL 支持差 | 页面不渲染（title 显示 URL 原文） | 带中文参数的 URL 用系统浏览器打开（桌面软件调用的就是系统默认浏览器，不受影响） |

已排除的误传：**"C 盘对 Python 禁写"在本项目目录不成立**（2026-10-25 实测：
Python 可在本项目目录创建/删除文件）；"COM 创建 .lnk 被策略挡"对 ZCode 环境
也不成立（v8 用 PowerShell COM 成功创建了桌面快捷方式）。原版交接文档的这两条
是 WorkBuddy 沙箱环境的结论，勿直接沿用。

---

## 2. 架构与数据流

```
.replay 文件（zlib + XOR 位流, 293 个）
   │
   ▼
bh_replay.py  parse_replay_file()          解析：zlib → 64字节循环XOR → MSB位流
   │                                        → 4bit 标签对象序列（Header/GameData/
   │                                          Results/Inputs/KOFaces/End）
   ▼
analyze.py  build_dataset()                统计引擎（v4 口径，见下方"判定地基"）：
   │                                        闪避三分（空中/原地/dash）· 受压意图四分
   │                                        （纯防御/瞬时/进攻/自发）· 胜负=名次
   ├──► cache.py  load/save                 增量缓存（指纹 mtime+size，CACHE_VERSION=4，
   │                                         记录 replay_dir，换目录自动作废）
   ├──► bh_api.py                           稳定 JSON 查询层（16 工具的数据源）：
   │         │                              画像/名单/对比/历史/事件明细/周报/聚类/
   │         │                              条件筛选/情报卡/笔记读写
   │         ▼
   │    ┌─ bh_mcp.py        MCP 服务器（stdio JSON-RPC，与 agent 工具注册表同源）
   │    └─ chat_server.py    本地聊天服务（127.0.0.1:8903，HTTP+SSE）
   │              │
   │              ▼
   │    浏览器聊天页（chat_server 内嵌 HTML/CSS/JS）+ ChatWindow.exe（pywebview 壳）
   │
   ├──► report.py  generate_report()         HTML 报告（shadcn 亮暗双主题，增量缓存 0.1s）
   ├──► bh_query.py  load_dataset()          CLI（缓存读写、增量解析）
   └─► agent.py  Agent                       AI 教练核心（OpenAI 兼容流式 +
            │                                 Function Calling 工具循环 + 记忆注入）
            ├──► memory_store.py              三层长期记忆（语义 facts / 情节 old_turns / 短期 history）
            └──► kb_store.py / kb_update.py   游戏知识库检索与更新（wiki.gg 抓取）
```

### 模块接口速查

| 模块文件 | 关键函数/入口 | 一句话说明 |
|---|---|---|
| `bh_replay.py` | `parse_replay_file(path)` / `parse_replay_bytes(raw)` | 回放解析（**勿动**，293/293 往返校验的地基） |
| `bh_write.py` | 直接运行 | 往返校验器（解析→重序列化→逐字节比对） |
| `analyze.py` | `build_dataset(dir)` / `aggregate_by_opponent(ds)` / `analyse_entity(seq, ms)` | 统计引擎；v4 口径全在这里 |
| `cache.py` | `load/save/fingerprint` | 增量缓存；`CACHE_VERSION=4`；记录 replay_dir |
| `bh_query.py` | `load_dataset()` / `main()` | CLI 入口 + 缓存读写（增量解析） |
| `bh_api.py` | `list_opponents / opponent_profile / my_profile / compare / match_history / match_detail / match_summary / weekly_report / loss_analysis / query_opponents / cluster_opponents / scout_card / get|set_opponent_note` | 16 工具的稳定 JSON 层 |
| `agent.py` | `Agent.chat(q, on_delta, on_tool, abort_check)` / `_tools_registry()` / `build_system_prompt()` | Agent 核心：流式 + 工具循环 + 记忆注入 + 自愈 |
| `memory_store.py` | `add_fact / push_old_turn / replace_facts / prompt_block / stats` | 三层长期记忆（out-v2\agent_memory.json） |
| `kb_store.py` | `search_kb(q)` / `legend_knowledge(names)` / `KB_DIR` | 知识库检索 |
| `kb_update.py` | 直接运行（`--offline`） | 从 wiki.gg MediaWiki API 更新知识库 |
| `bh_mcp.py` | 直接运行 | MCP 服务器（stdio），工具表复用 agent.py |
| `chat_server.py` | `run_server(port)` / 直接运行 | 本地聊天服务（HTTP+SSE），exe 内嵌运行 |
| `chat_window.py` → `ChatWindow.exe` | 直接运行 | pywebview 聊天窗口壳 |
| `bh_desktop.py` | `App(root)` | tkinter 主程序（画像/设置/报告/聊天启动） |
| `report.py` | `generate_report(dir)` / `advice_for(stats, is_opponent, lang)` | HTML 报告（含双语规则建议） |
| `legends.py` | `legend_name(id)` | 英雄 ID→名字（名册顺序，新英雄在此追加） |

### 判定逻辑的地基（改任何统计前必读）

1. **回放只存输入流**：每条记录 = `(时间戳ms, 14位按键位域)`，仅状态变化时记录。
   位定义（`bh_replay.py`）：bit0-3 方向（上/下/左/右）、bit4 跳、bit5 中优先、
   bit6 重攻、bit7 轻攻、**bit8 闪避/dash（同一键）**、bit9 拾取投掷、bit10-13 嘲讽。
2. **地面 闪避键+方向 = Dash**（位移，无无敌帧，不算 dodge）；地面真闪避只有
   **原地闪**（16 帧无敌 / 1s 冷却）；空中闪避 12/20 帧无敌 / 2.7s 冷却。
   这是 wiki.gg Movement 页的机制结论，v4 口径的地基。
3. **闪避三分**（`analyse_entity`）：按下帧 held 状态判定——跳跃位或 350ms 内
   起跳 = 空中闪；地面+方向位 = dash（位移）；地面无方向 = 原地闪。
   **不做方向回溯**（回溯会把真原地闪错划成 dash）。
4. **受压意图四分**（`match_reactive`）：对手出招后 0–400ms 窗口内；
   `<80ms` = 瞬时按键（同按/预输入）；出招前 300ms 内自己按过攻击 = 进攻性
   （chase dodge）；其余 80–400ms = 纯防御；无窗口 = 自发。
5. **胜负 = results.scores 的名次**：1=第一名。实测 scores 恒为 {1,2}，
   名次判定与"死亡次数更少者" 252/252 一致。**绝不能用 max(scores)**。
6. 对局时长以**输入流末时间戳**为准（results.length 有脏数据），<30s 的局过滤。

---

## 3. 已验证状态 vs 未验证状态

| 验证项 | 当前成绩（2026-10-25 实测） | 怎么复跑 |
|---|---|---|
| 字节级往返校验 | **293/293 完全一致** | `cd <源码目录> && "C:\Users\用户\.workbuddy\binaries\python\versions\3.13.12\python.exe" bh_write.py` |
| 胜负判定 vs 死亡数启发式 | 252/252 一致 | 见 `_probe_winner.py`（已进回收站，可还原；逻辑：min(scores) vs Counter(deaths) 少者） |
| v4 三分类计数一致性 | 50 对手抽查通过 | `_probe_v4.py`（回收站可还原；断言 dir_counts 合计=dodges 等） |
| 报告生成（缓存命中） | **0.08s**（208 对手 / 262 场） | `from report import generate_report; generate_report(r"%USERPROFILE%/BrawlhallaReplays")` 计时 |
| bh_query 缓存命中 | 0.0s / 262 缓存命中 | `python bh_query.py --self` 看 stderr 首行 |
| Agent 工具循环 | mock LLM 端到端 16/16（v17.6）+ 真实 DeepSeek 多轮 | mock 方案：monkeypatch `agent._post_stream`（见 git 历史/本文 §8）；真实：聊天窗口发问 |
| 聊天服务压测 | 14 项场景矩阵 14/14、零污染 | `python _stress_test.py`（需服务已启动；脚本在源码目录） |
| 长期记忆跨会话 | 会话1 告知 → 重启后准确复述并拉数据对比 | 清空 `agent_memory.json` → 会话1 说"记住：…" → 等 45s → 新 `Agent` 实例问"我的训练目标" |
| MCP 服务器 | 握手 OK + 16 工具 | 见第 5 节 MCP 命令 |
| 聊天窗口 exe | 启动 12s 稳定、渲染正常 | `start "" "<安装目录>\dist-v2\ChatWindow\ChatWindow.exe" 8903` 后 tasklist 验证 |
| 双版本解析（v270 legacy） | **实现了但未实测**（无 10.11 时代回放文件） | 拿到旧回放后丢进回放目录跑往返校验 |
| 录屏 CV 受击判定 | **纯设想**（旧项目方案，未开工） | 无 |

**信任地基**：`bh_replay.py`（解析）+ `bh_write.py`（往返校验）。
**改 `bh_replay.py` 后必须重跑往返校验且 293/293 不回退**；改 `analyze.py` 的
统计口径后必须 `CACHE_VERSION` += 1（`cache.py`）并抽查计数一致性
（dir_counts 合计=dodges、意图四桶合计=dodges）。

---

## 4. 已知边界与局限性（防止过度承诺）

1. **回放没有受击事件**——"被打中"物理上不可见。"受压闪避率"是
   "对手出招后 80–400ms 内的真闪避"的**代理指标**，已剔除瞬时同按（<80ms）
   与进攻性位移（对方自己出招后 300ms 内）。用户曾质疑"把小跳和 dash 算成
   受击闪避"——v4 已按游戏机制分层过滤，但真·受击判定只能走录屏 CV 路线。
2. **方向是屏幕绝对方向**——分不出朝向前后。"往左闪"对面向右的对手是后撤，
   对面向左的对手是贴脸。
3. **地面 dash 与站桩闪在输入位上可分**（v4 已分），但"站桩闪是有意防守还是
   漏判方向"不可分——v4 的 120ms 内方向回溯已移除（会污染分类），当前以
   "按下帧 held 状态"为准。
4. **反应直方图只含 80–400ms 纯防御样本**；<80ms 的按键被判定为同按/预输入，
   不是反应。旧版 128ms 反应中位是被瞬时按键压低的，v4 修正为 192ms。
5. **部分回放 results.length 是脏数据**（写 28 分钟实际 18 秒）——时长一律以
   输入流末时间戳为准，<30 秒的局过滤。
6. **agent 上下文有上限**（40 条消息，超出从 user 边界裁剪并归档进情节记忆）
   ——"记得多久"约最近十几轮 + 长期记忆 facts，不是无限记忆。
7. **`data.json` / `kb/profiles` 是聚合快照**——事件级明细要用
   `bh_api.match_detail` 现查。
8. 用户/前维护者质疑过的点及标准回答：
   - "为什么旧版方向面板里 84% 是 dash？" → 见 §〇 v4 机制修正；
   - "你只能分析对手和复盘吗" → 功能清单：筛选/对比/训练计划/周报/连败共性/
     机制问答/笔记/情报卡（chat_server 欢迎卡里有 5 条建议问法）；
   - "我遇到 用户（我自己）该怎么打" → me 是用户本人，走自我复盘
     （my_profile 按"对手视角"分析弱点），不是查对手表。

---

## 5. 日常操作命令

通用前缀（长任务必加）：

```bat
set TMP=D:\bh-tmp&& set TEMP=D:\bh-tmp&& set TMPDIR=D:\bh-tmp
```

托管 Python（CLI/服务，无 tkinter）：

```
set PY=C:\Users\用户\.workbuddy\binaries\python\versions\3.13.12\python.exe
```

```bat
:: 生成 HTML 报告（增量缓存，重复生成 0.1s）
"%PY%" report.py "%USERPROFILE%\BrawlhallaReplays"

:: 字节级往返校验（改 bh_replay.py 后必跑，预期 293/293）
"%PY%" bh_write.py

:: CLI 速查（模糊匹配对手名 / 自己 / 全部导出）
"%PY%" bh_query.py Zett
"%PY%" bh_query.py --self
"%PY%" bh_query.py --all

:: 知识库更新（抓 wiki.gg；--offline 只刷新本地档案；--no-legends 跳过逐英雄页）
"%PY%" kb_update.py

:: 独立运行聊天服务（默认 8903；桌面软件启动时会自动起）
"%PY%" chat_server.py 8903

:: MCP 服务器（stdio，供 ZCode 等客户端拉起；不要手动交互运行）
"%PY%" bh_mcp.py
```

系统 Python（GUI 开发/冒烟，有 tkinter + PIL）：

```
set PYS=C:\Users\用户\AppData\Local\Programs\Python\Python313\python.exe
```

```bat
:: 桌面软件开发运行
"%PYS%" bh_desktop.py
```

重新打包（先杀进程，icon 用绝对路径，两个 exe）：

```bat
taskkill /F /IM BrawlhallaAnalyzerV2.exe & taskkill /F /IM ChatWindow.exe
<安装目录>\venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --onedir ^
  --icon "<项目目录>\app.ico" ^
  --name BrawlhallaAnalyzerV2 --distpath <安装目录>\dist-v2 ^
  --workpath <安装目录>\build-v2 --specpath <安装目录>\build-v2 ^
  "<项目目录>\bh_desktop.py"
<安装目录>\venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --onedir ^
  --name ChatWindow --distpath <安装目录>\dist-v2 ^
  --workpath <安装目录>\build-cw --specpath <安装目录>\build-cw ^
  --hidden-import webview.platforms.winforms --hidden-import webview.platforms.edgechromium ^
  --collect-all webview --icon "<项目目录>\app.ico" ^
  "<项目目录>\chat_window.py"
```

知识库 MCP 接入 ZCode：把 `mcp-config-example.json` 的 `mcpServers` 段加进
ZCode 的 MCP 配置后重启 ZCode。

---

## 6. 未完成 / 可做的方向（按性价比排序）

1. **训练计划跨周自动检查**（成本 ~30 分钟）：现在记忆里存了目标，但"进度
   检查"要用户主动问。可加启动钩子：`weekly_report` + `memory_store.facts`
   对比上次记录， goals 有数据更新时在欢迎消息里主动汇报。入手：
   `chat_server.refresh_profiles()` 或 `bh_desktop._on_loaded`。
2. **周报自动落盘**（~30 分钟）：`weekly_report` 结果写 `out-v2\weekly\YYYY-Wxx.json`，
   追踪才有历史。入手：`bh_api.weekly_report` 尾部加落盘。
3. **连败滑坡主动告警**（~1 小时）：`loss_analysis` 已有数据，连续 3+ 负时在
   欢迎消息提示。入手：`bh_desktop._on_loaded` 尾部。
4. **对手风格聚类落库**（~1 小时）：`cluster_opponents` 结果存 kb/profiles，
   新对手打完一局自动归类。入手：`bh_api.cluster_opponents` + chat_server 钩子。
5. **反应直方图进聊天页**（~2 小时）：`hist_svg` 在 report.py，可复用渲染。
6. **BrawlDB 真连招抓取**（半天）：`brawldatabase.com` 每武器约 195 条真连招，
   补齐知识库最大的缺口（wiki.gg 武器页只有招式 Kit 没有连招段）。入手：
   新写 scraper，落 `kb/weapons.json` 的 combos 字段。
7. **录屏 CV 受击判定**（数天，旧项目方案）：补"被打中"信号，把受压指标升级为
   真·受击后闪避方向。参考旧项目（见 §8），明确做大再动。

**明确不做 / 已放弃**：

- **tkinter 同进程内嵌 webview**——pywebview 要求主线程，所有组合实测失败
  （§1 坑 3），别再试；
- **闪避方向的 120ms 回溯**——v4 实测会把真原地闪错划成 dash，已移除，别加回；
- **hero/level_id → 中文名映射**——原版试过从游戏 `Gfx_*` 文件 grep，收益低放弃；
- **memory_store 无限扩容**——facts 上限 18 条 + 模糊去重是刻意设计，别去掉。

---

## 7. 上下游依赖与波及范围

**本项目依赖**：

- 回放目录 `%USERPROFILE%\BrawlhallaReplays\`（293 个 .replay）；
- `<数据目录>\out-v2\`（缓存/报告/会话/记忆/配置的读写地）；
- `<数据目录>\kb\`（知识库，chat_server 与 exe 运行时都按
  `BH_KB_DIR` 环境变量 > 源码旁 > D 盘回退的顺序解析）；
- `<数据目录>\ref\`（C# 参考库 allhailcheese/BrawlhallaReplayLibrary，
  **只当线索**：它有 3 处与实测不符——对象顺序、结尾魔数、被忽略位；
  真相裁决标准是 bh_write.py 往返校验）；
- wiki.gg 的 MediaWiki API（kb_update 联网抓取）；
- 旧项目 `C:\Users\用户\WorkBuddy\2026-09-29-23-21-16\brawlhalla-dodge\`
  （录屏 CV 路线的参考，含 `_ground_truth.csv`）。

**反向依赖（改这里会波及那里）**：

- `agent.py _tools_registry()` ← `bh_mcp.py`（MCP 工具表直接复用）：
  改工具签名/返回格式，MCP 客户端的提示词需同步；
- `chat_session.json` / `agent_memory.json` / `desktop_config.json` ←
  桌面端与 chat_server **双写**（格式互通是刻意设计），改结构要两边同步；
- `analyze.py` 输出结构 ← `bh_query` / `report` / `bh_api` / `agent` 全链
  消费：改 summarize/merge_sums 的键名 = 全链改动 + CACHE_VERSION 必须 +1；
- `kb/notes/` ← kb_update 承诺永不覆盖，改 updater 前先确认这条不变；
- 原版目录 `bh-replay-lab\` 与 `<数据目录>\out\` 是**对照基线**，
  不要写入。

**动前必三思的文件**：`bh_replay.py`（解析地基）、`agent.py`（三处防护：
兜底注入/缓存回结果/sanitize，都是真实故障换来的）、`memory_store.py`
（用户真实记忆在里面，改存储结构前先备份 `agent_memory.json`）。

---

## 8. 深度背景资料

- **完整版本史（v2→v17.6，含每个 bug 的根因→修法→回归）**：
  `<项目目录>\V2_CHANGES.md`；
- **格式逆向的 5 个坑**（对象顺序、结尾魔数、byte_align 吞位、被忽略位、
  嘲讽位域冗余零位域——每个都有症状和修法，改 `bh_replay.py` 前必读）：
  `<工作区>\.workbuddy\memory\2026-10-23.md`；
  长期项目记忆：同目录 `MEMORY.md`；
- **C# 参考库**：`<数据目录>\ref\`（allhailcheese/BrawlhallaReplayLibrary）。
  **与 11.00 实际格式有 3 处不符**（对象顺序、结尾魔数、被忽略位），
  只能当线索；真相裁决 = `bh_write.py` 往返校验；
- **游戏机制权威来源**：`<数据目录>\kb\raw\Movement.wikitext`（已存档原文）
  与 brawlhalla.wiki.gg；**注意**：其 Stats 页只列 High/Low 极端名单，
  完整属性在各英雄页 infobox（kb_update 已按此实现）；
- **旧项目（录屏 CV 路线参考）**：
  `C:\Users\用户\WorkBuddy\2026-09-29-23-21-16\brawlhalla-dodge\`
  （`_ground_truth.csv` = 闪避方向人工标注、录屏血条检测方案）；
- **Agent 架构参考**：AstrBot（github.com/AstrBotDevs/AstrBot）的 provider +
  Function Calling 设计、Claude Code 式三层记忆——本项目 agent.py /
  memory_store.py 的思路来源；
- **已知与实测不符的参考**：① C# 参考库的 3 处格式差异（见上）；
  ② wiki.gg Stats 页不能当完整属性表用；③ Brawlhalla 官方 API 的
  `/static/legends/all/` 路径已 404（2026-10-25 实测），legend 名单用
  brawlhalla.wiki.gg/wiki/Legends。

---

## 9. 接手后第一件事

依次跑三条基线命令，确认成绩与文档一致（任何一条不符 = 基线不健康，
先查清再动代码）：

```bat
:: ① 解析地基（预期输出含 "round-trip: 293 identical / 0 mismatch / 0 error"）
cd /d "<项目目录>"
"C:\Users\用户\.workbuddy\binaries\python\versions\3.13.12\python.exe" bh_write.py

:: ② 报告与数据规模（预期 "0.08s" 级缓存命中、208 对手 / 262 场）
"%PY%" -c "import sys,time; sys.path.insert(0,'.'); from report import generate_report; t=time.time(); generate_report(r'%USERPROFILE%/BrawlhallaReplays'); print(f'{time.time()-t:.2f}s')"

:: ③ MCP 服务器（预期 "mcp-handshake: OK | tools: 16"）
"%PY%" - <<'EOF'
import subprocess, json, sys
p = subprocess.Popen([sys.executable, "bh_mcp.py"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
out, _ = p.communicate("\n".join([
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}',
    '{"jsonrpc":"2.0","method":"notifications/initialized"}',
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}']).encode("utf-8"), timeout=120)
tools = [json.loads(l) for l in out.decode().split("\n") if l.strip()]
print("mcp-handshake: OK | tools:", len([r for r in tools if r.get("id")==2][0]["result"]["tools"]))
EOF
```

基线一致后：读 §2 的"判定地基"六条 + §10 的安全/危险划分，再动手。

---

## 10. 与接手者的协作契约

**安全改动**（有校验兜底，直接做）：
- `report.py` / `chat_server.py` 页面与文案（浏览器刷新即验）；
- `bh_api.py` 新增查询函数（不改既有返回结构）；
- `kb/notes/` 用户笔记、`kb/mechanics.json` 追加条目；
- 建议阈值微调（`advice_for` / `advice` 内的数字）。

**危险改动**（按此流程）：
- `bh_replay.py`（解析层）→ 改前跑 `bh_write.py` 存基线成绩 → 小步改 →
  重跑往返校验，**293/293 不回退才算过**；
- `analyze.py` 统计口径 → `cache.py` 的 `CACHE_VERSION` += 1 →
  重算后抽查计数一致性（§3 表）→ 与原数据对比确认变化方向合理；
- `agent.py` 的三处防护（§1 坑 9）→ 必须走**真实 HTTP 路径**回归
  （`_stress_test.py`，14 场景；只测 Agent 直连会漏掉 chat_server 层的
  bug，v17.4 就是这么漏的）；
- `memory_store.py` 存储结构 → 先备份 `agent_memory.json`（用户真实记忆）；
- `chat_session.json` / `desktop_config.json` 结构 → 桌面端与 chat_server
  双写互通，改结构两边同步。

**文档与代码不符时**：以代码为准，改文档，并在文档修正处注明
（本项目惯例：在 V2_CHANGES.md 加一条版本条目说明这次修正）。
