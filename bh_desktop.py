#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Brawlhalla 对手闪避习惯分析器 · 桌面版 v6（tkinter，零第三方运行时依赖）

UI：Windows 11 Fluent 布局 + shadcn/ui 中性配色（浅色/深色/跟随系统），
界面语言支持 中文/English（设置面板切换，记忆在 desktop_config.json）。
数据层沿用 v4 口径（真闪避 vs 地面 dash、受压意图四分类、胜负=名次）。
英雄 ID 显示为名字（legends.py，游戏名册顺序，与官方 API 一致）。
"""
import os
import re
import sys
import json
import time
import queue
import ctypes
import datetime
import html as html_mod
import threading
import urllib.request
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import winreg
except ImportError:          # 非 Windows 平台兜底
    winreg = None

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bh_query                      # noqa: E402
import report as bh_report           # noqa: E402
from analyze import aggregate_by_opponent, pct   # noqa: E402
from bh_query import PAD_ORDER, DIR_LABEL   # noqa: E402
from legends import legend_name      # noqa: E402

from bh_paths import replay_dir, out_dir
DEFAULT_REPLAY_DIR = replay_dir()

PAD_ARROWS = {"up_left": "↖", "up": "↑", "up_right": "↗",
              "left": "←", "spot": "○", "right": "→",
              "down_left": "↙", "down": "↓", "down_right": "↘"}

# ---------- 主题（shadcn/ui neutral 令牌） ----------
THEMES = {
    "light": {
        "bg": "#ffffff", "side": "#f5f5f5", "card": "#ffffff",
        "card_hi": "#f0f0f0", "field": "#ffffff",
        "border": "#e5e5e5", "text": "#0a0a0a", "muted": "#737373",
        "accent": "#ea580c", "win": "#15803d", "good": "#0d9488",
        "bad": "#dc2626", "purple": "#ca8a04", "blue": "#3b6a9e",
        "gray": "#d4d4d4", "on_accent": "#ffffff", "bold_hi": "#c2410c",
        "heat": (234, 88, 12),
    },
    "dark": {
        "bg": "#0a0a0a", "side": "#0f0f0f", "card": "#171717",
        "card_hi": "#262626", "field": "#1a1a1a",
        "border": "#2e2e2e", "text": "#fafafa", "muted": "#a3a3a3",
        "accent": "#ff8a3d", "win": "#4ade80", "good": "#34d399",
        "bad": "#f87171", "purple": "#c084fc", "blue": "#60a5fa",
        "gray": "#525252", "on_accent": "#141414", "bold_hi": "#ffc59a",
        "heat": (255, 138, 61),
    },
}

# ---------- 界面文案（中/英） ----------
STR = {
    "zh": {
        "app_title": "对手闪避习惯分析器",
        "replay_dir": "回放目录", "browse": "浏览", "find": "查找",
        "refresh": "刷新数据", "gen_report": "生成报告", "open_report": "打开报告",
        "settings": "设置", "help": "指标说明", "open_dir": "打开目录",
        "opponents": "对手", "col_matches": "局", "col_vs": "对你",
        "you": "（你自己）",
        "select_hint": "选择左侧对手查看画像",
        "loading": "正在解析回放数据，首次约需十几秒…",
        "refreshing": "正在刷新回放数据…",
        "load_fail": "加载失败", "err_title": "出错",
        "dir_missing": "回放目录不存在：\n{d}\n\n请用「浏览」选择正确的目录。",
        "no_report": "还没有报告，请先点「生成报告」。",
        "report_done": "报告已生成：{n_opp} 个对手 / {n_match} 场 -> {path}",
        "report_ask": "完整报告已生成（{n_opp} 个对手 / {n_match} 场）。\n\n现在打开吗？",
        "generating": "正在生成完整报告（走增量缓存）…",
        "status_loaded": "{n} 场有效对局 · {m} 个对手 · 我 = {me} · 总战绩 {w}胜{l}负",
        "match_hint": "匹配到 {n} 个对手", "match_none": "没有匹配 “{t}” 的对手",
        "kpi_dodge_per_min": "真闪避/min", "kpi_dash_per_min": "dash/min",
        "kpi_reactive_rate": "纯受压率", "kpi_reaction": "反应中位",
        "kpi_attacks_per_min": "攻击/min", "kpi_spot": "原地闪",
        "kpi_panic": "连闪率", "kpi_aad": "闪后反打",
        "pad_caption": "真闪避方向（屏幕绝对方向）",
        "cat_title": "真闪避构成", "cat_ground": "地面原地闪", "cat_air": "空中闪",
        "cat_jump": "起跳接闪",
        "intent_title": "受压意图", "int_def": "纯防御", "int_inst": "瞬时按键",
        "int_off": "进攻性", "int_spo": "自发",
        "pdash_line": "受压 dash 逃逸 {p}% · 主要 {d} {dp}%（dash 无无敌帧，可直接打）",
        "tips_title": "怎么打",
        "meta": "{n} 局 · {m:.1f} 分钟{hero}{h2h}",
        "meta_h2h": " · 对你战绩 {w}胜{l}负（胜率 {r}%）",
        "set_title": "设置",
        "set_theme": "主题", "set_light": "浅色", "set_dark": "深色",
        "set_system": "跟随系统",
        "set_lang": "界面语言", "set_note": "设置立即生效并自动保存。",
        "set_provider": "AI 服务商",
        "set_api_base": "接口地址", "set_api_key": "API Key", "set_api_model": "模型",
        "set_key_hint": "（Key 只存在本机 desktop_config.json，不会上传）",
        "tab_profile": "画像",
        "tab_chat": "Agent 对话",
        "chat_title": "Agent 对话",
        "chat_placeholder": "问任何问题…（Enter 发送，Shift+Enter 换行）\n例如：Zett 怎么打 / 对比 Zett 和 JR•KillerBee / 空中闪避冷却几秒 / 我的胜率怎么样",
        "chat_send": "发送", "chat_stop_hint": "回复中…",
        "chat_clear": "清空对话", "chat_clear_ask": "清空当前对话？",
        "chat_resumed": "↩ 已恢复上次对话（上下文保留）",
        "chat_stop": "停止", "chat_stopped": "（已停止）",
        "chat_export": "导出", "chat_exported": "已导出对话：\n{p}",
        "ask_agent": "问 Agent 怎么打 ▸",
        "ask_agent_q": "{name} 怎么打？给我情报卡和针对性建议。",
        "new_matches_hint": "（自上次打开软件后有 {n} 场新对局，可以问我复盘）",
        "webchat_t": "AI 教练在浏览器里",
        "webchat_d": ("完整聊天界面：流式回复、工具过程折叠、markdown 渲染、\n"
                      "长期记忆与桌面软件互通（同一份会话和记忆）。\n"
                      "点下面按钮启动并打开。"),
        "webchat_open": "打开 AI 聊天",
        "webchat_need_key": "⚙ 先配置 AI 服务（点击打开设置）",
        "chat_welcome": ("我是你的 Brawlhalla 打法教练，有你的 262 场对局数据和游戏机制知识库。\n"
                         "我会记住你的训练目标和重要结论（跨会话）。试试问我：\n"
                         "  · Zett 怎么打？给我情报卡\n"
                         "  · 对比 Zett 和 JR • KillerBee\n"
                         "  · 找出受压时往右 dash 逃逸超 40% 的对手\n"
                         "  · 给我一份训练计划，下周帮我检查进度\n"
                         "  · 复盘我最近输的一场"),
        "chat_need_key_t": "还没有配置 AI 服务",
        "chat_need_key_d": "在设置里选一个服务商（推荐 DeepSeek）并填入 API Key，\nAgent 就能自动调用本工具查询对手数据、检索游戏知识库回答问题。\n\nKey 只保存在本机。",
        "chat_goto_settings": "去设置",
        "chat_you": "你", "chat_ai": "助手", "chat_tool": "工具",
        "chat_tool_line": "调用 {name}({args}) → {brief}",
        "chat_ctx": "上下文 {n} 条 · {model}",
        "chat_err_network": "请求失败：{e}\n检查设置里的接口地址/Key/模型名。",
        "btn_ok": "确定", "btn_cancel": "取消",
        "help_title": "指标说明",
        "sample_small": "▸ 真闪避样本偏少（< 15 次），结论仅供参考。",
        "launch": "正在启动…",
        "tab_matches": "对局",
        "matches_title": "对局记录（新 → 旧）· 打完自动出现",
        "m_time": "时间", "m_opp": "对手", "m_res": "结果", "m_dur": "时长",
        "match_hint_none": "选择左侧对局查看本局明细",
        "m_win": "胜", "m_loss": "负",
        "md_map": "地图", "md_me": "我", "md_opp": "对手",
        "kpi_deaths": "死亡次数",
        "md_died": "被击杀",
        "md_timeline": "本局时间轴（攻击密度 + 死亡时刻）",
        "md_lg_me_atk": "▲ 你的攻击",
        "md_lg_opp_atk": "▼ 对手攻击",
        "md_lg_me_die": "✖ 你被击杀",
        "md_lg_opp_die": "✖ 对手被击杀",
        "md_lg_lead": "顶部条=领先方（绿=你 红=对手）",
        "md_lg_scale": "左侧数字=每段攻击次数",
        "md_tl_counts": "{me}攻击 {a} · {opp}攻击 {b}",
        "md_no_detail": "本局明细解析失败（回放文件缺失或损坏）",
    },
    "en": {
        "app_title": "Brawlhalla Dodge Habit Analyzer",
        "replay_dir": "Replay folder", "browse": "Browse", "find": "Find",
        "refresh": "Refresh", "gen_report": "Report", "open_report": "Open report",
        "settings": "Settings", "help": "Metrics", "open_dir": "Open folder",
        "opponents": "Opponents", "col_matches": "Gms", "col_vs": "vs you",
        "you": " (you)",
        "select_hint": "Pick an opponent on the left",
        "loading": "Parsing replays (first run takes a while)…",
        "refreshing": "Refreshing replay data…",
        "load_fail": "Load failed", "err_title": "Error",
        "dir_missing": "Replay folder not found:\n{d}\n\nUse 'Browse' to pick the right folder.",
        "no_report": "No report yet — click 'Report' first.",
        "report_done": "Report ready: {n_opp} opponents / {n_match} matches -> {path}",
        "report_ask": "Full report generated ({n_opp} opponents / {n_match} matches).\n\nOpen it now?",
        "generating": "Generating full report (cached)…",
        "status_loaded": "{n} valid matches · {m} opponents · me = {me} · record {w}W-{l}L",
        "match_hint": "{n} opponents matched", "match_none": "No opponent matches “{t}”",
        "kpi_dodge_per_min": "True dodges/min", "kpi_dash_per_min": "Dashes/min",
        "kpi_reactive_rate": "Pressured rate", "kpi_reaction": "Reaction med.",
        "kpi_attacks_per_min": "Attacks/min", "kpi_spot": "Spot dodge",
        "kpi_panic": "Chained", "kpi_aad": "Counter post-dodge",
        "pad_caption": "True dodges · dashes excluded",
        "cat_title": "True dodge mix", "cat_ground": "Ground spot", "cat_air": "Air",
        "cat_jump": "Jump-dodge",
        "intent_title": "Pressure intent", "int_def": "Defensive", "int_inst": "Simul-press",
        "int_off": "Offensive", "int_spo": "Spontaneous",
        "pdash_line": "Pressured dash escapes {p}% · mainly {d} {dp}% (dashes have no i-frames — punish them)",
        "tips_title": "How to fight",
        "meta": "{n} matches · {m:.1f} min{hero}{h2h}",
        "meta_h2h": " · vs you {w}W-{l}L ({r}% win)",
        "set_title": "Settings",
        "set_theme": "Theme", "set_light": "Light", "set_dark": "Dark",
        "set_system": "Match system",
        "set_lang": "Language", "set_note": "Changes apply immediately and are saved.",
        "set_provider": "AI provider",
        "set_api_base": "Base URL", "set_api_key": "API Key", "set_api_model": "Model",
        "set_key_hint": "(Key stays in local desktop_config.json only)",
        "tab_profile": "Profile",
        "tab_chat": "Agent chat",
        "chat_title": "Agent chat",
        "chat_placeholder": "Ask anything… (Enter to send, Shift+Enter for newline)",
        "chat_send": "Send", "chat_stop_hint": "Replying…",
        "chat_clear": "Clear chat", "chat_clear_ask": "Clear this conversation?",
        "chat_resumed": "↩ Previous session restored (context kept)",
        "chat_stop": "Stop", "chat_stopped": "(stopped)",
        "chat_export": "Export", "chat_exported": "Exported:\n{p}",
        "ask_agent": "Ask Agent ▸",
        "ask_agent_q": "How do I beat {name}? Give me the scout card and counter-tips.",
        "new_matches_hint": "({n} new matches since last launch — ask me to review)",
        "webchat_t": "AI coach lives in the browser",
        "webchat_d": ("Full chat UI: streaming replies, collapsible tool traces,\n"
                      "markdown rendering. Shares memory and session with this app.\n"
                      "Click below to launch."),
        "webchat_open": "Open AI chat",
        "webchat_need_key": "⚙ Configure AI first (opens settings)",
        "chat_welcome": ("I'm your Brawlhalla coach with 262 matches of data and the game mechanics KB.\n"
                         "I remember your training goals across sessions. Try asking:\n"
                         "  · How do I beat Zett?\n"
                         "  · Compare Zett and JR • KillerBee\n"
                         "  · Find opponents escaping right with dash >40% under pressure\n"
                         "  · Make a training plan and check my progress next week\n"
                         "  · Review my latest loss"),
        "chat_need_key_t": "No AI service configured",
        "chat_need_key_d": "Pick a provider in Settings (DeepSeek recommended) and paste an API Key.\nThe agent will then query opponent data and the game knowledge base for you.\n\nThe key stays on this machine.",
        "chat_goto_settings": "Open settings",
        "chat_you": "You", "chat_ai": "Assistant", "chat_tool": "Tool",
        "chat_tool_line": "called {name}({args}) → {brief}",
        "chat_ctx": "{n} msgs · {model}",
        "chat_err_network": "Request failed: {e}\nCheck base URL / key / model in Settings.",
        "btn_ok": "OK", "btn_cancel": "Cancel",
        "help_title": "Metric definitions",
        "sample_small": "▸ Sample too small (< 15 true dodges) — treat as hints only.",
        "launch": "Starting…",
        "tab_matches": "Matches",
        "matches_title": "Match history (new → old) · fresh games appear automatically",
        "m_time": "Time", "m_opp": "Opponent", "m_res": "Result", "m_dur": "Len",
        "match_hint_none": "Pick a match on the left",
        "m_win": "W", "m_loss": "L",
        "md_map": "Map", "md_me": "Me", "md_opp": "Opp",
        "kpi_deaths": "Deaths",
        "md_died": "died",
        "md_timeline": "Match timeline (attack density + deaths)",
        "md_lg_me_atk": "▲ your attacks",
        "md_lg_opp_atk": "▼ opp attacks",
        "md_lg_me_die": "✖ you died",
        "md_lg_opp_die": "✖ opp died",
        "md_lg_lead": "top strip = leader (green=you, red=opp)",
        "md_lg_scale": "left numbers = attacks per bucket",
        "md_tl_counts": "{me} atk {a} · {opp} atk {b}",
        "md_no_detail": "Failed to parse this match (file missing or corrupt)",
    },
}

KPI_KEYS = ["dodge_per_min", "dash_per_min", "reactive_rate", "reaction",
            "attacks_per_min", "spot", "panic", "aad"]

HELP_TEXT = {
    "zh": """指标口径（v4：依据游戏机制修正；回放只存输入流，没有坐标/伤害）：

· 闪避 vs dash：地面按闪避键+方向 = dash（冲刺位移，无无敌帧、
  不算 dodge）。真闪避只有 地面原地闪（16 帧无敌）与 空中闪避（含小跳接闪）。
· 胜负判定：scores 存的是名次（1=第一名），旧版取最高分当赢家导致
  胜负全反，已修正并在 252 场上与"死亡次数更少者"100% 吻合。
· 纯受压闪避率：你出招后 80-400ms 内他的真闪避，剔除
  - <80ms 的瞬时按键（同按/预输入，不是反应）
  - 他自己出招后 300ms 内的位移（追击闪避/dash-in，是进攻不是防御）
· 受压 dash 率：被压时用 dash 逃逸的比例（dash 全程无无敌帧，可直接打）。
· 反应中位：仅纯防御闪避的延迟中位数。
· 英雄名：hero_id 为游戏名册顺序，与官方 API 一致（legends.py 可随时补新英雄）。

数据来源：本地 .replay 文件解析（293/293 字节级往返校验一致）。""",
    "en": """Metric definitions (v4, based on game mechanics; replays store inputs only):

· Dodge vs dash: dodge+direction on the ground = a dash (movement, no
  invulnerability, NOT a dodge). True dodges are ground spot dodges (16f
  i-frames) and air dodges (incl. jump-then-dodge).
· Win/loss: results.scores stores placement (1 = first). The old build picked
  the highest score as winner, inverting every record — fixed, verified 252/252
  against "fewest deaths".
· Pressured-dodge rate: his true dodges within 80-400ms after your attack,
  excluding simul-presses (<80ms) and offensive movement (own attack within
  300ms = chase dodge / dash-in).
· Pressured dash rate: how often he escapes by dash under pressure
  (dashes have no i-frames and can be punished directly).
· Reaction median: median delay of defensive dodges only.
· Legend names: hero_id follows the game roster order, same as the official
  API (append new legends in legends.py).

Source: local .replay parsing (293/293 byte-level roundtrip verified).""",
}


def pick_out_dir():
    """输出目录：BH_OUT_DIR 环境变量优先，否则 %LOCALAPPDATA%\\BrawlhallaAnalyzer
    （由 bh_paths 统一解析，见 bh_paths.py）。"""
    try:
        d = out_dir()
        os.makedirs(d, exist_ok=True)
        return d
    except OSError:
        return _HERE


def system_prefers_dark():
    """读 Windows 深色模式设置（AppsUseLightTheme，0 = 深色）。"""
    if winreg is None:
        return False
    try:
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k)
        return v == 0
    except OSError:
        return False


def _blend(c1, c2, t):
    return "#%02x%02x%02x" % tuple(
        round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def round_rect(cv, x1, y1, x2, y2, r=8, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class App:
    def __init__(self, root):
        self.root = root
        self.out_dir = pick_out_dir()
        cfg = self._load_config()
        self.replay_dir = cfg.get("replay_dir") or DEFAULT_REPLAY_DIR
        self.theme_name = cfg.get("theme", "dark")
        self.lang = cfg.get("lang", "zh")
        self.api_provider = cfg.get("api_provider", "deepseek")
        self.api_base = cfg.get("api_base", "")
        self.api_key = cfg.get("api_key", "")
        self.api_model = cfg.get("api_model", "")
        self.ds = None
        self.agg = {}
        self.me_sum = None
        self.me = None
        self.rows = []
        self.by_name = {}
        self.q = queue.Queue()
        self.busy = False
        self.report_path = os.path.join(self.out_dir, "report.html")
        self.kpi_values = {k: "–" for k in KPI_KEYS}
        self._cur = None
        self._sel_name = None
        self._last_w = None
        self._asking = False
        self._sel_match = None          # 对局页当前选中的 file
        self._cur_match = None          # 供窗口 resize 时重绘节奏图
        self.match_by_file = {}
        self._open_ts = time.time()     # 本次启动时刻（用于标记“刚打完”的对局）
        self._last_snap = None          # 回放目录指纹（自动刷新检测用）
        self._new_matches_done = False  # last_match_count 每次启动只写回一次

        self._apply_window()
        self._build_style()
        self._build_ui()
        self.root.bind("<Configure>", self._redraw_on_resize)
        self._start_load(initial=True)
        self.root.after(120, self._poll)
        self.root.after(60000, self._auto_refresh_poll)

    # ---------------- 配置 ----------------
    def _cfg_path(self):
        return os.path.join(self.out_dir, "desktop_config.json")

    def _load_config(self):
        try:
            with open(self._cfg_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        try:
            cfg = self._load_config()   # 读改写：保留 last_match_count 等非本类键
            cfg.update({"replay_dir": self.replay_dir,
                        "theme": self.theme_name, "lang": self.lang,
                        "api_provider": self.api_provider,
                        "api_base": self.api_base, "api_key": self.api_key,
                        "api_model": self.api_model})
            with open(self._cfg_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except OSError:
            pass

    @property
    def s(self):
        """DPI 缩放系数（1.0 = 96dpi）。像素布局全部乘它，字体用点号自动缩放。"""
        try:
            return max(self.root.winfo_fpixels("1i") / 96.0, 1.0)
        except Exception:
            return 1.0

    def tr(self, key, **kw):
        s = STR.get(self.lang, STR["zh"]).get(key) or STR["zh"].get(key, key)
        return s.format(**kw) if kw else s

    def T(self):
        if self.theme_name == "dark":
            return THEMES["dark"]
        if self.theme_name == "light":
            return THEMES["light"]
        return THEMES["dark" if system_prefers_dark() else "light"]

    def _apply_window(self):
        t = self.T()
        s = self.s
        self.root.title(self.tr("app_title"))
        self.root.geometry(f"{int(1280 * s)}x{int(840 * s)}")
        self.root.minsize(int(1020 * s), int(660 * s))
        self.root.configure(bg=t["bg"])

    # ---------------- 设置 ----------------
    def open_settings(self):
        win = tk.Toplevel(self.root)
        t = self.T()
        win.title(self.tr("set_title"))
        win.configure(bg=t["bg"])
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text=self.tr("set_title"), font=("Segoe UI", 13, "bold"),
                 bg=t["bg"], fg=t["text"], anchor="w").pack(
            fill=tk.X, padx=18, pady=(16, 4))
        tk.Label(win, text=self.tr("set_note"), font=("Segoe UI", 9), bg=t["bg"],
                 fg=t["muted"], anchor="w").pack(fill=tk.X, padx=18, pady=(0, 10))

        tv = tk.StringVar(value=self.theme_name)
        lv = tk.StringVar(value=self.lang)

        def group(label):
            f = tk.Frame(win, bg=t["bg"])
            f.pack(fill=tk.X, padx=18, pady=6)
            tk.Label(f, text=label, font=("Segoe UI", 10, "bold"), bg=t["bg"],
                     fg=t["text"], anchor="w").pack(anchor="w")
            return f

        ft = group(self.tr("set_theme"))
        for val, label in (("light", self.tr("set_light")),
                           ("dark", self.tr("set_dark")),
                           ("system", self.tr("set_system"))):
            tk.Radiobutton(ft, text=label, variable=tv, value=val,
                           bg=t["bg"], fg=t["text"], selectcolor=t["card_hi"],
                           activebackground=t["bg"], activeforeground=t["text"],
                           font=("Segoe UI", 10), anchor="w").pack(anchor="w")
        fl = group(self.tr("set_lang"))
        for val, label in (("zh", "中文"), ("en", "English")):
            tk.Radiobutton(fl, text=label, variable=lv, value=val,
                           bg=t["bg"], fg=t["text"], selectcolor=t["card_hi"],
                           activebackground=t["bg"], activeforeground=t["text"],
                           font=("Segoe UI", 10), anchor="w").pack(anchor="w")
        fa = group(self.tr("set_provider"))
        from agent import PROVIDERS
        keys = list(PROVIDERS.keys())
        self._prov_keys = keys
        self.provider_var = tk.StringVar(
            value=PROVIDERS.get(self.api_provider,
                                PROVIDERS["deepseek"])["label"])
        self.api_base_var = tk.StringVar(value=self.api_base)
        self.api_key_var = tk.StringVar(value=self.api_key)
        self.api_model_var = tk.StringVar(value=self.api_model)

        def on_prov_change(_e=None):
            key = keys[[PROVIDERS[k]["label"] for k in keys].index(
                self.provider_var.get())]
            if key != "custom":
                self.api_base_var.set(PROVIDERS[key]["base_url"])
                self.api_model_var.set(PROVIDERS[key]["model"])

        cb = ttk.Combobox(fa, textvariable=self.provider_var, state="readonly",
                          values=[PROVIDERS[k]["label"] for k in keys],
                          width=22, font=("Segoe UI", 10))
        cb.pack(anchor="w", pady=(0, 4))
        cb.bind("<<ComboboxSelected>>", on_prov_change)
        for label, var, show in ((self.tr("set_api_base"), self.api_base_var, None),
                                 (self.tr("set_api_key"), self.api_key_var, "•"),
                                 (self.tr("set_api_model"), self.api_model_var, None)):
            tk.Label(fa, text=label, font=("Segoe UI", 9), bg=t["bg"],
                     fg=t["muted"], anchor="w").pack(anchor="w")
            ttk.Entry(fa, textvariable=var, width=34, show=show or "").pack(
                anchor="w", pady=(0, 4))
        tk.Label(fa, text=self.tr("set_key_hint"), font=("Segoe UI", 8),
                 bg=t["bg"], fg=t["muted"], anchor="w").pack(anchor="w")

        btns = tk.Frame(win, bg=t["bg"])
        btns.pack(fill=tk.X, padx=18, pady=(12, 16))

        def on_ok():
            self.theme_name, self.lang = tv.get(), lv.get()
            try:
                self.api_provider = self._prov_keys[
                    [PROVIDERS[k]["label"] for k in self._prov_keys].index(
                        self.provider_var.get())]
            except (ValueError, AttributeError):
                self.api_provider = "deepseek"
            self.api_base = self.api_base_var.get().strip()
            self.api_key = self.api_key_var.get().strip()
            self.api_model = self.api_model_var.get().strip()
            self._save_config()
            win.destroy()
            self._refresh_chrome()

        tk.Button(btns, text=self.tr("btn_ok"), command=on_ok,
                  bg=t["accent"], fg=t["on_accent"], relief=tk.FLAT,
                  font=("Segoe UI", 10), padx=18, pady=4).pack(side=tk.RIGHT)
        tk.Button(btns, text=self.tr("btn_cancel"), command=win.destroy,
                  bg=t["card"], fg=t["text"], relief=tk.FLAT,
                  font=("Segoe UI", 10), padx=14, pady=4).pack(side=tk.RIGHT,
                                                               padx=(0, 8))
        win.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + 120
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _refresh_chrome(self):
        """主题/语言变更后重建 UI（保留已加载数据与当前选择）。"""
        sel = self._sel_name
        self._cur = None
        for w in self.root.winfo_children():
            w.destroy()
        self._apply_window()
        self._build_style()
        self._build_ui()
        if self.rows:
            self._fill_tree(self.rows)
            if sel and sel in self.by_name:
                self.tree.selection_set(sel)
            elif self.tree.get_children():
                self.tree.selection_set(self.tree.get_children()[0])
        if self.ds:
            self._fill_match_list()
            if self._sel_match and self._sel_match in self.match_by_file:
                self.mtree.selection_set(self._sel_match)
            elif self.match_by_file:
                self.mtree.selection_set(next(iter(self.match_by_file)))

    # ---------------- 样式 ----------------
    def _build_style(self):
        t = self.T()
        s = self.s
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background=t["bg"])
        st.configure("Side.TFrame", background=t["side"])
        st.configure("TLabel", background=t["bg"], foreground=t["text"],
                     font=("Segoe UI", 10))
        st.configure("Side.TLabel", background=t["side"], foreground=t["muted"],
                     font=("Segoe UI", 9))
        st.configure("Muted.TLabel", background=t["bg"], foreground=t["muted"],
                     font=("Segoe UI", 9))
        st.configure("Title.TLabel", background=t["bg"], foreground=t["text"],
                     font=("Segoe UI", 13, "bold"))
        st.configure("TButton", font=("Segoe UI", 10), padding=(int(14 * s), int(6 * s)),
                     background=t["card"], foreground=t["text"],
                     bordercolor=t["border"], lightcolor=t["card"],
                     darkcolor=t["card"], relief="flat")
        st.map("TButton",
               background=[("active", t["card_hi"]), ("pressed", t["border"])],
               foreground=[("active", t["text"])])
        st.configure("TEntry", fieldbackground=t["field"], foreground=t["text"],
                     insertcolor=t["text"], bordercolor=t["border"],
                     lightcolor=t["field"], darkcolor=t["field"],
                     padding=int(5 * s))
        st.configure("Treeview", font=("Segoe UI", 10), rowheight=int(34 * s),
                     background=t["side"], fieldbackground=t["side"],
                     foreground=t["text"], borderwidth=0,
                     lightcolor=t["side"], darkcolor=t["side"])
        st.configure("Treeview.Heading", font=("Segoe UI", 9),
                     padding=(int(8 * s), int(5 * s)),
                     background=t["side"], foreground=t["muted"], borderwidth=0,
                     relief="flat")
        st.map("Treeview", background=[("selected", t["card_hi"])],
               foreground=[("selected", t["text"])])
        st.configure("Vertical.TScrollbar", background=t["side"],
                     troughcolor=t["side"], bordercolor=t["side"],
                     arrowcolor=t["muted"])

    # ---------------- UI ----------------
    def _build_ui(self):
        t = self.T()
        s = self.s
        top = tk.Frame(self.root, bg=t["bg"], height=int(56 * s))
        top.pack(fill=tk.X, padx=int(16 * s), pady=(int(12 * s), int(6 * s)))
        top.pack_propagate(False)

        tk.Label(top, text=self.tr("app_title"), font=("Segoe UI", 14, "bold"),
                 bg=t["bg"], fg=t["text"]).pack(side=tk.LEFT)
        tk.Label(top, text="v4", font=("Segoe UI", 9, "bold"), bg=t["accent"],
                 fg=t["on_accent"], padx=6, pady=1).pack(side=tk.LEFT,
                                                        padx=(8, 14), pady=(4, 0))
        tk.Label(top, text=self.tr("replay_dir"), bg=t["bg"], fg=t["muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=self.replay_dir)
        ttk.Entry(top, textvariable=self.dir_var, width=32).pack(
            side=tk.LEFT, padx=(6, 4))
        ttk.Button(top, text=self.tr("browse"), command=self._browse_dir,
                   width=6).pack(side=tk.LEFT)

        self.search_var = tk.StringVar()
        se = ttk.Entry(top, textvariable=self.search_var, width=16)
        se.pack(side=tk.LEFT, padx=(14, 4))
        se.bind("<KeyRelease>", lambda ev: self._search())
        ttk.Button(top, text=self.tr("find"), command=self._search,
                   width=6).pack(side=tk.LEFT)
        ttk.Button(top, text=self.tr("refresh"),
                   command=lambda: self._start_load(), width=9).pack(
            side=tk.LEFT, padx=(10, 0))
        ttk.Button(top, text="⚙ " + self.tr("settings"),
                   command=self.open_settings, width=9).pack(side=tk.RIGHT)
        ttk.Button(top, text=self.tr("gen_report"), command=self._gen_report,
                   width=9).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(top, text=self.tr("open_report"), command=self._open_report,
                   width=10).pack(side=tk.RIGHT, padx=(0, 6))

        body = tk.Frame(self.root, bg=t["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=int(16 * s))
        body.columnconfigure(1, weight=1, minsize=int(640 * s))
        body.rowconfigure(0, weight=1)

        side = tk.Frame(body, bg=t["side"], highlightbackground=t["border"],
                        highlightthickness=1)
        side.grid(row=0, column=0, sticky="nsw")
        side.rowconfigure(1, weight=1)
        side.columnconfigure(0, weight=1)
        tk.Label(side, text=self.tr("opponents"), font=("Segoe UI", 10, "bold"),
                 bg=t["side"], fg=t["muted"], anchor="w", padx=int(14 * s),
                 pady=int(6 * s)).grid(row=0, column=0, sticky="ew")
        tree_wrap = tk.Frame(side, bg=t["side"])
        tree_wrap.grid(row=1, column=0, sticky="nsew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_wrap, columns=("m", "wl"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("m", text=self.tr("col_matches"))
        self.tree.heading("wl", text=self.tr("col_vs"))
        self.tree.column("#0", width=int(170 * s), minwidth=int(150 * s),
                         anchor="w", stretch=True)
        self.tree.column("m", width=int(40 * s), minwidth=int(40 * s),
                         anchor="e", stretch=False)
        self.tree.column("wl", width=int(64 * s), minwidth=int(64 * s),
                         anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("me", font=("Segoe UI", 10, "bold"))
        self.tree.bind("<<TreeviewSelect>>", lambda ev: self._on_select())

        # ---- 右侧：标签页（画像 / Agent 对话）----
        st = ttk.Style()
        st.configure("Chat.TNotebook", background=t["bg"], borderwidth=0,
                     tabmargins=(0, 4, 0, 0))
        st.configure("Chat.TNotebook.Tab", background=t["bg"],
                     foreground=t["muted"], font=("Segoe UI", 10), padding=(14, 6))
        st.map("Chat.TNotebook.Tab",
               background=[("selected", t["card"])],
               foreground=[("selected", t["text"])])
        content = ttk.Notebook(body, style="Chat.TNotebook")
        content.grid(row=0, column=1, sticky="nsew", padx=(int(14 * s), 0))
        self.notebook = content

        tab_profile = tk.Frame(content, bg=t["bg"])
        content.add(tab_profile, text=f"  {self.tr('tab_profile')}  ")
        tab_matches = tk.Frame(content, bg=t["bg"])
        content.add(tab_matches, text=f"  {self.tr('tab_matches')}  ")
        tab_chat = tk.Frame(content, bg=t["bg"])
        content.add(tab_chat, text=f"  {self.tr('tab_chat')}  ")
        self._build_matches_tab(tab_matches)
        self._build_chat_tab(tab_chat)

        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)

        head = tk.Frame(tab_profile, bg=t["bg"])
        head.grid(row=0, column=0, sticky="ew")
        self.name_var = tk.StringVar(value=self.tr("select_hint"))
        tk.Label(head, textvariable=self.name_var, font=("Segoe UI", 15, "bold"),
                 bg=t["bg"], fg=t["text"], anchor="w").pack(side=tk.LEFT)
        self.ask_link_btn = tk.Button(
            head, text=" " + self.tr("ask_agent") + " ", font=("Segoe UI", 9, "bold"),
            bg=t["card"], fg=t["accent"], relief=tk.FLAT, bd=0,
            activebackground=t["card_hi"], activeforeground=t["accent"],
            command=self._ask_about_current, cursor="hand2")
        self.ask_link_btn.pack(side=tk.RIGHT, pady=(2, 0))
        self.meta_var = tk.StringVar(value=" ")
        tk.Label(tab_profile, textvariable=self.meta_var, font=("Segoe UI", 9),
                 bg=t["bg"], fg=t["muted"], anchor="w").grid(row=1, column=0,
                                                             sticky="ew",
                                                             pady=(2, int(10 * s)))

        self.kpi_cv = tk.Canvas(tab_profile, height=int(150 * s), bg=t["bg"],
                                highlightthickness=0)
        self.kpi_cv.grid(row=2, column=0, sticky="ew")
        self.kpi_cv.bind("<Configure>", lambda ev: self._draw_kpis())

        charts = tk.Frame(tab_profile, bg=t["bg"])
        charts.grid(row=3, column=0, sticky="nsew", pady=(int(10 * s), 0))
        charts.columnconfigure(0, weight=0)
        charts.columnconfigure(1, weight=1)
        charts.rowconfigure(0, weight=1)

        self.pad = tk.Canvas(charts, width=int(300 * s), height=int(330 * s),
                             bg=t["bg"], highlightthickness=0)
        self.pad.grid(row=0, column=0, sticky="nw", padx=(0, int(14 * s)))

        rc = tk.Frame(charts, bg=t["bg"])
        rc.grid(row=0, column=1, sticky="nsew")
        rc.columnconfigure(0, weight=1)
        rc.rowconfigure(2, weight=1)
        self.bars = tk.Canvas(rc, height=int(138 * s), bg=t["bg"],
                              highlightthickness=0)
        self.bars.grid(row=0, column=0, sticky="ew")
        self.bars.bind("<Configure>", lambda ev: self._draw_bars())
        tk.Label(rc, text=self.tr("tips_title"), font=("Segoe UI", 11, "bold"),
                 bg=t["bg"], fg=t["text"], anchor="w").grid(row=1, column=0,
                                                            sticky="ew",
                                                            pady=(10, 2))
        self.tips = tk.Text(rc, font=("Segoe UI", 10), wrap=tk.WORD, bg=t["bg"],
                            fg=t["text"], relief=tk.FLAT, padx=2, pady=2,
                            state=tk.DISABLED, height=8, cursor="arrow",
                            spacing2=2, spacing3=4, borderwidth=0,
                            highlightthickness=0)
        self.tips.grid(row=2, column=0, sticky="nsew")
        t_sb = ttk.Scrollbar(rc, orient=tk.VERTICAL, command=self.tips.yview)
        t_sb.grid(row=2, column=1, sticky="ns")
        self.tips.configure(yscrollcommand=t_sb.set)
        self.tips.tag_configure("bullet", foreground=t["accent"])
        self.tips.tag_configure("bold", foreground=t["bold_hi"],
                                font=("Segoe UI", 10, "bold"))
        self.tips.tag_configure("plain", foreground=t["text"])
        self.tips.tag_configure("muted", foreground=t["muted"],
                                font=("Segoe UI", 9))

        status = tk.Frame(self.root, bg=t["bg"])
        status.pack(fill=tk.X, padx=int(16 * s), pady=(int(4 * s), int(8 * s)))
        self.status_var = tk.StringVar(value=self.tr("launch"))
        tk.Label(status, textvariable=self.status_var, font=("Segoe UI", 9),
                 bg=t["bg"], fg=t["muted"], anchor="w").pack(side=tk.LEFT)
        self.cache_var = tk.StringVar(value="")
        tk.Label(status, textvariable=self.cache_var, font=("Segoe UI", 9),
                 bg=t["bg"], fg=t["muted"], anchor="e").pack(side=tk.RIGHT)
        ttk.Button(status, text=self.tr("help"), command=self._help,
                   width=9).pack(side=tk.RIGHT, padx=(0, 14))
        ttk.Button(status, text=self.tr("open_dir"), command=self._open_replay_dir,
                   width=10).pack(side=tk.RIGHT)

    # ---------------- 重绘 ----------------
    def _redraw_on_resize(self, *_):
        w = self.root.winfo_width()
        if w == self._last_w:
            return
        self._last_w = w
        if self._cur:
            self._draw_kpis()
            self._draw_bars()

    def _draw_kpis(self):
        t = self.T()
        s = self.s
        cv = self.kpi_cv
        cv.delete("all")
        w = max(cv.winfo_width(), int(480 * s))
        gap = int(10 * s)
        cols = max(3, min(5, (w - gap) // int(190 * s)))
        cw = (w - gap * (cols - 1)) // cols
        ch = int(66 * s)
        rows = (len(KPI_KEYS) + cols - 1) // cols
        need_h = rows * (ch + gap) + int(4 * s)
        if int(cv.cget("height")) != need_h:
            cv.configure(height=need_h)
        for idx, key in enumerate(KPI_KEYS):
            r, c = divmod(idx, cols)
            x0 = c * (cw + gap)
            y0 = r * (ch + gap)
            round_rect(cv, x0, y0, x0 + cw, y0 + ch, r=int(10 * s),
                       fill=t["card"], outline=t["border"])
            cv.create_text(x0 + int(14 * s), y0 + int(12 * s), anchor="nw",
                           text=self.kpi_values[key],
                           font=("Consolas", 13, "bold"), fill=t["text"])
            cv.create_text(x0 + int(14 * s), y0 + int(40 * s), anchor="nw",
                           text=self.tr("kpi_" + key),
                           font=("Segoe UI", 9), fill=t["muted"])

    def _draw_pad(self, dir_counts, total):
        t = self.T()
        s = self.s
        c = self.pad
        c.delete("all")
        counts = {k: dir_counts.get(k, 0) for k in PAD_ORDER}
        top = max(counts.values()) if counts else 0
        cell = int(86 * s)
        gap = int(8 * s)
        base = _hex2rgb(t["card"])
        acc = t["heat"]
        for idx, k in enumerate(PAD_ORDER):
            r, col = divmod(idx, 3)
            x0 = int(10 * s) + col * (cell + gap)
            y0 = int(10 * s) + r * (cell + gap)
            v = counts[k]
            frac = (v / top) if top else 0.0
            fill_rgb = tuple(round(base[i] + (acc[i] - base[i]) * (0.06 + 0.80 * frac))
                             for i in range(3))
            fill = "#%02x%02x%02x" % fill_rgb
            # 按填充亮度自动选黑/白文字，两种主题都保持对比度
            lum = 0.299 * fill_rgb[0] + 0.587 * fill_rgb[1] + 0.114 * fill_rgb[2]
            fg_hi = "#1a1a1a" if lum > 140 else "#ffffff"
            fg_lo = "#6b6b6b" if lum > 140 else "#e8e8e8"
            round_rect(c, x0, y0, x0 + cell, y0 + cell, r=int(10 * s), fill=fill,
                       outline=t["border"])
            c.create_text(x0 + cell / 2, y0 + cell / 2 - int(14 * s),
                          text=PAD_ARROWS[k], font=("Segoe UI Symbol", 15),
                          fill=fg_hi)
            c.create_text(x0 + cell / 2, y0 + cell / 2 + int(14 * s),
                          text=f"{pct(v, total):.1f}%",
                          font=("Consolas", 11, "bold"), fill=fg_hi)
            c.create_text(x0 + cell / 2, y0 + cell / 2 + int(31 * s),
                          text=str(v), font=("Consolas", 8), fill=fg_lo)
        c.create_text(int(150 * s), int(330 * s) - int(4 * s), anchor="s",
                      text=self.tr("pad_caption"),
                      font=("Segoe UI", 9), fill=t["muted"])

    def _draw_bars(self):
        t = self.T()
        s = self.s
        cv = self.bars
        cv.delete("all")
        w = max(int(cv.winfo_width()), int(380 * s))
        s_n = (self._cur[1] or 1) if self._cur else 1
        s_s = self._cur[0] if self._cur else {}
        n = s_n
        y = 0
        bar_h = int(12 * s)

        def stack(items, label):
            nonlocal y
            x = 0
            for _, v, color in items:
                seg = w * v / n if n else 0
                if seg > 0:
                    cv.create_rectangle(x, y, x + seg, y + bar_h, fill=color,
                                        width=0)
                    x += seg
            cv.create_text(0, y + bar_h + int(4 * s), anchor="nw",
                           text=label + "   " + "   ".join(
                               f"{lab} {pct(v, n)}%" for lab, v, _ in items),
                           font=("Segoe UI", 9), fill=t["muted"])
            y += int(44 * s)

        stack([(self.tr("cat_ground"), s_s.get("dodges_ground", 0), t["blue"]),
               (self.tr("cat_air"), s_s.get("dodges_air", 0), t["purple"]),
               (self.tr("cat_jump"), s_s.get("dodges_jump", 0), t["good"])],
              self.tr("cat_title"))
        stack([(self.tr("int_def"), s_s.get("reactive", 0), t["good"]),
               (self.tr("int_inst"), s_s.get("reactive_instant", 0), t["purple"]),
               (self.tr("int_off"), s_s.get("reactive_offensive", 0), t["bad"]),
               (self.tr("int_spo"), s_s.get("spontaneous", 0), t["gray"])],
              self.tr("intent_title"))
        pd = s_s.get("pressure_dash", 0)
        if pd and s_s.get("dashes"):
            pdd = s_s.get("pressure_dash_dir") or {}
            top = max(pdd.items(), key=lambda kv: kv[1])
            cv.create_text(0, y, anchor="nw",
                           text=self.tr("pdash_line",
                                        p=s_s.get("pressure_dash_rate", 0),
                                        d=self.tr_dir(top[0]),
                                        dp=pct(top[1], pd)),
                           font=("Segoe UI", 9), fill=t["win"])

    # ---------------- 数据加载 ----------------
    def _set_busy(self, busy):
        self.busy = busy

    def _start_load(self, initial=False, quiet=False):
        if self.busy:
            return
        if not os.path.isdir(self.replay_dir):
            messagebox.showerror("Error", self.tr("dir_missing", d=self.replay_dir))
            return
        self._set_busy(True)
        self.status_var.set(self.tr("loading" if initial else "refreshing"))
        if not quiet:               # 自动刷新走静默：不清空当前画面
            self.cache_var.set("")
            self._show_placeholder()
        self._save_config()
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        try:
            bh_query.REPLAY_DIR = self.replay_dir
            bh_query.OUT_DIR = self.out_dir
            # 同步到环境变量：内嵌聊天服务(bh_api._ensure)每次按 env 取回放目录，
            # 不同步的话，用户改过目录后 GUI 与 AI 聊天会各读一个数据源
            os.environ["BH_REPLAY_DIR"] = self.replay_dir
            os.environ["BH_OUT_DIR"] = self.out_dir
            ds, stats = bh_query.load_dataset()
            agg, me_sum = aggregate_by_opponent(ds)
            self.q.put(("ok", ds, agg, me_sum, ds.get("me"), stats))
        except Exception as e:  # noqa: BLE001
            self.q.put(("err", f"{type(e).__name__}: {e}"))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "ok":
                    self._on_loaded(msg[1:])
                elif kind == "err":
                    self._set_busy(False)
                    self.status_var.set(self.tr("load_fail"))
                    messagebox.showerror(self.tr("err_title"), msg[1])
                elif kind == "report_ok":
                    self._on_report_done(msg[1:])
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _on_loaded(self, payload):
        ds, agg, me_sum, me, stats = payload
        self.ds, self.agg, self.me_sum, self.me = ds, agg, me_sum, me
        self._set_busy(False)
        wins = sum(e["wins"] for e in agg.values())
        losses = sum(e["losses"] for e in agg.values())
        self.status_var.set(self.tr("status_loaded", n=len(ds["matches"]),
                                    m=len(agg), me=me, w=wins, l=losses))
        self.cache_var.set(stats)
        self._last_snap = self._dir_snapshot()
        self._tl_cache = {}     # 单场明细缓存随数据集一并失效
        self._tl = None
        self._rebuild_rows()
        self._fill_match_list()
        self._chat_update_gate()   # 数据就绪后更新面板状态
        if not self._new_matches_done:
            self._new_matches_done = True
            self._new_matches_count()  # 每次启动写回一次（驱动聊天页“N 场新对局”）
        # 聊天服务已在跑时同步刷新其数据源（自动抓到新对局后 Agent 也能查到）
        if "bh_api" in sys.modules:
            try:
                sys.modules["bh_api"].refresh()
            except Exception:  # noqa: BLE001
                pass
        # 已配置 AI 时自动启动聊天服务（浏览器访问 http://127.0.0.1:8903）
        if self.api_key and not self._server_alive():
            threading.Thread(target=self._open_webchat_quiet,
                             daemon=True).start()
        # 恢复选中项（自动刷新不打断正在看的内容）
        kids = self.tree.get_children()
        if self._sel_name and self._sel_name in kids:
            self.tree.selection_set(self._sel_name)
        elif kids:
            self.tree.selection_set(kids[0])
        if self._sel_match and self._sel_match in self.match_by_file:
            self.mtree.selection_set(self._sel_match)
        elif self.match_by_file:
            self.mtree.selection_set(next(iter(self.match_by_file)))

    def _open_webchat_quiet(self):
        """起服务但不弹浏览器。"""
        try:
            os.environ["BH_OUT_DIR"] = self.out_dir
            import chat_server as cs
            cs.run_server(8903)
        except Exception:  # noqa: BLE001
            pass

    def _rebuild_rows(self):
        self.rows = []
        self.by_name = {}
        if self.me and self.me_sum:
            e = {"name": self.me, "matches": self.me_sum.get("_matches", 1),
                 "wins": None, "losses": None, "minutes": self.me_sum["minutes"],
                 "sum": self.me_sum, "heroes": {}, "maps": {}, "history": []}
            self.rows.append(("me", self.me, e))
            self.by_name[self.me] = e
        ranked = sorted(self.agg.items(),
                        key=lambda kv: (-kv[1]["matches"], kv[0].lower()))
        for name, e in ranked:
            self.rows.append(("opp", name, e))
            self.by_name[name] = e
        self._fill_tree(self.rows)

    def tr_dir(self, key):
        """方向标签：中文用 DIR_LABEL，英文用箭头+英文词。"""
        if self.lang == "en":
            names = {"up_left": "Up-Left", "up": "Up", "up_right": "Up-Right",
                     "left": "Left", "spot": "Spot", "right": "Right",
                     "down_left": "Down-Left", "down": "Down",
                     "down_right": "Down-Right"}
            return f"{PAD_ARROWS.get(key, '')} {names.get(key, key)}"
        return DIR_LABEL.get(key, key)

    def _fill_tree(self, rows):
        self.tree.delete(*self.tree.get_children())
        for kind, name, e in rows:
            if e.get("wins") is None:
                wl = "--"
            elif self.lang == "en":
                wl = f"{e['wins']}W-{e['losses']}L"
            else:
                wl = f"{e['wins']}胜{e['losses']}负"
            label = f"★ {name}{self.tr('you')}" if kind == "me" else name
            self.tree.insert("", tk.END, iid=name, text=label,
                             values=(e["matches"], wl),
                             tags=("me",) if kind == "me" else ())

    # ---------------- 画像显示 ----------------
    def _on_select(self, *_):
        if self.busy:
            return
        sel = self.tree.selection()
        if not sel:
            return
        e = self.by_name.get(sel[0])
        if not e:
            return
        self._sel_name = sel[0]
        s = e["sum"]
        is_me = sel[0] == self.me
        n = s.get("dodges", 0) or 1
        self._cur = (s, n, is_me)

        self.name_var.set(sel[0] + (self.tr("you") if is_me else ""))
        heroes = e.get("heroes") or {}
        hero_s = ""
        if heroes:
            top = max(heroes, key=heroes.get)
            hero_s = f" · {legend_name(top)} (ID {top})"
        if is_me or e.get("wins") is None:
            wl_s = ""
        else:
            total_wl = e["wins"] + e["losses"]
            wr = pct(e["wins"], total_wl) if total_wl else None
            wl_s = self.tr("meta_h2h", w=e["wins"], l=e["losses"],
                           r=wr if wr is not None else 0)
        self.meta_var.set(self.tr("meta", n=e["matches"], m=e["minutes"],
                                  hero=hero_s, h2h=wl_s))

        self.kpi_values = {
            "dodge_per_min": f"{s.get('dodge_per_min', 0):g}",
            "dash_per_min": f"{s.get('dash_per_min', 0):g}",
            "reactive_rate": f"{s.get('reactive_rate', 0):g}%",
            "reaction": (f"{s['reaction_median_ms']}ms"
                         if s.get("reaction_median_ms") is not None else "–"),
            "attacks_per_min": f"{s.get('attacks_per_min', 0):g}",
            "spot": f"{pct(s.get('dir_counts', {}).get('spot', 0), n):g}%",
            "panic": f"{s.get('panic_rate', 0):g}%",
            "aad": f"{pct(s.get('attack_after_dodge', 0), n):g}%",
        }
        self._draw_kpis()
        self._draw_pad(s.get("dir_counts", {}), n)
        self._draw_bars()
        self._set_tips(s, is_opponent=not is_me)

    def _set_tips(self, s, is_opponent):
        self.tips.configure(state=tk.NORMAL)
        self.tips.delete("1.0", tk.END)
        if s.get("dodges", 0) < 15:
            self.tips.insert(tk.END, self.tr("sample_small") + "\n", "muted")
        for tip in bh_report.advice_for(s, is_opponent=is_opponent,
                                        lang=self.lang):
            self._insert_tip(tip)
        self.tips.configure(state=tk.DISABLED)

    def _insert_tip(self, tip_html):
        parts = re.split(r"(<b>.*?</b>)", tip_html)
        self.tips.insert(tk.END, "▸ ", "bullet")
        for p in parts:
            if p.startswith("<b>") and p.endswith("</b>"):
                self.tips.insert(tk.END, p[3:-4], "bold")
            elif p:
                plain = html_mod.unescape(re.sub(r"<[^>]+>", "", p))
                self.tips.insert(tk.END, plain, "plain")
        self.tips.insert(tk.END, "\n")

    def _show_placeholder(self):
        t = self.T()
        self.name_var.set(self.tr("select_hint"))
        self.meta_var.set(" ")
        self.kpi_values = {k: "–" for k in KPI_KEYS}
        self._cur = None
        self._draw_kpis()
        self.pad.delete("all")
        self.bars.delete("all")
        self.tips.configure(state=tk.NORMAL)
        self.tips.delete("1.0", tk.END)
        self.tips.insert(tk.END, self.tr("loading"), "muted")
        self.tips.configure(state=tk.DISABLED)

    # ---------------- 交互 ----------------
    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.replay_dir or "C:/",
                                    title=self.tr("replay_dir"))
        if d:
            self.replay_dir = d.replace("/", "\\")
            self.dir_var.set(self.replay_dir)
            self._start_load(initial=True)

    def _search(self, *_):
        term = self.search_var.get().strip().lower()
        if not term:
            self._fill_tree(self.rows)
            return
        hits = [(k, nm, e) for k, nm, e in self.rows if term in nm.lower()]
        if not hits:
            self.status_var.set(self.tr("match_none", t=term))
            return
        self._fill_tree(hits)
        self.tree.selection_set(hits[0][1])
        self.status_var.set(self.tr("match_hint", n=len(hits)))

    def _gen_report(self):
        if self.busy:
            return
        if not os.path.isdir(self.replay_dir):
            messagebox.showerror("Error", self.tr("dir_missing", d=self.replay_dir))
            return
        self._set_busy(True)
        self.status_var.set(self.tr("generating"))
        threading.Thread(target=self._report_worker, daemon=True).start()

    def _report_worker(self):
        try:
            bh_report.OUT_DIR = self.out_dir
            ds = self.ds
            if ds is None:
                bh_query.REPLAY_DIR = self.replay_dir
                bh_query.OUT_DIR = self.out_dir
                ds, _ = bh_query.load_dataset()
            agg, me_sum = aggregate_by_opponent(ds)
            path = os.path.join(self.out_dir, "report.html")
            bh_report.render(ds, agg, me_sum, path)
            self.q.put(("report_ok", path, len(agg), len(ds["matches"])))
        except Exception as e:  # noqa: BLE001
            self.q.put(("err", f"{type(e).__name__}: {e}"))

    def _on_report_done(self, payload):
        path, n_opp, n_match = payload
        self._set_busy(False)
        self.report_path = path
        self.status_var.set(self.tr("report_done", n_opp=n_opp, n_match=n_match,
                                    path=path))
        self.cache_var.set(time.strftime("%H:%M:%S"))
        if messagebox.askyesno(self.tr("app_title"),
                               self.tr("report_ask", n_opp=n_opp,
                                       n_match=n_match)):
            self._open_report()

    def _open_report(self):
        if not os.path.isfile(self.report_path):
            messagebox.showinfo(self.tr("app_title"), self.tr("no_report"))
            return
        webbrowser.open("file:///" + self.report_path.replace("\\", "/"))

    def _open_replay_dir(self):
        if os.path.isdir(self.replay_dir):
            os.startfile(self.replay_dir)  # noqa: S606
        else:
            messagebox.showerror("Error", self.replay_dir)

    def _help(self):
        messagebox.showinfo(self.tr("help_title"), HELP_TEXT[self.lang])

    # ---------------- Agent 对话（浏览器版：桌面端只做启动面板） ----------------
    def _build_chat_tab(self, parent):
        t = self.T()
        s = self.s
        self._chat_folds = []
        self._fold_seq = 0
        self.chat_turns = []
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        tk.Label(parent, text=self.tr("chat_title"), font=("Segoe UI", 13, "bold"),
                 bg=t["bg"], fg=t["text"]).grid(row=0, column=0, sticky="w",
                                                pady=(int(8 * s), int(4 * s)))
        card = tk.Frame(parent, bg=t["card"], highlightbackground=t["border"],
                        highlightthickness=1)
        card.grid(row=1, column=0, sticky="nsew")
        inner = tk.Frame(card, bg=t["card"])
        inner.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(inner, text="⚔", font=("Segoe UI", 34), bg=t["card"],
                 fg=t["accent"]).pack()
        tk.Label(inner, text=self.tr("webchat_t"), font=("Segoe UI", 14, "bold"),
                 bg=t["card"], fg=t["text"]).pack(pady=(int(6 * s), 0))
        tk.Label(inner, text=self.tr("webchat_d"), font=("Segoe UI", 10),
                 bg=t["card"], fg=t["muted"], justify=tk.LEFT,
                 wraplength=int(420 * s)).pack(pady=int(8 * s))
        btnrow = tk.Frame(inner, bg=t["card"])
        btnrow.pack(pady=int(6 * s))
        self.webchat_btn = tk.Button(
            btnrow, text="  " + self.tr("webchat_open") + "  ",
            font=("Segoe UI", 11, "bold"), bg=t["accent"], fg=t["on_accent"],
            relief=tk.FLAT, padx=int(20 * s), pady=int(8 * s), bd=0,
            activebackground=t["card_hi"], activeforeground=t["text"],
            command=self._open_webchat, cursor="hand2")
        self.webchat_btn.pack()
        self.webchat_url_var = tk.StringVar(value="http://127.0.0.1:8903/")
        tk.Label(inner, textvariable=self.webchat_url_var,
                 font=("Consolas", 9), bg=t["card"], fg=t["muted"]).pack(
            pady=(int(8 * s), 0))
        self._chat_update_gate()

    # ---------------- 对局页（按时间顺序的单场记录） ----------------
    def _build_matches_tab(self, parent):
        t = self.T()
        s = self.s
        self.match_by_file = {}
        self._cur_match = None
        parent.columnconfigure(0, weight=0)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)

        # 左：时间序列表（新 → 旧）
        left = tk.Frame(parent, bg=t["side"], highlightbackground=t["border"],
                        highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsw")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        tk.Label(left, text=self.tr("matches_title"), font=("Segoe UI", 10, "bold"),
                 bg=t["side"], fg=t["muted"], anchor="w", padx=int(14 * s),
                 pady=int(6 * s)).grid(row=0, column=0, sticky="ew")
        mwrap = tk.Frame(left, bg=t["side"])
        mwrap.grid(row=1, column=0, sticky="nsew")
        mwrap.rowconfigure(0, weight=1)
        mwrap.columnconfigure(0, weight=1)
        self.mtree = ttk.Treeview(mwrap, columns=("t", "o", "r", "d"),
                                  show="headings", selectmode="browse")
        for col, key, w, mn, anchor, stretch in (
                ("t", "m_time", int(104 * s), int(92 * s), "w", False),
                ("o", "m_opp", int(128 * s), int(90 * s), "w", True),
                ("r", "m_res", int(44 * s), int(40 * s), "center", False),
                ("d", "m_dur", int(52 * s), int(46 * s), "e", False)):
            self.mtree.heading(col, text=self.tr(key))
            self.mtree.column(col, width=w, minwidth=mn, anchor=anchor,
                              stretch=stretch)
        self.mtree.grid(row=0, column=0, sticky="nsew")
        msb = ttk.Scrollbar(mwrap, orient=tk.VERTICAL, command=self.mtree.yview)
        msb.grid(row=0, column=1, sticky="ns")
        self.mtree.configure(yscrollcommand=msb.set)
        self.mtree.tag_configure("new", font=("Segoe UI", 10, "bold"))
        self.mtree.bind("<<TreeviewSelect>>", lambda ev: self._on_match_select())

        # 右：本局明细
        right = tk.Frame(parent, bg=t["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(int(14 * s), 0))
        right.columnconfigure(0, weight=1)
        self.md_title_var = tk.StringVar(value=self.tr("match_hint_none"))
        self.md_title_label = tk.Label(
            right, textvariable=self.md_title_var, font=("Segoe UI", 15, "bold"),
            bg=t["bg"], fg=t["text"], anchor="w")
        self.md_title_label.grid(row=0, column=0, sticky="ew")
        self.md_meta_var = tk.StringVar(value=" ")
        tk.Label(right, textvariable=self.md_meta_var, font=("Segoe UI", 9),
                 bg=t["bg"], fg=t["muted"], anchor="w").grid(
            row=1, column=0, sticky="ew", pady=(2, int(8 * s)))

        self.md_kpi_frame = tk.Frame(right, bg=t["bg"])
        self.md_kpi_frame.grid(row=2, column=0, sticky="ew")

        tk.Label(right, text=self.tr("md_timeline"), font=("Segoe UI", 10, "bold"),
                 bg=t["bg"], fg=t["text"], anchor="w").grid(
            row=3, column=0, sticky="ew", pady=(int(12 * s), 2))
        self.md_timeline = tk.Canvas(right, height=int(200 * s), bg=t["bg"],
                                     highlightthickness=0)
        self.md_timeline.grid(row=4, column=0, sticky="ew")
        self.md_timeline.bind("<Configure>", lambda ev: self._redraw_timeline())
        self.md_timeline.bind("<Motion>", self._on_timeline_motion)
        self.md_timeline.bind("<Leave>",
                              lambda ev: self.md_hover_var.set(""))
        self.md_hover_var = tk.StringVar(value="")
        self.md_hover_label = tk.Label(right, textvariable=self.md_hover_var,
                                       font=("Consolas", 9), bg=t["bg"],
                                       fg=t["muted"], anchor="w")
        self.md_hover_label.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        self._tl = None
        self._tl_cache = {}

    def _fill_match_list(self):
        self.match_by_file = {}
        if not self.ds:
            return
        self.mtree.delete(*self.mtree.get_children())
        ms = [m for m in self.ds.get("matches", []) if m.get("me")]
        ms.sort(key=lambda m: -m["mtime"])
        for m in ms:
            f = m["file"]
            self.match_by_file[f] = m
            if m.get("winner_name") == m.get("me"):
                res = self.tr("m_win")
            elif m.get("opponent") and m.get("winner_name") == m.get("opponent"):
                res = self.tr("m_loss")
            else:
                res = "—"
            ts = datetime.datetime.fromtimestamp(m["mtime"]).strftime("%m-%d %H:%M")
            # 本次启动后打完的对局加粗标记（一眼看到“刚打完的”）
            tags = ("new",) if m["mtime"] >= self._open_ts else ()
            self.mtree.insert("", tk.END, iid=f,
                              values=(ts, m.get("opponent") or "-", res,
                                      f"{m['minutes']:.1f}"),
                              tags=tags)

    def _on_match_select(self, *_):
        sel = self.mtree.selection()
        if not sel:
            return
        m = self.match_by_file.get(sel[0])
        if not m:
            return
        self._sel_match = sel[0]
        try:
            self._render_match_detail(m)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()   # 单场明细失败不影响整页

    @staticmethod
    def _fmt_mmss(ms):
        sec = int(ms // 1000)
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _render_match_detail(self, m):
        t = self.T()
        s = self.s
        me, opp = m.get("me"), m.get("opponent")
        players = m.get("players") or {}
        my, op = players.get(me), players.get(opp)
        if m.get("winner_name") == me:
            res_txt, res_col = self.tr("m_win"), t["win"]
        elif opp and m.get("winner_name") == opp:
            res_txt, res_col = self.tr("m_loss"), t["bad"]
        else:
            res_txt, res_col = "—", t["muted"]
        dt = datetime.datetime.fromtimestamp(m["mtime"]).strftime("%Y-%m-%d %H:%M")
        self.md_title_var.set(f"{opp or '-'}  {res_txt}")
        self.md_title_label.configure(fg=res_col)
        hero_bits = []
        if my:
            hero_bits.append(f"{self.tr('md_me')} {legend_name(my.get('hero'))}")
        if op:
            hero_bits.append(f"{self.tr('md_opp')} {legend_name(op.get('hero'))}")
        self.md_meta_var.set(
            f"{dt} · {m['minutes']:.1f}min · "
            f"{self.tr('md_map')} {m.get('level_id', '-')} · " + " · ".join(hero_bits))

        # KPI 对比（我 vs 对手，本局口径）
        for wdg in self.md_kpi_frame.winfo_children():
            wdg.destroy()
        my_s = (my or {}).get("sum") or {}
        op_s = (op or {}).get("sum") or {}

        def val(v, suffix=""):
            return "–" if v is None else f"{v:g}{suffix}"

        def med(d):
            v = d.get("reaction_median_ms")
            return f"{v}ms" if v is not None else "–"

        rows = [
            (self.tr("kpi_dodge_per_min"),
             val(my_s.get("dodge_per_min")), val(op_s.get("dodge_per_min"))),
            (self.tr("kpi_dash_per_min"),
             val(my_s.get("dash_per_min")), val(op_s.get("dash_per_min"))),
            (self.tr("kpi_attacks_per_min"),
             val(my_s.get("attacks_per_min")), val(op_s.get("attacks_per_min"))),
            (self.tr("kpi_reactive_rate"),
             val(my_s.get("reactive_rate"), "%"), val(op_s.get("reactive_rate"), "%")),
            (self.tr("kpi_reaction"), med(my_s), med(op_s)),
            (self.tr("kpi_deaths"),
             str(my.get("deaths")) if my else "–",
             str(op.get("deaths")) if op else "–"),
        ]
        for c, txt in enumerate(("", self.tr("md_me"), self.tr("md_opp"))):
            tk.Label(self.md_kpi_frame, text=txt, font=("Segoe UI", 9, "bold"),
                     bg=t["bg"], fg=t["muted"],
                     anchor=("w" if c == 0 else "e")).grid(
                row=0, column=c,
                padx=(0, int(26 * s) if c else 0), sticky="ew")
        for r, (label, mv, ov) in enumerate(rows, start=1):
            tk.Label(self.md_kpi_frame, text=label, font=("Segoe UI", 9),
                     bg=t["bg"], fg=t["text"], anchor="w").grid(
                row=r, column=0, sticky="ew")
            for c, v in ((1, mv), (2, ov)):
                tk.Label(self.md_kpi_frame, text=v, font=("Consolas", 10),
                         bg=t["bg"], fg=t["text"], anchor="e").grid(
                    row=r, column=c,
                    padx=(int(26 * s), 0) if c == 1 else (0, 0), sticky="e")

        # 本局时间轴（单文件解析，稍后异步画）
        self._cur_match = m
        self.root.after(10, self._redraw_timeline)

    def _redraw_timeline(self):
        if self._cur_match:
            self._draw_match_timeline(self._cur_match)

    def _match_detail_cached(self, m):
        key = (self.replay_dir, m["file"])
        if key not in self._tl_cache:
            try:
                import bh_api
                self._tl_cache[key] = bh_api.match_detail(m["file"])
            except Exception:  # noqa: BLE001
                self._tl_cache[key] = {"error": "parse failed"}
        return self._tl_cache[key]

    def _draw_match_timeline(self, m):
        """整局时间轴：横轴=比赛时间。上/下条=双方攻击密度（自适应桶宽），
        ✖=精确死亡时刻（带虚线定位），顶部彩条=当时谁在领先。悬停看精确事件。"""
        cv = self.md_timeline
        t = self.T()
        s = self.s
        cv.delete("all")
        try:
            cv.update_idletasks()
        except tk.TclError:
            return
        me, opp = m.get("me"), m.get("opponent")
        det = self._match_detail_cached(m)
        if "error" in det or me not in det or opp not in det:
            cv.create_text(int(8 * s), int(10 * s), anchor="nw",
                           text=self.tr("md_no_detail"),
                           font=("Segoe UI", 9), fill=t["muted"])
            self._tl = None
            return
        dur = max(int(det.get("duration_ms") or 0), 1000)
        my_d = [x for x in (det[me].get("death_times_ms") or []) if 0 <= x <= dur]
        op_d = [x for x in (det[opp].get("death_times_ms") or []) if 0 <= x <= dur]
        my_a = [a["t_ms"] for a in (det[me].get("attacks") or [])]
        op_a = [a["t_ms"] for a in (det[opp].get("attacks") or [])]
        W = max(int(cv.winfo_width()), int(380 * s))
        H = int(cv.cget("height"))

        # 图例（带色文字，等宽排布）
        import tkinter.font as tkfont
        f = tkfont.Font(root=self.root, font=("Segoe UI", 8))
        lx = int(6 * s)
        for txt, col in ((self.tr("md_lg_me_atk"), t["accent"]),
                         (self.tr("md_lg_opp_atk"), t["blue"]),
                         (self.tr("md_lg_me_die"), t["bad"]),
                         (self.tr("md_lg_opp_die"), t["win"]),
                         (self.tr("md_lg_lead"), t["muted"]),
                         (self.tr("md_lg_scale"), t["muted"])):
            cv.create_text(lx, int(9 * s), anchor="w", text=txt,
                           font=("Segoe UI", 8), fill=col)
            lx += f.measure(txt) + int(14 * s)

        # 布局：图例 / 领先条 / 图表 / 时间刻度；左侧留白放数值刻度
        pad_l = int(34 * s)
        x0 = pad_l
        plot_w = max(W - x0, int(300 * s))
        strip_y = int(20 * s)
        strip_h = int(5 * s)
        chart_t = strip_y + strip_h + int(8 * s)
        chart_b = H - int(20 * s)
        mid = (chart_t + chart_b) // 2
        half = (chart_b - chart_t) // 2
        bar_max = max(half - int(16 * s), 8)

        # 时间刻度 + 竖网格
        step = next((tv * 1000 for tv in (15, 30, 60, 120, 300, 600)
                     if tv * 1000 >= dur / 6), 600000)
        tt = step
        while tt < dur:
            x = x0 + tt / dur * plot_w
            cv.create_line(x, chart_t, x, chart_b, fill=t["border"])
            cv.create_text(x, H - int(9 * s), text=self._fmt_mmss(tt),
                           font=("Consolas", 8), fill=t["muted"])
            tt += step

        # 攻击密度条（约 8 秒/桶，自适应桶数）
        nb = max(12, min(72, int(dur // 8000)))
        bw = plot_w / nb

        def counts(ats):
            c = [0] * nb
            for ts in ats:
                if 0 <= ts < dur:
                    c[min(int(ts / dur * nb), nb - 1)] += 1
            return c

        cm, co = counts(my_a), counts(op_a)
        mx = max(max(cm), max(co), 1)
        for i in range(nb):
            x = x0 + i * bw
            if cm[i]:
                h1 = bar_max * cm[i] / mx
                cv.create_rectangle(x + 1, mid - h1, x + bw - 1, mid,
                                    fill=t["accent"], width=0)
            if co[i]:
                h2 = bar_max * co[i] / mx
                cv.create_rectangle(x + 1, mid, x + bw - 1, mid + h2,
                                    fill=t["blue"], width=0)
        cv.create_line(x0, mid, W, mid, fill=t["border"])

        # 数值刻度：满格与半格横网格线 + 左侧数值（上下对称同刻度）
        for frac in (1.0, 0.5):
            for sgn in (-1, 1):
                y = mid + sgn * bar_max * frac
                cv.create_line(x0, y, W, y, fill=t["border"])
            v = mx * frac
            lab = f"{v:g}"
            cv.create_text(int(4 * s), mid - bar_max * frac, anchor="w",
                           text=lab, font=("Consolas", 8), fill=t["muted"])
            cv.create_text(int(4 * s), mid + bar_max * frac, anchor="w",
                           text=lab, font=("Consolas", 8), fill=t["muted"])

        # 死亡标记（精确时刻）+ 虚线定位
        deaths_xy = []
        for ts in op_d:
            x = x0 + ts / dur * plot_w
            cv.create_line(x, chart_t, x, chart_b, fill=t["border"], dash=(2, 3))
            cv.create_text(x, mid - bar_max - int(8 * s), text="✖",
                           font=("Segoe UI Symbol", 10), fill=t["win"])
            deaths_xy.append((x, ts, opp))
        for ts in my_d:
            x = x0 + ts / dur * plot_w
            cv.create_line(x, chart_t, x, chart_b, fill=t["border"], dash=(2, 3))
            cv.create_text(x, mid + bar_max + int(8 * s), text="✖",
                           font=("Segoe UI Symbol", 10), fill=t["bad"])
            deaths_xy.append((x, ts, me))

        # 领先条：每一次死亡推进一次（领先 = 对手已死次数更多）
        lead = 0
        last_t = 0
        for ts, delta in sorted([(x, 1) for x in op_d]
                                + [(x, -1) for x in my_d]) + [(dur, 0)]:
            if ts > last_t:
                color = (t["muted"] if lead == 0
                         else (t["win"] if lead > 0 else t["bad"]))
                cv.create_rectangle(x0 + last_t / dur * plot_w, strip_y,
                                    x0 + ts / dur * plot_w, strip_y + strip_h,
                                    fill=color, width=0)
                last_t = ts
            lead += delta
        cv.create_line(x0, strip_y + strip_h, W, strip_y + strip_h,
                       fill=t["border"])
        self._tl = {"x0": x0, "plot_w": plot_w, "nb": nb, "cm": cm, "co": co,
                    "dur": dur, "deaths": deaths_xy, "me": me}

    def _on_timeline_motion(self, ev):
        g = getattr(self, "_tl", None)
        if not g:
            return
        t = self.T()
        thr = int(7 * self.s)
        hit = None
        for (dx, ts, who) in g["deaths"]:
            if abs(ev.x - dx) <= thr and (hit is None or abs(ev.x - dx) < abs(ev.x - hit[0])):
                hit = (dx, ts, who)
        # 光标所在桶的双方攻击次数
        rel = max(0, min(ev.x - g["x0"], g["plot_w"] - 0.001))
        bi = int(rel / g["plot_w"] * g["nb"]) if g["plot_w"] else 0
        counts_txt = self.tr("md_tl_counts", me=self.tr("md_me"),
                             a=g["cm"][bi], opp=self.tr("md_opp"),
                             b=g["co"][bi])
        if hit:
            self.md_hover_var.set(
                f"{self._fmt_mmss(hit[1])} · {hit[2]} {self.tr('md_died')}"
                f" · {counts_txt}")
            self.md_hover_label.configure(
                fg=t["bad"] if hit[2] == g["me"] else t["win"])
        else:
            t_ms = int(rel / g["plot_w"] * g["dur"])
            self.md_hover_var.set(f"{self._fmt_mmss(t_ms)} · {counts_txt}")
            self.md_hover_label.configure(fg=t["muted"])

    # ---------------- 自动发现新对局 ----------------
    def _dir_snapshot(self):
        """回放目录指纹（文件名+mtime+size 摘要），用于检测打完的新对局。"""
        try:
            items = []
            for name in os.listdir(self.replay_dir):
                if name.lower().endswith(".replay"):
                    try:
                        st = os.stat(os.path.join(self.replay_dir, name))
                        items.append((name, st.st_mtime_ns, st.st_size))
                    except OSError:
                        pass
            return tuple(sorted(items))
        except OSError:
            return None

    def _auto_refresh_poll(self):
        """每 60s 检查一次回放目录：有新对局/变更就静默增量刷新（打完就能看）。
        走增量缓存，通常只有新文件会被解析（每个约 0.1-0.2s）。"""
        try:
            if (not self.busy and self._last_snap is not None
                    and self._dir_snapshot() != self._last_snap):
                self._start_load(quiet=True)
        except Exception:  # noqa: BLE001
            pass
        self.root.after(60000, self._auto_refresh_poll)

    def _server_alive(self, port=8903):
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _open_webchat(self, q=None):
        """打开 AI 聊天：优先独立窗口（ChatWindow.exe，WebView2 内核），
        不存在则回退系统浏览器。q=预填问题（画像页联动）。"""
        if not self._server_alive():
            try:
                os.environ["BH_OUT_DIR"] = self.out_dir
                import chat_server as cs
                threading.Thread(target=lambda: cs.run_server(8903),
                                 daemon=True).start()
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("Error", f"{type(e).__name__}: {e}")
                return
            for _ in range(60):
                if self._server_alive():
                    break
                time.sleep(0.25)
        import urllib.parse
        url = "http://127.0.0.1:8903/" + (
            "?q=" + urllib.parse.quote(q) if q else "")
        # 首选：软件自带的独立聊天窗口（WebView2）
        import subprocess
        cw = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])),
                          "..", "ChatWindow", "ChatWindow.exe")
        cw = os.path.normpath(cw)
        if not os.path.isfile(cw):
            cw = r"D:\bh-desktop\dist-v2\ChatWindow\ChatWindow.exe"
        if os.path.isfile(cw):
            try:
                subprocess.Popen([cw, "8903"] + ([q] if q else []),
                                 cwd=os.path.dirname(cw),
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True)
                return
            except OSError:
                pass
        webbrowser.open(url)

    def _chat_update_gate(self):
        """浏览器版聊天：面板仅更新按钮状态。"""
        btn = getattr(self, "webchat_btn", None)
        if btn is None:
            return
        if not self.api_key:
            btn.configure(text=self.tr("webchat_need_key"), state=tk.NORMAL,
                          command=self.open_settings)
    def _ask_about_current(self):
        """画像页 → 聊天窗口联动：带问题打开 AI 聊天（自动填入输入框）。"""
        name = self._sel_name
        q = self.tr("ask_agent_q", name=name) if (name and name != self.me) else ""
        self._open_webchat(q=q)

    def _new_matches_count(self):
        """自上次启动以来的新对局数（纯本地）。同时把当前数写回配置。"""
        try:
            if not self.ds:
                return 0
            cur = len(self.ds["matches"])
            last = self._load_config().get("last_match_count")
            cfg_path = self._cfg_path()
            cfg = self._load_config()
            cfg["last_match_count"] = cur
            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f)
            except OSError:
                pass
            if isinstance(last, int) and cur > last:
                return cur - last
        except Exception:  # noqa: BLE001
            pass
        return 0

def _enable_dpi_awareness():
    """声明 DPI 感知：不声明的话 Windows 在高缩放率下拉伸窗口位图导致发糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    _enable_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


def _run_guarded():
    """windowed 打包后没有控制台，崩溃时写日志 + 弹窗，避免用户看到'双击没反应'。"""
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", _HERE),
                               "BrawlhallaAnalyzerV2")
        log_path = os.path.join(log_dir, "crash.log")
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            log_path = "(无法写入日志)"
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("启动失败",
                                 f"程序遇到错误，详情已保存到：\n{log_path}")
            r.destroy()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    _run_guarded()
