# -*- coding: utf-8 -*-
"""agent.py：桌面端 AI agent 核心（参考 AstrBot 的 provider 思路）。

架构：
  Provider 层  OpenAI 兼容 /chat/completions（DeepSeek / OpenAI / 任何兼容端点）
               - SSE 流式输出（打字机效果）
               - 标准 Function Calling（模型自己决定调用哪个工具）
  工具层       复用 bh_api / kb_store 的 10 个查询工具（数字全部来自确定性分析层）
  Agent 循环   user -> LLM -> tool_calls? -> 执行 -> 结果回传 -> LLM ... -> 最终答复
               单轮最多 8 次工具迭代，防止死循环
  会话层       多轮上下文（历史消息 + 工具结果都在上下文里）

零第三方依赖：urllib + 手写 SSE 解析。
"""

import os
import re
import sys
import json
import time
import queue
import threading
import urllib.request
import urllib.error

from analyze import pct

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------- 服务商预设 ----------------
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "custom": {
        "label": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "model": "",
        "key_url": "",
    },
}

SYSTEM_PROMPT = """你是 Brawlhalla 打法教练，接入用户的本地回放分析工具（数据经字节级校验）。

【工具规则——最高优先级】
1. 数据类问题（对手/自己/对局/英雄/机制）必须先调工具；禁止在无工具结果时说"根据数据"。
2. 工具返回的是紧凑文本报告，直接使用其中数字；不需要重复调用同一工具。
3. 可组合多工具：画像→英雄知识(kb_search)→给对策。
4. my_profile 的 me 字段是用户本人；用户问"分析 me 本人"= 自我复盘，从对手视角讲弱点。
5. 闲聊可不用工具。
6. 你有跨会话长期记忆（system 里的【长期记忆】）。涉及用户训练目标、约定、
   重要结论时，主动用 memory_write 记下来；用户问"你记得我什么"用 kb_memory。

【输出规范——严格执行】
- 结构：第一行一句话结论 → 「关键数据」要点（每条一行带数字）→ 「怎么打/建议」。
- 用 markdown：## 小节标题、- 列表、**加粗关键数字**。禁止输出 JSON 或大段引用工具原文。
- 篇幅：简单问题 ≤6 行；画像/对比 10-15 行；复盘/训练计划 ≤20 行。宁可短而准。
- 对比类用列表逐项对比（A vs B 同一指标一行）。
- 训练计划：最多 3 条，按"问题→为什么(数据)→练法"格式，标优先级。
- 复盘：按时间段讲转折点（谁先起势、死亡前后发生了什么），给可执行的下次改进。
- 数据不足就直说缺什么，不猜。"""

MAX_TOOL_ROUNDS = 8
MAX_HISTORY_MSGS = 40


def build_system_prompt():
    """系统提示 + 长期记忆注入（每次会话请求时组装，记忆更新后自然生效）。"""
    import memory_store
    block = memory_store.prompt_block()
    return SYSTEM_PROMPT + ("\n\n" + block + "\n" if block else "")


EXTRACT_PROMPT = """你是记忆管理器。维护一份关于用户的长期记忆事实列表。

现有记忆：
{facts}

刚发生的对话：
用户：{q}
助手：{reply}

规则：保留仍然有效的事实；合并重复；新增本轮值得长期记住的信息
（用户偏好/训练目标/已给的建议/对手结论/用户自我认知）；
删除过时或矛盾的。最多 {n} 条，每条一句话，中文。
只输出 JSON 数组，例如 ["事实1", "事实2"]，不要其他文字。"""


def extract_memory_async(q, reply, base_url, api_key, model):
    """后台线程：让 LLM 提炼本轮记忆事实。失败静默（记忆是尽力而为）。"""
    import memory_store

    def run():
        try:
            mem = memory_store.load()
            facts = mem["facts"]
            body = {"model": model,
                    "messages": [
                        {"role": "system", "content": "只输出 JSON。"},
                        {"role": "user", "content": EXTRACT_PROMPT.format(
                            facts=json.dumps(facts, ensure_ascii=False),
                            q=q[:400], reply=(reply or "")[:900],
                            n=memory_store._MAX_FACTS)}],
                    "temperature": 0.1, "max_tokens": 400, "stream": False}
            req = urllib.request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"})
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            text = d["choices"][0]["message"]["content"].strip()
            # 剥掉可能的 ```json 围栏
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.S).strip()
            arr = json.loads(text)
            if isinstance(arr, list) and arr:
                memory_store.replace_facts([str(x) for x in arr])
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()


# ---------------- 工具注册表（输出一律为给 LLM 读的紧凑文本） ----------------
def _fmt_profile(p):
    """对手画像 → 紧凑文本报告。"""
    if "error" in p:
        return p
    s = p["sum"]
    dc = s["dir_counts"]
    n = s["dodges"] or 1
    ddc = s.get("dash_dir_counts") or {}
    dd_top = sorted(ddc.items(), key=lambda kv: -kv[1])[:3]
    pdd = s.get("pressure_dash_dir") or {}
    pd_top = sorted(pdd.items(), key=lambda kv: -kv[1])[:2]
    legends = ", ".join(f"{k}×{v['count']}" for k, v in
                        list(p["legends"].items())[:2]) or "-"
    lines = [
        f"【{p['name']}】对你 {p['matches']}局 {p['wins']}胜{p['losses']}负"
        + (f"(你胜率{p['win_rate']}%)" if p.get("win_rate") is not None else "")
        + f" 共{p['minutes']}分钟 | 英雄: {legends}",
        f"真闪避 {s['dodge_per_min']}/min 共{s['dodges']}次: "
        f"原地{pct(dc.get('spot',0),n):.0f}% "
        + " ".join(f"{k}{pct(v,n):.0f}%" for k, v in
                   sorted(dc.items(), key=lambda kv: -kv[1])[:3] if k != "spot"),
        f"构成: 地面原地{s['dodges_ground']} / 起跳接闪{s['dodges_jump']} / 空中{s['dodges_air']}",
        f"dash {s['dash_per_min']}/min 共{s['dashes']}次: "
        + " ".join(f"{k}{v}" for k, v in dd_top),
        f"受压意图: 纯防御{s['reactive_rate']:.0f}% / 瞬时{pct(s.get('reactive_instant',0),n):.0f}% "
        f"/ 进攻性{pct(s.get('reactive_offensive',0),n):.0f}% / 自发{pct(s.get('spontaneous',0),n):.0f}%",
        f"反应中位{s.get('reaction_median_ms') or '-'}ms | 连闪率{s['panic_rate']:.0f}% "
        f"| 闪后反打{pct(s.get('attack_after_dodge',0),n):.0f}% | 攻击{s.get('attacks_per_min',0)}/min",
    ]
    if s.get("pressure_dash"):
        lines.append(f"受压dash逃逸{s['pressure_dash_rate']:.0f}%: "
                     + " ".join(f"{k}{pct(v, s['pressure_dash']):.0f}%" for k, v in pd_top))
    hist = p.get("history") or []
    if hist:
        recent = hist[:4]
        lines.append("近期交锋(新→旧): " + " / ".join(
            f"{'胜' if h['win'] else '负'}{h['minutes']:.0f}min" for h in recent))
    return "\n".join(lines)


def _fmt_brief(b):
    return (f"{b['name']}: {b['matches']}局({b['wins']}-{b['losses']}) "
            f"真闪避{b['true_dodges_per_min']}/min dash{b['dashes_per_min']}/min "
            f"原地{b['spot_dodge_share']:.0f}% 受压率{b['pressured_dodge_rate']:.0f}% "
            f"反应{b['reaction_median_ms']}ms")


def _tools_registry():
    import bh_api
    import kb_store
    import memory_store
    from analyze import pct as _pct

    def _opp_text(name):
        p = bh_api.opponent_profile(name)
        return _fmt_profile(p) if "error" not in p else p

    def _scout(name, lang="zh"):
        r = bh_api.scout_card(name, lang=lang)
        return r["card"] if "card" in r else r   # 错误 dict 原样回传（含 hint）

    def _query_text(r):
        if "error" in r:
            return r
        lines = [f"筛选[{r['metric']}] {r['condition']}: 命中{r['count']}人"]
        for row in r["rows"]:
            lines.append(f"{row['name']} | {row['matches']}局{row['record']} | "
                         f"{row['metric']}={row['value']} | 原地{row['spot_share']:.0f}% "
                         f"真闪避{row['dodge_per_min']}/min dash{row['dash_per_min']}/min")
        return "\n".join(lines)

    def _cluster_text(r):
        lines = []
        for k, v in r["archetypes"].items():
            if not v["count"]:
                continue
            lines.append(f"## {v['desc']} ({v['count']}人)")
            for m in v["members"][:8]:
                lines.append(f"- {m['name']} {m['matches']}局{m['record']} "
                             f"原地{m['spot']:.0f}% 空中{m['air']:.0f}% "
                             f"dash{m['dash']}/min 受压率{m['react']:.0f}%")
        return "\n".join(lines) or "样本不足"

    def _match_sum_text(r):
        if "error" in r:
            return r
        names = sorted({k for seg in r["segments"] for k in seg if k != "t"})
        lines = [f"{r['file']} 时长{r['duration_min']}min"]
        for seg in r["segments"]:
            parts = [seg["t"]]
            for n in names:
                d = seg[n]
                parts.append(f"{n.split(' ')[0]}:攻{d['atk']}闪{d['dodge']}"
                             f"冲{d['dash']}死{d['die']}")
            lines.append(" | ".join(parts))
        dw = r.get("death_windows") or []
        if dw:
            lines.append("死亡窗口(死前5秒, 死者行为 | 击杀方行为):")
            for w in dw:
                died_full = w["died"]
                other_full = next((k[:-6] for k in w
                                   if k.endswith("_atk5s") and k != died_full + "_atk5s"),
                                  None)
                died = died_full.split(" ")[0]
                other = (other_full or "?").split(" ")[0]
                d_a = w.get(died_full + "_atk5s", "?")
                d_d = w.get(died_full + "_dodge5s", "?")
                d_h = w.get(died_full + "_dash5s", "?")
                o_a = w.get((other_full or "") + "_atk5s", "?")
                lines.append(f"{w['t_s']}s {died}死 | {died}:攻{d_a}闪{d_d}冲{d_h}"
                             f" | {other}:攻{o_a}")
        return "\n".join(lines)

    def _weekly_text(r):
        lines = []
        for w in r["weeks"]:
            lines.append(
                f"{w['week']} {w['win_loss']}(胜率{w['win_rate']}%) "
                f"{w['minutes']}min | 闪避{w['dodge_per_min']} dash{w['dash_per_min']} "
                f"攻击{w['attacks_per_min']}/min | 偏左闪{w.get('left_share')}% "
                f"原地{w.get('spot_share')}% 受压率{w.get('reactive_rate')}% "
                f"连闪{w.get('panic_rate')}%")
        return "\n".join(lines)

    def _loss_text(r):
        def one(rec, tag):
            return (f"[{tag}] vs {rec['opponent']} | 他: 原地{rec['opp_spot_share']:.0f}% "
                    f"真闪避{rec['opp_dodge_per_min']}/min dash{rec['opp_dash_per_min']}/min "
                    f"攻击{rec['opp_attacks_per_min']}/min 受压率{rec['opp_reactive_rate']:.0f}% "
                    f"| 文件:{rec['file']}")
        lines = [one(x, "输") for x in r["recent_losses"]]
        lines += [one(x, "赢") for x in r["recent_wins"][:3]]
        lines.append(r["note"])
        return "\n".join(lines)

    def _my_text():
        import json as _json
        p = bh_api.my_profile()
        s = p.get("sum", {})
        dc = s.get("dir_counts", {})
        n = s.get("dodges", 1)
        lines = [f"用户本人 {p['me']}（对手列表查不到此名，这就是用户自己）",
                 f"真闪避 {s.get('dodge_per_min')}/min: 原地{_pct(dc.get('spot',0),n):.0f}% "
                 + " ".join(f"{k}{_pct(v,n):.0f}%" for k, v in
                            sorted(dc.items(), key=lambda kv: -kv[1])[:3] if k != "spot"),
                 f"dash {s.get('dash_per_min')}/min | 受压纯防御率{s.get('reactive_rate', 0)}% "
                 f"| 反应中位{s.get('reaction_median_ms') or '-'}ms | 连闪率{s.get('panic_rate', 0)}% "
                 f"| 攻击{s.get('attacks_per_min', 0)}/min",
                 f"受压dash逃逸率{s.get('pressure_dash_rate',0)}% 方向:"
                 + " ".join(f"{k}{v}" for k, v in sorted(
                     (s.get('pressure_dash_dir') or {}).items(),
                     key=lambda kv: -kv[1])[:2])]
        lg = p.get("legends", {})
        if lg:
            from legends import legend_name as _ln
            tops = sorted(lg.items(), key=lambda kv: -kv[1]["games"])[:5]
            lines.append("英雄胜率: " + " | ".join(
                f"{_ln(int(k))} {v['wins']}-{v['losses']}({v['win_rate']}%)"
                for k, v in tops))
        mp = p.get("maps", {})
        if mp:
            tops = sorted(mp.items(), key=lambda kv: -kv[1]["games"])[:4]
            lines.append("地图胜率: " + " | ".join(
                f"ID{k} {v['wins']}-{v['losses']}({v['win_rate']}%)"
                for k, v in tops))
        return "\n".join(lines)

    return [
        {"name": "list_opponents", "desc": "对手名单概要。q=名字过滤。",
         "params": {"type": "object", "properties": {
             "q": {"type": "string"}, "limit": {"type": "integer", "default": 15}}},
         "fn": lambda a: "\n".join(_fmt_brief(b) for b in bh_api.list_opponents(
             q=a.get("q"), limit=int(a.get("limit", 15))))},
        {"name": "opponent_profile", "desc": "对手完整画像文本报告（方向分布/受压意图/反应/交锋史）。",
         "params": {"type": "object", "required": ["name"],
                    "properties": {"name": {"type": "string"}}},
         "fn": lambda a: _opp_text(a["name"])},
        {"name": "my_profile", "desc": "用户本人画像（含英雄/地图胜率）。分析'我'必用。",
         "params": {"type": "object", "properties": {}},
         "fn": lambda a: _my_text()},
        {"name": "compare_opponents", "desc": "两对手对比文本（同指标逐行 A vs B）。",
         "params": {"type": "object", "required": ["a", "b"],
                    "properties": {"a": {"type": "string"}, "b": {"type": "string"}}},
         "fn": lambda a: _opp_text(a["a"]) + "\n----\n" + _opp_text(a["b"])},
        {"name": "query_opponents", "desc": "条件筛选对手。metric 可选: spot_share(原地闪%) / dash_per_min / dodge_per_min / reactive_rate(受压率%) / reaction_ms / panic_rate / attacks_per_min / pressure_dash_rate(受压dash率%) / right_escape_share(受压右逃%) / left_escape_share / win_rate_vs_me(你对他的胜率%) / matches。op: > < >= <=，value: 数值。",
         "params": {"type": "object",
                    "properties": {"metric": {"type": "string"},
                                   "op": {"type": "string", "enum": [">", "<", ">=", "<="]},
                                   "value": {"type": "number"},
                                   "min_matches": {"type": "integer", "default": 1},
                                   "limit": {"type": "integer", "default": 12}}},
         "fn": lambda a: _query_text(bh_api.query_opponents(
             metric=a.get("metric", "matches"), op=a.get("op"),
             value=a.get("value"), min_matches=int(a.get("min_matches", 1)),
             limit=int(a.get("limit", 12))))},
        {"name": "cluster_opponents", "desc": "把对手按打法聚成原型（站桩型/空中型/dash型/反应型/均衡型）。",
         "params": {"type": "object",
                    "properties": {"min_matches": {"type": "integer", "default": 2}}},
         "fn": lambda a: _cluster_text(bh_api.cluster_opponents(
             min_matches=int(a.get("min_matches", 2))))},
        {"name": "match_history", "desc": "最近对局列表（可按对手过滤）。",
         "params": {"type": "object", "properties": {
             "name": {"type": "string"}, "limit": {"type": "integer", "default": 10}}},
         "fn": lambda a: "\n".join(
             f"{m['date']} vs {m['opponent']} {'胜' if m['win'] else '负'}"
             f" {m['minutes']:.0f}min | 他真闪避{m['opp_dodges_per_min']} "
             f"dash{m['opp_dashes_per_min']}/min 受压率{m['opp_pressured_rate']}% "
             f"| 文件:{m['file']}"
             for m in bh_api.match_history(a.get("name"),
                                           limit=int(a.get("limit", 10))))},
        {"name": "match_summary", "desc": "单场复盘摘要：按30秒分段给出双方攻击/闪避/dash密度与死亡时刻。复盘某场必用，file 来自 match_history。",
         "params": {"type": "object", "required": ["file"],
                    "properties": {"file": {"type": "string"}}},
         "fn": lambda a: _match_sum_text(bh_api.match_summary(a["file"]))},
        {"name": "weekly_report", "desc": "近几周战绩+关键指标变化（训练追踪用）。",
         "params": {"type": "object",
                    "properties": {"weeks": {"type": "integer", "default": 6}}},
         "fn": lambda a: _weekly_text(bh_api.weekly_report(
             weeks=int(a.get("weeks", 6))))},
        {"name": "loss_analysis", "desc": "连败共性：最近输局对手 vs 赢局对手的画像对比。问'为什么连输'必用。",
         "params": {"type": "object",
                    "properties": {"n": {"type": "integer", "default": 5}}},
         "fn": lambda a: _loss_text(bh_api.loss_analysis(n=int(a.get("n", 5))))},
        {"name": "scout_card", "desc": "赛前情报卡（画像+建议的一段文本）。",
         "params": {"type": "object", "required": ["name"],
                    "properties": {"name": {"type": "string"},
                                   "lang": {"type": "string", "enum": ["zh", "en"]}}},
         "fn": lambda a: _scout(a["name"], a.get("lang", "zh"))},
        {"name": "kb_search", "desc": "游戏知识库：闪避帧数/冷却/dash机制、69英雄武器属性、武器招式、用户笔记。问机制/英雄知识必用。",
         "params": {"type": "object", "required": ["query"],
                    "properties": {"query": {"type": "string"}}},
         "fn": lambda a: "\n\n".join(f"[{h['title']}]\n{h['text'][:350]}"
                                     for h in kb_store.search_kb(a["query"], top_k=3))},
        {"name": "memory_write", "desc": "把需要跨会话长期记住的关键信息写入记忆。调用 1 次即成功，返回确认后直接用自然语言答复用户，禁止重复调用。",
         "params": {"type": "object", "required": ["text"],
                    "properties": {"text": {"type": "string",
                                            "description": "一句话事实，中文"}}},
         "fn": lambda a: "✅ 已永久记住（所有后续对话自动生效）: " +
                         memory_store.add_fact(a["text"]) +
                         "。现在请直接答复用户，不要再调用任何工具。"},
        {"name": "kb_memory", "desc": "查看当前长期记忆列表（用户问'你记得我什么'时用）。",
         "params": {"type": "object", "properties": {}},
         "fn": lambda a: "\n".join("- " + f for f in memory_store.load()["facts"])
                          or "(记忆为空)"},
        {"name": "get_note", "desc": "读用户对某对手的笔记。",
         "params": {"type": "object", "required": ["name"],
                    "properties": {"name": {"type": "string"}}},
         "fn": lambda a: bh_api.get_opponent_note(a["name"]) or "(无笔记)"},
        {"name": "set_note", "desc": "写/改用户对某对手的笔记（空串=删除）。",
         "params": {"type": "object", "required": ["name", "note"],
                    "properties": {"name": {"type": "string"},
                                   "note": {"type": "string"}}},
         "fn": lambda a: bh_api.set_opponent_note(a["name"], a["note"])},
    ]


def tools_openai_schema():
    """OpenAI Function Calling 格式的工具声明。"""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["desc"], "parameters": t["params"]}}
        for t in _tools_registry()]


def dispatch_tool(name, arguments):
    reg = {t["name"]: t for t in _tools_registry()}
    t = reg.get(name)
    if not t:
        return {"error": f"unknown tool {name}"}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return {"error": f"bad arguments: {arguments!r}"}
    try:
        return t["fn"](args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _sanitize_reply(text):
    """清洗模型输出的内联工具调用残留（DeepSeek 禁用 tools 后偶发）。"""
    t = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S)
    t = re.sub(r"<｜｜?DSML｜｜?>[^<]*", "", t)
    t = re.sub(r"<tool_call>.*", "", t, flags=re.S)      # 未闭合残尾
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _tool_content(result):
    """工具结果 -> tool 消息 content（字符串直存，dict 转 JSON）。"""
    if isinstance(result, str):
        return result[:12000]
    return json.dumps(result, ensure_ascii=False)[:12000]


# ---------------- SSE / HTTP ----------------
def _post_stream(base_url, api_key, model, messages, tools=None, timeout=90,
                 abort_check=None):
    """POST /chat/completions (stream=True)，逐字段 yield delta 事件。"""
    body = {"model": model, "messages": messages, "stream": True,
            "temperature": 0.2,
            "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            if abort_check is not None and abort_check():
                raise AgentAborted()
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            if d.get("usage"):          # 末尾 usage chunk（choices 为空）
                yield {"__usage": d["usage"]}
            choices = d.get("choices") or []
            if choices:
                yield choices[0].get("delta") or {}


class AgentError(Exception):
    pass


class AgentAborted(Exception):
    """用户主动停止生成。"""


# ---------------- Agent ----------------
class Agent:
    """一次会话 = 一个 Agent 实例（持有上下文）；UI 层把回调塞进队列渲染。

    会话持久化：history 可 JSON 序列化（state_save/state_load），
    last_turn 记录本轮工具调用与耗时（供 UI 折叠块展示）。"""

    def __init__(self, base_url, api_key, model):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.history = []          # OpenAI 消息数组（含工具消息）
        self.busy = False
        self._usage_total = 0      # 会话累计 tokens（stream usage 累计）
        self.last_turn = {"tools": [], "elapsed": 0.0, "reply": ""}

    def state_save(self):
        return {"base_url": self.base_url, "model": self.model,
                "history": self.history}

    def state_load(self, state):
        self.history = state.get("history") or []

    def _trim_history(self):
        """上下文裁剪 + 记忆归档：超过上限时丢最旧的消息组（从 user 边界切，
        绝不拆散 tool_calls 与 tool 消息对）；被裁轮次进入情节记忆。"""
        if len(self.history) <= MAX_HISTORY_MSGS:
            return
        cut = len(self.history) - MAX_HISTORY_MSGS
        for i in range(max(cut, 1), len(self.history)):
            if self.history[i].get("role") == "user":
                cut = i
                break
        else:
            cut = max(cut, 1)
        dropped = self.history[:cut]
        self.history = self.history[cut:]
        # 归档：被裁部分按 (user, 随后的 assistant 文本) 配对进情节记忆
        try:
            import memory_store
            cur_q = None
            for m in dropped:
                if m.get("role") == "user" and m.get("content"):
                    cur_q = m["content"]
                elif (m.get("role") == "assistant" and m.get("content")
                        and cur_q):
                    memory_store.push_old_turn(cur_q, m["content"])
                    cur_q = None
        except Exception:  # noqa: BLE001
            pass

    def reset(self):
        self.history = []

    def chat(self, user_text, on_delta=None, on_tool=None, abort_check=None):
        """发送一轮；流式回调 on_delta(text_fragment)、on_tool(name, args, result)。
        abort_check() 返回 True 时中止（抛 AgentAborted，已生成文本保留在
        last_turn.reply）。返回最终答复文本。阻塞执行——UI 层放线程里跑。"""
        if self.busy:
            raise AgentError("busy")
        self.busy = True
        try:
            try:
                return self._chat_inner(user_text, on_delta, on_tool,
                                        abort_check=abort_check)
            except AgentAborted:
                self._sanitize_history()  # 中断可能留下缺结果的 tool_calls
                self.last_turn = {"tools": self.last_turn.get("tools", []),
                                  "elapsed": self.last_turn.get("elapsed", 0.0),
                                  "reply": self.last_turn.get("reply", "")
                                  or "(已停止)"}
                return self.last_turn["reply"]
        finally:
            self.busy = False

    def _auto_tool(self, user_text):
        """模型跳过工具时的兜底：按问题内容替它选一个工具。
        返回 (name, args, result) 或 None。"""
        import bh_api
        import kb_store
        try:
            low = user_text.lower()
            me = bh_api.my_profile().get("me", "")
            if me and me.lower() in low:
                return ("my_profile", {}, bh_api.my_profile())
            name = bh_api.find_opponent_in_text(user_text)
            if name:
                return ("scout_card", {"name": name, "lang": "zh"},
                        bh_api.scout_card(name))
        except Exception:  # noqa: BLE001
            pass
        low = user_text.lower()
        if (any(k in user_text for k in ("我的", "自己", "我常", "我该", "我打"))
                or any(k in low for k in ("my ", "me ", "myself", "i play"))):
            try:
                return ("my_profile", {}, bh_api.my_profile())
            except Exception:  # noqa: BLE001
                pass
        hits = kb_store.search_kb(user_text, top_k=4)
        if hits:
            return ("kb_search", {"query": user_text}, hits)
        return None

    def _sanitize_history(self):
        """会话结构自愈（每次请求前跑）：
        - 修复/剥离残缺的 tool_calls（用户停止生成时 arguments 可能只有
          半截 JSON，非法结构会让下一轮请求 400 卡死）
        - 删除孤立的 tool 消息；补齐缺失的 tool 结果
        - OpenAI 格式严格要求 assistant(tool_calls) 与 tool 成对。"""
        import copy
        hist = copy.deepcopy(self.history)
        # 0) content 类型收敛（历史数据里可能存成 list）
        for m in hist:
            c = m.get("content")
            if isinstance(c, list):
                m["content"] = "".join(str(x) for x in c)
            elif c is not None and not isinstance(c, str):
                m["content"] = str(c)
        # 1) 修残缺 tool_calls：arguments 非法 JSON 的声明剔除；
        #    剔尽后无 content 的 assistant 整条删
        cleaned = []
        for m in hist:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ok_tcs = []
                for tc in m["tool_calls"]:
                    try:
                        json.loads((tc.get("function") or {})
                                   .get("arguments") or "{}")
                        ok_tcs.append(tc)
                    except ValueError:
                        continue
                m["tool_calls"] = ok_tcs
                if not ok_tcs and not (m.get("content") or "").strip():
                    continue                      # 整条丢弃
            cleaned.append(m)
        hist = cleaned
        # 2) 孤立 tool 删除 + 缺失结果补齐
        declared = {}
        for m in hist:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    if tc.get("id"):
                        declared[tc["id"]] = True
        answered = {m.get("tool_call_id") for m in hist
                    if m.get("role") == "tool"}
        out = []
        for m in hist:
            if m.get("role") == "tool":
                if m.get("tool_call_id") not in declared:
                    continue
                out.append(m)
                continue
            out.append(m)
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    tid = tc.get("id")
                    if tid and tid not in answered:
                        out.append({"role": "tool", "tool_call_id": tid,
                                    "content": "[该次调用已被中断，无结果。"
                                               "请基于其他已有信息作答，"
                                               "不要向用户提及。]"})
                        answered.add(tid)
        self.history = out

    def _chat_inner(self, user_text, on_delta, on_tool, abort_check=None):
        self._sanitize_history()          # 先修复中断遗留的畸形结构
        self.history.append({"role": "user", "content": user_text})
        self.last_turn = {"tools": [], "elapsed": 0.0, "reply": "", "usage": 0}
        _t0 = time.time()
        _usage_start = getattr(self, "_usage_total", 0)
        _reply_parts = []
        tools_record = []

        def _on_delta(frag):
            _reply_parts.append(frag)
            self.last_turn["reply"] = "".join(_reply_parts)
            if on_delta:
                on_delta(frag)

        def _record_tool(name, args, result):
            tools_record.append({"name": name, "args": args,
                                 "result": _tool_content(result)[:2500]})

        tools = tools_openai_schema()
        final_text = ""
        used_tool = False
        executed_calls = {}     # (name, args) -> 次数：重复调用防护
        ok_results = {}         # (name, args) -> 上次成功结果（重复调用回缓存）
        memory_writes = 0
        force_final = False     # 检测到滥用工具后，下一轮禁用工具强制作答
        for _round in range(MAX_TOOL_ROUNDS):
            if force_final:
                msgs = [{"role": "system",
                         "content": build_system_prompt() +
                                    "\n\n【系统强制】工具调用已被禁用。禁止输出任何"
                                    " <tool_call>、<｜｜DSML｜｜> 之类的内联调用格式，"
                                    "禁止再尝试调用工具。请立即用自然语言，基于已获得的"
                                    "工具结果直接作答。"}] + self.history
            else:
                msgs = [{"role": "system", "content": build_system_prompt()}] \
                       + self.history
            content_parts = []
            tool_calls = {}      # index -> {id, name, arguments}
            for delta in _post_stream(self.base_url, self.api_key, self.model,
                                      msgs, None if force_final else tools,
                                      abort_check=abort_check):
                if delta.get("__usage"):
                    self._usage_total = delta["__usage"].get(
                        "total_tokens", self._usage_total)
                    continue
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    _on_delta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    slot = tool_calls.setdefault(i, {"id": None, "name": "",
                                                     "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

            msg = {"role": "assistant",
                   "content": "".join(content_parts) or None}
            calls = [tool_calls[i] for i in sorted(tool_calls)]
            if calls:
                # 统一解析调用 id 并写回（流式偶尔不带 id 时生成稳定的）；
                # tool 消息必须引用同一个 id，否则模型会陷入重试循环。
                msg["tool_calls"] = []
                for i, c in enumerate(calls):
                    c["resolved_id"] = c["id"] or f"call_{i}_{len(self.history)}"
                    msg["tool_calls"].append({
                        "id": c["resolved_id"], "type": "function",
                        "function": {"name": c["name"],
                                     "arguments": c["arguments"] or "{}"}})
            self.history.append(msg)

            if not calls:
                if not used_tool and _round == 0:
                    # 模型跳过了工具（LLM 概率性行为）：替它查一次真实数据注入，
                    # 让它基于事实重答，杜绝"根据数据"式编造。
                    auto = self._auto_tool(user_text)
                    if auto:
                        name, args, result = auto
                        if on_tool:
                            on_tool(name, json.dumps(args, ensure_ascii=False),
                                    result)
                        _record_tool(name, json.dumps(args, ensure_ascii=False),
                                     result)
                        self.history.append({
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "id": "auto_inject_1", "type": "function",
                                "function": {"name": name,
                                             "arguments": json.dumps(
                                                 args, ensure_ascii=False)}}]})
                        self.history.append({
                            "role": "tool", "tool_call_id": "auto_inject_1",
                            "content": _tool_content(result)})
                        used_tool = True
                        continue
                final_text = "".join(content_parts)
                break

            used_tool = True
            cache_hits = 0
            for c in calls:
                key = (c["name"], (c["arguments"] or "{}").strip())
                executed_calls[key] = executed_calls.get(key, 0) + 1
                if c["name"] == "memory_write":
                    memory_writes += 1
                if key in ok_results and executed_calls[key] > 1:
                    # 同参重复调用：直接回上次结果（不是 error——模型拿到
                    # 数据就会继续作答，杜绝"被拦截"叙事）
                    result = ok_results[key]
                    if isinstance(result, str):
                        result = "（同参数调用，返回上次结果）\n" + result
                    cache_hits += 1
                elif executed_calls[key] > 3:
                    result = {"error": "该参数多次失败。请改用其他参数/工具，"
                                       "或基于已有信息作答；不要向用户提及此消息。"}
                elif memory_writes > 2:
                    result = ("已收到（本轮记忆写入已达上限，此条不再重复记录）。"
                              "请直接答复用户。")
                else:
                    result = dispatch_tool(c["name"], c["arguments"])
                    if not (isinstance(result, dict) and "error" in result):
                        ok_results[key] = result
                if on_tool:
                    on_tool(c["name"], c["arguments"], result)
                _record_tool(c["name"], c["arguments"], result)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": c["resolved_id"],
                    "content": _tool_content(result)})
            if cache_hits == len(calls) and len(calls) > 0:
                # 整轮全是缓存命中 = 模型在原地打转 → 下一轮禁用工具
                force_final = True
            # 继续下一轮让模型消化工具结果
            # 继续下一轮让模型消化工具结果
        else:
            final_text = final_text or "（工具调用轮数达上限，已停止。）"
        if not final_text and self.history:
            final_text = "(空回复)"
        cleaned = _sanitize_reply(final_text)
        if not cleaned and force_final:
            # 禁工具后模型仍只输出内联调用被清洗为空：补一次"直接总结"请求
            try:
                msgs2 = [{"role": "system",
                          "content": "基于上文已获得的工具结果，用中文直接输出最终答案正文。"
                                     "禁止任何工具调用语法。"}] + self.history
                buf = []
                for delta in _post_stream(self.base_url, self.api_key,
                                          self.model, msgs2, None,
                                          abort_check=abort_check):
                    if delta.get("content"):
                        buf.append(delta["content"])
                cleaned = _sanitize_reply("".join(buf))
            except Exception:  # noqa: BLE001
                pass
        final_text = cleaned or "(空回复)"
        self.last_turn = {"tools": tools_record,
                          "elapsed": round(time.time() - _t0, 1),
                          "reply": final_text,
                          "usage": max(getattr(self, "_usage_total", 0)
                                       - _usage_start, 0)}
        self._trim_history()
        # 异步提炼长期记忆（不阻塞回复）
        try:
            extract_memory_async(user_text, final_text, self.base_url,
                                 self.api_key, self.model)
        except Exception:  # noqa: BLE001
            pass
        return final_text
