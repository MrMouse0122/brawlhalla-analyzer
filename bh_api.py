# -*- coding: utf-8 -*-
"""bh_api：给 agent / MCP / 桌面问答用的稳定查询层。

设计原则：
- 所有数字都来自确定性分析层（build_dataset -> aggregate），agent 只做推理不做计算；
- 返回值一律是可 JSON 序列化的纯 dict/list；
- 惰性加载数据集（走增量缓存，重复查询 0 开销）；
- 事件级明细（match_detail）按需单文件解析，不做全量。
"""
import os
import json
import glob
import datetime

import bh_query
from analyze import build_dataset, aggregate_by_opponent, pct, DIR_LABEL
from legends import legend_name

from bh_paths import replay_dir as _replay_dir
REPLAY_DIR_DEFAULT = _replay_dir()

_DS = None       # 数据集单例
_AGG = None
_ME_SUM = None


def _ensure(force_refresh=False):
    """惰性加载数据集 + 聚合（走增量缓存）。"""
    global _DS, _AGG, _ME_SUM
    if _DS is None or force_refresh:
        bh_query.REPLAY_DIR = os.environ.get("BH_REPLAY_DIR", REPLAY_DIR_DEFAULT)
        _DS, _stats = bh_query.load_dataset()
        _AGG, _ME_SUM = aggregate_by_opponent(_DS)
    return _DS, _AGG, _ME_SUM


def refresh():
    """强制重扫回放目录（有新对局时用）。"""
    _ensure(force_refresh=True)
    return {"ok": True, "matches": len(_DS["matches"]), "opponents": len(_AGG)}


def _sum_clean(s):
    return {k: v for k, v in s.items() if k != "_lat"}


def _entry_brief(name, e):
    s = e["sum"]
    dc = s["dir_counts"]
    top = max(dc.items(), key=lambda kv: kv[1])[0] if dc else "spot"
    return {
        "name": name,
        "matches": e["matches"],
        "wins": e["wins"], "losses": e["losses"],
        "minutes": round(e["minutes"], 1),
        "top_legends": [f"{legend_name(h)} (ID {h})×{c}"
                        for h, c in sorted(e["heroes"].items(), key=lambda kv: -kv[1])[:2]],
        "true_dodges_per_min": s["dodge_per_min"],
        "dashes_per_min": s["dash_per_min"],
        "pressured_dodge_rate": s["reactive_rate"],
        "reaction_median_ms": s.get("reaction_median_ms"),
        "spot_dodge_share": pct(dc.get("spot", 0), s["dodges"] or 1),
        "top_dodge_direction": DIR_LABEL.get(top, top),
        "panic_rate": s["panic_rate"],
        "attacks_per_min": s.get("attacks_per_min", 0),
    }


def list_opponents(q=None, limit=50, sort="matches"):
    """对手名单（brief 概要）。q=名字模糊过滤；sort: matches/dodges/winrate。"""
    _, agg, _ = _ensure()
    items = [(n, e) for n, e in agg.items()
             if not q or q.lower() in n.lower()]
    if sort == "winrate":
        items.sort(key=lambda kv: -(pct(kv[1]["wins"],
                   kv[1]["wins"] + kv[1]["losses"]) if (kv[1]["wins"] + kv[1]["losses"]) else -1))
    else:
        items.sort(key=lambda kv: (-kv[1]["matches"], -kv[1]["sum"]["dodges"]))
    return [_entry_brief(n, e) for n, e in items[:limit]]


def find_opponent(name):
    """名字模糊匹配（大小写不敏感子串），返回 (精确名, entry) 或 None。"""
    _, agg, _ = _ensure()
    low = name.lower()
    for n, e in agg.items():
        if n.lower() == low:
            return n, e
    hits = [(n, e) for n, e in agg.items() if low in n.lower()]
    if hits:
        return hits[0]
    return None


def find_opponent_in_text(text):
    """在一整句问话里找出现的对手名（长度>=3 的名字做子串匹配，取最长的）。"""
    _, agg, _ = _ensure()
    low = text.lower()
    best = None
    for n in agg:
        if len(n) >= 3 and n.lower() in low:
            if best is None or len(n) > len(best):
                best = n
    return best


# ---------------- 高级查询（agent 第一/二档功能的数据层） ----------------

METRICS = {
    "spot_share":      ("原地闪占比%", lambda s, e: pct(s["dir_counts"].get("spot", 0), s["dodges"] or 1)),
    "dodge_per_min":   ("真闪避/min", lambda s, e: s["dodge_per_min"]),
    "dash_per_min":    ("dash/min", lambda s, e: s["dash_per_min"]),
    "reactive_rate":   ("纯受压率%", lambda s, e: s["reactive_rate"]),
    "reaction_ms":     ("反应中位ms", lambda s, e: s.get("reaction_median_ms") or 0),
    "panic_rate":      ("连闪率%", lambda s, e: s["panic_rate"]),
    "attacks_per_min": ("攻击/min", lambda s, e: s.get("attacks_per_min", 0)),
    "pressure_dash_rate": ("受压dash率%", lambda s, e: s.get("pressure_dash_rate", 0)),
    "right_escape_share": ("受压右逃%", lambda s, e: _dir_share(s, ("right", "up_right", "down_right"))),
    "left_escape_share":  ("受压左逃%", lambda s, e: _dir_share(s, ("left", "up_left", "down_left"))),
    "win_rate_vs_me":  ("你对他的胜率%", lambda s, e: pct(e["wins"], e["wins"] + e["losses"]) if (e["wins"] + e["losses"]) else None),
    "matches":         ("局数", lambda s, e: e["matches"]),
}


def _dir_share(s, keys):
    pd = s.get("pressure_dash_dir") or {}
    total = s.get("pressure_dash", 0)
    if not total:
        return 0.0
    return pct(sum(pd.get(k, 0) for k in keys), total)


def query_opponents(metric="matches", op=None, value=None, min_matches=1,
                    sort=None, limit=15, q=None):
    """条件筛选对手。metric 见 METRICS；op: '>'/'<'/>='/'<='；sort 默认按 metric 降序。"""
    _, agg, _ = _ensure()
    if metric not in METRICS:
        return {"error": f"unknown metric: {metric}",
                "available": list(METRICS.keys())}
    label, get = METRICS[metric]
    rows = []
    for n, e in agg.items():
        if e["matches"] < min_matches or (q and q.lower() not in n.lower()):
            continue
        v = get(e["sum"], e)
        if v is None:
            continue
        if op and value is not None:
            if op == ">" and not v > value:
                continue
            if op == "<" and not v < value:
                continue
            if op == ">=" and not v >= value:
                continue
            if op == "<=" and not v <= value:
                continue
        dc = e["sum"]["dir_counts"]
        rows.append({"name": n, "matches": e["matches"],
                     "record": f"{e['wins']}-{e['losses']}",
                     "value": v, "metric": label,
                     "spot_share": pct(dc.get("spot", 0), e["sum"]["dodges"] or 1),
                     "dodge_per_min": e["sum"]["dodge_per_min"],
                     "dash_per_min": e["sum"]["dash_per_min"]})
    desc = (sort is None) or (sort == "desc")
    rows.sort(key=lambda r: (-(r["value"] if r["value"] is not None else -1)))
    if not desc:
        rows.reverse()
    return {"metric": label, "condition": (f"{op} {value}" if op else "all"),
            "count": len(rows), "rows": rows[:limit]}


def cluster_opponents(min_matches=2, limit_per_type=12):
    """把对手按打法风格聚成原型（规则聚类）。"""
    _, agg, _ = _ensure()
    types = {"turtler": [], "air_fighter": [], "dash_runner": [],
             "reactor": [], "balanced": []}
    meta = {}
    for n, e in agg.items():
        if e["matches"] < min_matches:
            continue
        s = e["sum"]
        dc = s["dir_counts"]
        n_d = s["dodges"] or 1
        spot = pct(dc.get("spot", 0), n_d)
        air_share = pct(s["dodges_air"] + s["dodges_jump"], n_d)
        info = {"name": n, "matches": e["matches"],
                "record": f"{e['wins']}-{e['losses']}",
                "spot": spot, "air": air_share,
                "dash": s["dash_per_min"], "react": s["reactive_rate"]}
        if spot >= 40:
            types["turtler"].append(info)
        elif air_share >= 45:
            types["air_fighter"].append(info)
        elif s["dash_per_min"] >= 28 and s["dodge_per_min"] < 12:
            types["dash_runner"].append(info)
        elif s["reactive_rate"] >= 35:
            types["reactor"].append(info)
        else:
            types["balanced"].append(info)
    meta["turtler"] = "站桩防御型：原地闪≥40%，硬抗你的压制"
    meta["air_fighter"] = "空中缠斗型：一半以上闪避发生在空中"
    meta["dash_runner"] = "高机动dash型：dash密、真闪避少，靠跑位"
    meta["reactor"] = "反应型：你出招后 35%+ 概率闪避"
    meta["balanced"] = "均衡型：无极端特征"
    return {"archetypes": {k: {"desc": meta[k], "count": len(v),
                               "members": v[:limit_per_type]}
                           for k, v in types.items()}}


def match_summary(file_hint, segment_s=30):
    """单场复盘摘要：按时间分段统计双方行为密度 + 死亡时刻 + 结果。
    给 LLM 叙事用（原始事件明细太长，见 match_detail）。"""
    det = match_detail(file_hint)
    if "error" in det:
        return det
    names = [k for k in det if k not in ("file", "duration_min")]
    seg_ms = segment_s * 1000
    out = {"file": det["file"], "duration_min": det["duration_min"],
           "segments": []}
    dur_ms = int(det["duration_min"] * 60000)
    for t0 in range(0, dur_ms, seg_ms):
        t1 = t0 + seg_ms
        seg = {"t": f"{t0 // 1000}-{t1 // 1000}s"}
        for n in names:
            d = det[n]
            seg[n] = {
                "atk": sum(1 for a in d["attacks"] if t0 <= a["t_ms"] < t1),
                "dodge": sum(1 for x in d["dodges"] if t0 <= x["t_ms"] < t1),
                "dash": sum(1 for x in d["dashes"] if t0 <= x["t_ms"] < t1),
                "die": sum(1 for t in d["death_times_ms"] if t0 <= t < t1),
            }
        out["segments"].append(seg)
    # 死亡窗口：每次死亡前 5 秒双方行为（死因叙事的关键数据）
    windows = []
    for n in names:
        other = [x for x in names if x != n][0]
        for dt in det[n]["death_times_ms"]:
            lo, hi = dt - 5000, dt

            def cnt(events):
                return sum(1 for e in events if lo <= e["t_ms"] < hi)
            windows.append({
                "t_s": round(dt / 1000), "died": n,
                f"{n}_atk5s": cnt(det[n]["attacks"]),
                f"{n}_dodge5s": cnt(det[n]["dodges"]),
                f"{n}_dash5s": cnt(det[n]["dashes"]),
                f"{other}_atk5s": cnt(det[other]["attacks"]),
                f"{other}_dodge5s": cnt(det[other]["dodges"]),
                f"{other}_dash5s": cnt(det[other]["dashes"]),
            })
    out["death_windows"] = windows
    return out


def weekly_report(weeks=6):
    """周报：每周胜负 + 我方关键指标变化（训练追踪用）。
    指标含：真闪避/dash/攻击频率 + 偏左闪避占比 + 受压率 + 连闪率。"""
    _ensure()
    ds = _DS
    me = ds["me"]
    by_week = {}
    for m in ds["matches"]:
        ms = m["players"].get(me)
        if not ms or not m.get("opponent"):
            continue
        wk = datetime.datetime.fromtimestamp(m["mtime"]).isocalendar()[:2]
        w = by_week.setdefault(wk, {"w": 0, "l": 0, "dodges": 0, "dashes": 0,
                                    "attacks": 0, "min": 0.0, "spot": 0,
                                    "left": 0, "reactive": 0, "panic": 0})
        if m["winner_name"] == me:
            w["w"] += 1
        elif m["winner_name"] == m["opponent"]:
            w["l"] += 1
        s = ms["sum"]
        dc = s["dir_counts"]
        w["dodges"] += s["dodges"]
        w["dashes"] += s["dashes"]
        w["attacks"] += s.get("attacks", 0)
        w["min"] += m["minutes"]
        w["spot"] += dc.get("spot", 0)
        w["left"] += dc.get("left", 0) + dc.get("up_left", 0) + dc.get("down_left", 0)
        w["reactive"] += s["reactive"]
        w["panic"] += s["panic_dodges"]
    out = []
    for wk in sorted(by_week)[-weeks:]:
        w = by_week[wk]
        mn = max(w["min"], 0.1)
        nd = max(w["dodges"], 1)
        out.append({
            "week": f"{wk[0]}-W{wk[1]}",
            "win_loss": f"{w['w']}-{w['l']}",
            "win_rate": pct(w["w"], w["w"] + w["l"]) if (w["w"] + w["l"]) else None,
            "minutes": round(w["min"], 1),
            "dodge_per_min": round(w["dodges"] / mn, 1),
            "dash_per_min": round(w["dashes"] / mn, 1),
            "attacks_per_min": round(w["attacks"] / mn, 1),
            "spot_share": round(100.0 * w["spot"] / nd, 1),
            "left_share": round(100.0 * w["left"] / nd, 1),
            "reactive_rate": round(100.0 * w["reactive"] / nd, 1),
            "panic_rate": round(100.0 * w["panic"] / nd, 1),
        })
    return {"weeks": out}


def loss_analysis(n=5):
    """连败共性：最近输的 n 场对手的聚合画像 vs 赢局对手聚合画像。"""
    _ensure()
    ds = _DS
    me = ds["me"]
    losses, wins = [], []
    for m in sorted(ds["matches"], key=lambda x: -x["mtime"]):
        if not m.get("opponent") or not m["winner_name"]:
            continue
        opp_sum = m["players"][m["opponent"]]["sum"]
        dc = opp_sum["dir_counts"]
        rec = {"file": m["file"], "opponent": m["opponent"],
               "opp_top_dodge": max(dc.items(), key=lambda kv: kv[1])[0] if dc else "-",
               "opp_spot_share": pct(dc.get("spot", 0), opp_sum["dodges"] or 1),
               "opp_dodge_per_min": opp_sum["dodge_per_min"],
               "opp_dash_per_min": opp_sum["dash_per_min"],
               "opp_attacks_per_min": opp_sum.get("attacks_per_min", 0),
               "opp_reactive_rate": opp_sum["reactive_rate"]}
        (losses if m["winner_name"] == m["opponent"] else wins).append(rec)
    return {"recent_losses": losses[:n],
            "recent_wins": wins[:n],
            "note": "对比输局与赢局对手的指标分布，找出共性差异"}


def opponent_profile(name):
    """单个对手完整画像（含交锋历史、英雄、dash 方向分布）。"""
    _ensure()
    hit = find_opponent(name)
    if not hit:
        return {"error": f"opponent not found: {name}",
                "hint": "用 list_opponents 查看全部名字"}
    real, e = hit
    s = e["sum"]
    return {
        "name": real,
        "matches": e["matches"],
        "wins": e["wins"], "losses": e["losses"],
        "win_rate": pct(e["wins"], e["wins"] + e["losses"]) if (e["wins"] + e["losses"]) else None,
        "minutes": round(e["minutes"], 1),
        "legends": {legend_name(h): {"id": h, "count": c}
                    for h, c in e["heroes"].items()},
        "maps": e["maps"],
        "sum": _sum_clean(s),
        "dash_dir_counts": s.get("dash_dir_counts", {}),
        "pressure_dash_dir": s.get("pressure_dash_dir", {}),
        "history": [{"date": datetime.datetime.fromtimestamp(h["date"]).strftime("%m-%d %H:%M"),
                     "win": h["win"], "minutes": h["minutes"], "file": h["file"]}
                    for h in reversed(e.get("history", []))],
    }


def my_profile():
    """我自己的画像 + 英雄/地图胜率 + 近期走势。"""
    _ensure()
    ds, agg, me_sum = _ensure()
    me = ds["me"]
    hero_stats, map_stats = {}, {}
    for m in ds["matches"]:
        ms = m["players"].get(me)
        if not ms or m["winner_name"] not in (me, m.get("opponent")):
            continue
        win = m["winner_name"] == me
        for store, key in ((hero_stats, ms["hero"]), (map_stats, m["level_id"])):
            st = store.setdefault(key, [0, 0])
            st[0 if win else 1] += 1
    def fmt_stats(store):
        out = {}
        for k, (w, l) in store.items():
            if w + l >= 2:
                out[k] = {"games": w + l, "wins": w, "losses": l,
                          "win_rate": pct(w, w + l)}
        return out
    return {
        "me": me,
        "sum": _sum_clean(me_sum) if me_sum else {},
        "legends": fmt_stats(hero_stats),
        "maps": fmt_stats(map_stats),
        "note": "legends 键是英雄 ID（用 legends.py 的 LEGENDS 表或 legend_name() 转名字）",
    }


def compare(a, b):
    """两个对手画像对比，自动标出关键差异。"""
    pa = opponent_profile(a)
    pb = opponent_profile(b)
    if "error" in pa or "error" in pb:
        return {"error": "compare failed", "a": pa.get("error"), "b": pb.get("error")}
    sa, sb = pa["sum"], pb["sum"]
    fields = [("dodge_per_min", "真闪避/min"), ("dash_per_min", "dash/min"),
              ("reactive_rate", "纯受压率%"), ("reaction_median_ms", "反应中位ms"),
              ("panic_rate", "连闪率%"), ("attacks_per_min", "攻击/min"),
              ("pressure_dash_rate", "受压dash率%")]
    diff = {}
    for key, label in fields:
        va, vb = sa.get(key), sb.get(key)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va is not None and vb is not None:
            diff[label] = {"a": va, "b": vb, "delta": round(va - vb, 1)}
    return {"a": pa["name"], "b": pb["name"], "diff": diff,
            "a_profile": pa, "b_profile": pb}


def match_history(name=None, limit=20):
    """对局级历史（可按对手过滤），含双方关键指标。"""
    _ensure()
    ds = _DS
    me = ds["me"]
    out = []
    for m in sorted(ds["matches"], key=lambda x: -x["mtime"]):
        if name and m.get("opponent") != name:
            continue
        opp = m.get("opponent") or "-"
        os_ = m["players"].get(opp, {}).get("sum", {})
        ms_ = m["players"].get(me, {}).get("sum", {})
        out.append({
            "file": m["file"], "date": datetime.datetime.fromtimestamp(m["mtime"]).strftime("%m-%d %H:%M"),
            "opponent": opp,
            "win": (m["winner_name"] == me) if m["winner_name"] else None,
            "minutes": m["minutes"],
            "me_dodges_per_min": ms_.get("dodge_per_min"),
            "opp_dodges_per_min": os_.get("dodge_per_min"),
            "opp_dashes_per_min": os_.get("dash_per_min"),
            "opp_pressured_rate": os_.get("reactive_rate"),
        })
        if len(out) >= limit:
            break
    return out


def match_detail(file_hint):
    """单场对局事件级明细（双方 闪避/dash/攻击 时间线 + 死亡时间戳）。"""
    _ensure()
    path = None
    for m in _DS["matches"]:
        if m["file"] == file_hint or file_hint in m["file"]:
            path = m["path"]
            break
    if not path:
        return {"error": f"match not found: {file_hint}",
                "hint": "file 参数来自 match_history 的 file 字段"}
    from bh_replay import parse_replay_file, F_HEAVY, F_LIGHT, F_DODGE, F_JUMP
    from analyze import (dodge_direction, direction_bits_at, AIRBORNE_MS,
                         DIR_MASK as DIR_MASK_A)
    rep = parse_replay_file(path)
    gd = rep["game_data"]
    ents = {e["ent_id"]: e["name"] for e in gd["entities"]}
    if len(ents) != 2:
        return {"error": "not a 1v1 match"}
    a, b = list(ents)
    seq_a = rep["inputs"].get(a) or []
    seq_b = rep["inputs"].get(b) or []
    if not seq_a or not seq_b:
        return {"error": "replay has an empty input stream"}
    out = {"file": os.path.basename(path), "duration_min": round(
        max(seq_a[-1][0], seq_b[-1][0]) / 60000, 1)}
    for ent, other in ((a, b), (b, a)):
        seq = rep["inputs"].get(ent, [])
        dodges, dashes, attacks = [], [], []
        recent_jumps = []
        for i in range(1, len(seq)):
            ts, fl = seq[i]
            _, pf = seq[i - 1]
            recent_jumps = [jt for jt in recent_jumps if 0 <= ts - jt <= AIRBORNE_MS]
            if (fl & F_DODGE) and not (pf & F_DODGE):
                if (fl & F_JUMP) or recent_jumps:
                    vert, horiz = direction_bits_at(seq, i)
                    d = dodge_direction(vert, horiz)
                    dodges.append({"t_ms": ts, "dir": d,
                                   "cat": "air" if (fl & F_JUMP) else "jump"})
                elif fl & DIR_MASK_A:
                    dashes.append({"t_ms": ts,
                                   "dir": dodge_direction(fl & DIR_MASK_A, 0)})
                else:
                    dodges.append({"t_ms": ts, "dir": "spot", "cat": "ground"})
            if (fl & (F_LIGHT | F_HEAVY)) and not (pf & (F_LIGHT | F_HEAVY)):
                attacks.append({"t_ms": ts, "heavy": bool(fl & F_HEAVY)})
            if (fl & F_JUMP) and not (pf & F_JUMP):
                recent_jumps.append(ts)
        deaths = [d["time"] for d in (rep.get("deaths") or []) if d["ent_id"] == ent]
        out[ents[ent]] = {
            "opponent": ents[other],
            "n_true_dodges": len(dodges), "n_dashes": len(dashes), "n_attacks": len(attacks),
            "dodges": dodges, "dashes": dashes, "attacks": attacks,
            "death_times_ms": deaths,
        }
    return out


def scout_card(name, lang="zh"):
    """赛前情报卡：画像 + 英雄知识 + 打法建议，压缩成一段可读文本。"""
    p = opponent_profile(name)
    if "error" in p:
        return p
    import report as bh_report
    from kb_store import legend_knowledge
    tips = bh_report.advice_for(p["sum"], is_opponent=True, lang=lang)
    lines = []
    tips_plain = []
    import re as _re
    import html as _html
    for t in tips:
        tips_plain.append(_html.unescape(_re.sub(r"<[^>]+>", "", t)))
    legends_str = ", ".join(p["legends"].keys()) or "-"
    if lang == "en":
        head = (f"[Scout card] {p['name']} — {p['matches']} matches vs you, "
                f"{p['wins']}W-{p['losses']}L, {p['minutes']} min. Legends: {legends_str}")
    else:
        head = (f"[情报卡] {p['name']} — 对你 {p['matches']} 局 "
                f"{p['wins']}胜{p['losses']}负，共 {p['minutes']} 分钟。常用英雄：{legends_str}")
    lines.append(head)
    lines.append("")
    lines.extend("▸ " + t for t in tips_plain)
    lk = legend_knowledge(list(p["legends"].keys()))
    if lk:
        lines.append("")
        lines.append(lk)
    notes = get_opponent_note(p["name"])
    if notes:
        lines.append("")
        lines.append(f"[我的笔记] {notes}")
    return {"name": p["name"], "card": "\n".join(lines)}


# ---------------- 用户笔记 ----------------
from kb_store import KB_DIR as _KB_DIR  # noqa: E402
_NOTES_PATH = os.path.join(_KB_DIR, "notes", "opponent_notes.json")


def get_opponent_note(name):
    try:
        with open(_NOTES_PATH, encoding="utf-8") as f:
            return json.load(f).get(name, "")
    except (OSError, ValueError):
        return ""


def set_opponent_note(name, text):
    os.makedirs(os.path.dirname(_NOTES_PATH), exist_ok=True)
    notes = {}
    try:
        with open(_NOTES_PATH, encoding="utf-8") as f:
            notes = json.load(f)
    except (OSError, ValueError):
        pass
    if text.strip():
        notes[name] = text.strip()
    else:
        notes.pop(name, None)
    with open(_NOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=1)
    return {"ok": True, "name": name, "note": notes.get(name, "")}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("me =", my_profile()["me"])
    print("opponents:", len(list_opponents(limit=1000)))
    print(json.dumps(_entry_brief(*list(
        sorted([(n, e) for n, e in _AGG.items()], key=lambda kv: -kv[1]["matches"])[0])),
        ensure_ascii=False, indent=1))
