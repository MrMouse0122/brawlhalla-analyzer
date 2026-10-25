# Brawlhalla 对手习惯分析器

解析 Brawlhalla 本地回放，统计每个对手的**真闪避 / dash 位移**习惯与受压反应模式，
生成可视化报告，并附带一个**能记住你训练目标的 AI 打法教练**。

> 数据全部来自本地回放文件解析（293 场实测、逐字节校验），不联网上传任何数据。

## 它能做什么

| 功能 | 说明 |
|---|---|
| 对手画像报告 | 真闪避方向面板、dash 逃逸方向、受压反应、英雄/地图胜率，HTML 一页看完 |
| AI 打法教练 | 自然语言提问，自动调用 16 个本地工具查数据后回答，支持长期记忆 |
| 对手筛选 | "找出受压时往右 dash 逃逸超 40% 的对手"这类条件查询 |
| 单场复盘 | 按 30 秒分段 + 死亡前 5 秒窗口的因果叙事 |
| 游戏知识库 | 闪避帧数 / dash 机制 / 69 英雄武器属性，可从 wiki 更新 |

**口径说明**：Brawlhalla 里地面"闪避键+方向"其实是 **dash（冲刺位移，无无敌帧）**，
真正的闪避只有原地闪与空中闪——本工具严格按此区分，这是与其他分析工具最大的不同。

## 快速开始

### 方式一：直接使用（推荐）

1. 下载 `BrawlhallaAnalyzerV2` 与 `ChatWindow` 两个程序文件夹（见 Releases）；
2. 运行 `BrawlhallaAnalyzerV2.exe`，回放目录默认取
   `%USERPROFILE%\BrawlhallaReplays`（游戏官方回放位置，可在 ⚙ 设置里改）；
3. ⚙ 设置 → AI 服务商选 DeepSeek → 填入
   [API Key](https://platform.deepseek.com/api_keys)，即可使用 AI 教练。

### 方式二：从源码运行

```bat
git clone https://github.com/MrMouse0122/brawlhalla-analyzer.git
cd brawlhalla-analyzer
python bh_desktop.py        :: 桌面软件（需 Python 3.8+，tkinter 标准库自带）
python chat_server.py 8903  :: 或只跑 AI 聊天服务，浏览器访问 127.0.0.1:8903
```

零第三方依赖；AI 教练功能需要一个 OpenAI 兼容 API Key（DeepSeek / OpenAI 均可）。

## AI 教练会记住什么

- **长期记忆**：你的训练目标、英雄偏好、重要结论——跨会话生效，可随时查看与清除；
- **对话历史**：关掉软件不丢，重启自动恢复；
- 所有记忆保存在本地文件里，删掉即彻底清除。

## 数据目录

| 内容 | 默认位置（可用环境变量覆盖） |
|---|---|
| 回放文件 | `%USERPROFILE%\BrawlhallaReplays`（`BH_REPLAY_DIR`） |
| 报告 / 缓存 / 对话 / 记忆 | `%LOCALAPPDATA%\BrawlhallaAnalyzer`（`BH_OUT_DIR`） |
| 游戏知识库 | 数据目录下 `kb\`（`BH_KB_DIR`） |

## 文档

- `HANDOVER.md` — 部署与维护交接（面向后续维护者）
- `V2_CHANGES.md` — 完整版本历史与算法口径修正记录

## 免责

本项目与 Brawlhalla 官方无关；分析结果仅供训练参考。
