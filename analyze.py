"""对手闪避习惯分析器：批量解析 replay，抽出双方输入习惯画像。

核心思路：Brawlhalla 的 .replay 只存输入流（+ 死亡时间戳），不存坐标。
所以"习惯"只能从输入序列上做统计——但对打法研究来说，输入习惯本身就是
最想要的东西：对手被压制时往哪闪、反应多快、爱不爱原地闪、爱不爱乱跳。
"""

import os
import sys
import glob
import json
from collections import Counter, defaultdict

from bh_replay import (
    parse_replay_file, ReplayParseError,
    F_UP, F_DROP, F_LEFT, F_RIGHT, F_JUMP, F_NEUTRAL_PRIORITY,
    F_HEAVY, F_LIGHT, F_DODGE, F_PICKUP_THROW,
    F_TAUNT_UP, F_TAUNT_RIGHT, F_TAUNT_DOWN, F_TAUNT_LEFT,
)

MS_PER_FRAME = 1000 / 60.0

DIR_LABEL = {
    "spot": "原地闪(spot)",
    "up": "上 ↑",
    "down": "下 ↓",
    "left": "左 ←",
    "right": "右 →",
    "up_left": "左上 ↖",
    "up_right": "右上 ↗",
    "down_left": "左下 ↙",
    "down_right": "右下 ↘",
}

# 3x3 方向面板坐标（行=上/中/下，列=左/中/右）
PAD_LAYOUT = [
    ("up_left", "up", "up_right"),
    ("left", "spot", "right"),
    ("down_left", "down", "down_right"),
]

DIR_MASK = F_UP | F_DROP | F_LEFT | F_RIGHT

# ---- v4 闪避/冲刺分类（依据 Brawlhalla Wiki "Movement" 的游戏机制）----
# 地面：闪避键+方向 = Dash（冲刺位移：无无敌帧、不消耗冷却，游戏里根本不算 dodge）；
#       闪避键单独按下 = 原地闪避 spot dodge（真闪避，16 帧无敌）。
# 空中：任何闪避键按下都是真闪避（方向空中闪 / 空中原地闪）。
# 因此"闪避键按下沿"要按 起跳状态+方向 三分：空中闪避 / 地面原地闪 / 地面 dash。
DIR_LOOKBACK_MS = 50      # 空中闪避方向的意图回溯窗口（方向点按后马上闪的场景）
AIRBORNE_MS = 350         # 起跳后该窗口内视为仍在空中（短跳全程约 500-700ms）
CHASE_CTX_MS = 300        # 自己出招后该窗口内按闪避 = 追击闪避/进攻位移（chase dodge 类）
REACT_WINDOW_MS = 400     # 受压判定窗口：对手出招后多久内的闪避算"受压"
REACT_MIN_MS = 80         # 人类反应下限；<80ms 的"受压闪避"大概率是同时按键/预输入

# 胜负：results.scores 是名次（1=第一名，2=第二名），不是比分。
# 已在 252 场有效对局上验证：名次判定与"死亡次数更少者"100% 一致。
# （旧版用 max(scores) 选"赢家"，实际选出的是败者，胜负全部颠倒。）


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    mid = n // 2
    if n % 2:
        return xs[mid]
    return round((xs[mid - 1] + xs[mid]) / 2)


def direction_bits_at(seq, i, window_ms=DIR_LOOKBACK_MS):
    """空中闪避的方向输入（垂直轴 / 水平轴）。

    按下帧按住的方向优先；某轴没有时，在 window_ms 内回溯最近一次出现过的
    方向（意图回溯：点一下方向立刻空中闪的场景）。
    注意：地面分类不使用本函数的回溯结果——地面"按下帧有没有方向"直接决定
    是 dash 还是原地闪，回溯会把真原地闪错划成 dash。
    """
    ts, flags = seq[i]
    vert = flags & (F_UP | F_DROP)
    horiz = flags & (F_LEFT | F_RIGHT)
    if not (vert and horiz):
        for j in range(i - 1, -1, -1):
            tsj, fj = seq[j]
            if tsj > ts or ts - tsj > window_ms:
                break
            if not vert and (fj & (F_UP | F_DROP)):
                vert = fj & (F_UP | F_DROP)
            if not horiz and (fj & (F_LEFT | F_RIGHT)):
                horiz = fj & (F_LEFT | F_RIGHT)
            if vert and horiz:
                break
    return vert, horiz


def own_attack_context(seq, i, window_ms=CHASE_CTX_MS):
    """闪避/冲刺按下前 window_ms 内自己是否按下过攻击键。

    命中后的追击闪避（chase dodge）、攻击后接 dash-in 都是这个输入特征——
    属于进攻性位移，不应计入"受压防御"。返回距最近一次攻击按下的 ms，否则 None。
    """
    ts, flags = seq[i]
    if flags & (F_LIGHT | F_HEAVY):
        return 0  # 攻击与闪避同时按住，明显是进攻动作
    for j in range(i, 0, -1):
        tsj, fj = seq[j]
        if tsj > ts or ts - tsj > window_ms:
            break
        pstate = seq[j - 1][1] if j >= 1 else 0
        if (fj & (F_LIGHT | F_HEAVY)) and not (pstate & (F_LIGHT | F_HEAVY)):
            return ts - tsj
    return None


def dodge_direction(flags: int, prev_flags: int) -> str:
    """把方向位组合归到 9 个桶里（纯分类，回溯逻辑在 direction_bits_at）。"""
    f = (flags or 0) | (prev_flags or 0)
    up = bool(f & F_UP)
    down = bool(f & F_DROP)
    left = bool(f & F_LEFT)
    right = bool(f & F_RIGHT)
    if up and down:      # 同时按上下，视为没有明确垂直意图
        up = down = False
    if left and right:
        left = right = False
    if not (up or down or left or right):
        return "spot"
    if up:
        return "up_left" if left else ("up_right" if right else "up")
    if down:
        return "down_left" if left else ("down_right" if right else "down")
    return "left" if left else "right"


def is_attack(flags: int) -> bool:
    return bool(flags & (F_LIGHT | F_HEAVY))


def is_taunt(flags: int) -> bool:
    return bool(flags & (F_TAUNT_UP | F_TAUNT_RIGHT | F_TAUNT_DOWN | F_TAUNT_LEFT))


def analyse_entity(seq, match_ms: int):
    """分析单个实体在一局里的输入习惯。seq = [(ts, flags), ...] 状态变更序列。"""
    stats = {
        "dodges": [],           # (ts, dir_key, cat, own_ctx) 真闪避 cat: ground(原地)/air/jump
        "dashes": [],           # (ts, dir_key, own_ctx)      地面 dash（位移，非闪避）
        "dir_counts": Counter(),
        "dash_dir_counts": Counter(),
        "dodges_by_cat": Counter(),  # ground=地面原地闪 air=空中闪 jump=起跳接闪
        "reactive_latency": [],  # 对手出招 -> 防御性闪避 的延迟(ms)
        "panic_dodges": 0,       # 1s 内连续真闪避
        "light": 0,
        "heavy": 0,
        "jump": 0,
        "jump_attack": 0,
        "fastfall": 0,
        "throw_pickup": 0,
        "taunt": 0,
        "neutral_priority": 0,
        "attack_after_dodge": 0,  # 真闪避后 500ms 内立刻还击
    }
    if not seq:
        return stats

    recent_jumps = []  # AIRBORNE_MS 内的起跳时间戳，用于识别"起跳后空中闪"
    for i in range(1, len(seq)):
        ts, flags = seq[i]
        pts, pflags = seq[i - 1]
        recent_jumps = [jt for jt in recent_jumps if 0 <= ts - jt <= AIRBORNE_MS]

        if (flags & F_DODGE) and not (pflags & F_DODGE):
            # —— v4 三分类（游戏机制：地面方向+闪避=Dash，不算闪避）——
            own_ctx = own_attack_context(seq, i)
            if (flags & F_JUMP) or recent_jumps:
                # 空中闪避（真闪避）：方向空中闪或空中原地闪
                vert, horiz = direction_bits_at(seq, i)
                d = dodge_direction(vert, horiz)
                cat = "air" if (flags & F_JUMP) else "jump"
                stats["dodges"].append((ts, d, cat, own_ctx))
                stats["dir_counts"][d] += 1
                stats["dodges_by_cat"][cat] += 1
            elif flags & DIR_MASK:
                # 地面 + 按下帧带方向 = Dash（冲刺位移，非闪避）
                d = dodge_direction(flags & DIR_MASK, 0)
                stats["dashes"].append((ts, d, own_ctx))
                stats["dash_dir_counts"][d] += 1
            else:
                # 地面原地闪（真闪避，游戏里唯一地面 dodge）
                stats["dodges"].append((ts, "spot", "ground", own_ctx))
                stats["dir_counts"]["spot"] += 1
                stats["dodges_by_cat"]["ground"] += 1
            # 真闪避后 500ms 内是否立刻还击：逐事件追踪"攻击按下沿"
            if len(stats["dodges"]) and stats["dodges"][-1][0] == ts:
                prev_f = flags
                for ts2, f2 in seq[i + 1:]:
                    if ts2 - ts > 500:
                        break
                    if (f2 & (F_LIGHT | F_HEAVY)) and not (prev_f & (F_LIGHT | F_HEAVY)):
                        stats["attack_after_dodge"] += 1
                        break
                    prev_f = f2

        # 招式 / 习惯键的"按下沿"计数
        if (flags & F_LIGHT) and not (pflags & F_LIGHT):
            stats["light"] += 1
        if (flags & F_HEAVY) and not (pflags & F_HEAVY):
            stats["heavy"] += 1
        if (flags & F_JUMP) and not (pflags & F_JUMP):
            stats["jump"] += 1
            recent_jumps.append(ts)
            if flags & (F_LIGHT | F_HEAVY):
                stats["jump_attack"] += 1
        if (flags & F_DROP) and not (pflags & F_DROP):
            stats["fastfall"] += 1
        if (flags & F_PICKUP_THROW) and not (pflags & F_PICKUP_THROW):
            stats["throw_pickup"] += 1
        if is_taunt(flags) and not is_taunt(pflags):
            stats["taunt"] += 1
        if (flags & F_NEUTRAL_PRIORITY) and not (pflags & F_NEUTRAL_PRIORITY):
            stats["neutral_priority"] += 1

    dodges = stats["dodges"]
    for i in range(1, len(dodges)):
        if dodges[i][0] - dodges[i - 1][0] <= 1000:
            stats["panic_dodges"] += 1
    return stats


def attack_timeline(seq):
    """对手的"出招瞬间"时间戳列表（攻击键按下沿）。"""
    out = []
    for i in range(1, len(seq)):
        ts, flags = seq[i]
        _, pflags = seq[i - 1]
        if (flags & (F_LIGHT | F_HEAVY)) and not (pflags & (F_LIGHT | F_HEAVY)):
            out.append(ts)
    return out


def _nearest_attack(ts, opp_attacks, window_ms):
    best = None
    for ats in opp_attacks:
        if 0 <= ts - ats <= window_ms and (best is None or ats > best):
            best = ats
    return ts - best if best is not None else None


def match_reactive(stats, opp_attacks, window_ms=REACT_WINDOW_MS):
    """把真闪避 / 地面 dash 与对手出招对齐，并按意图分类。

    回放没有受击事件，"被打中"不可见——这里给的是逐级过滤后的代理：
      defensive   纯防御：对手出招后 [80,400]ms 内的真闪避，且非自己出招后的追击位移
      instant     瞬时按键：延迟 <80ms，多为同按/预输入，不是反应
      offensive   进攻性：自己出招后 300ms 内的闪避/dash（chase dodge、攻击后 dash-in）
      spontaneous 自发：与对手出招无关的真闪避
    地面 dash 单独统计受压逃逸（pressure_dash）及方向。
    """
    latencies = []
    intents = Counter()
    for ts, d, cat, own_ctx in stats["dodges"]:
        lat = _nearest_attack(ts, opp_attacks, window_ms)
        if own_ctx is not None:
            intent = "offensive"
        elif lat is None:
            intent = "spontaneous"
        elif lat < REACT_MIN_MS:
            intent = "instant"
        else:
            intent = "defensive"
            latencies.append(lat)
        intents[intent] += 1
    stats["reactive_intent"] = intents
    # 受压 dash：反应窗口内、非进攻上下文的地面位移
    pd_dir = Counter()
    pd_n = 0
    for ts, d, own_ctx in stats["dashes"]:
        lat = _nearest_attack(ts, opp_attacks, window_ms)
        if own_ctx is None and lat is not None and lat >= REACT_MIN_MS:
            pd_n += 1
            pd_dir[d] += 1
    stats["pressure_dash"] = pd_n
    stats["pressure_dash_dir"] = pd_dir
    return latencies


def pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


def summarize(stats, minutes, latencies):
    n = len(stats["dodges"])
    nd = len(stats["dashes"])
    attacks = stats["light"] + stats["heavy"]
    intents = stats.get("reactive_intent") or Counter()
    return {
        "matches": 1,
        "minutes": minutes,
        "dodges": n,
        "dodge_per_min": round(n / minutes, 2) if minutes > 0 else 0.0,
        "dashes": nd,
        "dash_per_min": round(nd / minutes, 2) if minutes > 0 else 0.0,
        "dash_dir_counts": dict(stats["dash_dir_counts"]),
        "dodges_ground": stats["dodges_by_cat"].get("ground", 0),
        "dodges_air": stats["dodges_by_cat"].get("air", 0),
        "dodges_jump": stats["dodges_by_cat"].get("jump", 0),
        "dir_counts": dict(stats["dir_counts"]),
        "reactive": len(latencies),
        "reactive_rate": pct(len(latencies), n),
        "reactive_instant": intents.get("instant", 0),
        "reactive_offensive": intents.get("offensive", 0),
        "spontaneous": intents.get("spontaneous", 0),
        "pressure_dash": stats.get("pressure_dash", 0),
        "pressure_dash_rate": pct(stats.get("pressure_dash", 0), nd),
        "pressure_dash_dir": dict(stats.get("pressure_dash_dir") or {}),
        "reaction_avg_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "reaction_median_ms": median(latencies) if latencies else None,
        "panic_dodges": stats["panic_dodges"],
        "panic_rate": pct(stats["panic_dodges"], n),
        "attack_after_dodge": stats["attack_after_dodge"],
        "light": stats["light"],
        "heavy": stats["heavy"],
        "attacks": attacks,
        "attacks_per_min": round(attacks / minutes, 1) if minutes > 0 else 0.0,
        "jump": stats["jump"],
        "jump_attack": stats["jump_attack"],
        "fastfall": stats["fastfall"],
        "throw_pickup": stats["throw_pickup"],
        "taunt": stats["taunt"],
        "neutral_priority": stats["neutral_priority"],
    }


def merge_sums(a, b):
    if a is None:
        return b
    out = dict(a)
    out["matches"] = a["matches"] + b["matches"]
    out["minutes"] = round(a["minutes"] + b["minutes"], 2)
    for k in ("dodges", "reactive", "panic_dodges", "attack_after_dodge",
              "light", "heavy", "attacks", "jump", "jump_attack", "fastfall",
              "throw_pickup", "taunt", "neutral_priority",
              "dodges_ground", "dodges_air", "dodges_jump",
              "dashes", "reactive_instant", "reactive_offensive",
              "spontaneous", "pressure_dash"):
        out[k] = a.get(k, 0) + b.get(k, 0)
    dc = Counter(a["dir_counts"]) + Counter(b["dir_counts"])
    out["dir_counts"] = dict(dc)
    ddc = Counter(a.get("dash_dir_counts") or {}) + Counter(b.get("dash_dir_counts") or {})
    out["dash_dir_counts"] = dict(ddc)
    pddc = Counter(a.get("pressure_dash_dir") or {}) + Counter(b.get("pressure_dash_dir") or {})
    out["pressure_dash_dir"] = dict(pddc)
    lat = (a.get("_lat") or []) + (b.get("_lat") or [])
    out["_lat"] = lat
    out["dodge_per_min"] = round(out["dodges"] / out["minutes"], 2) if out["minutes"] else 0.0
    out["dash_per_min"] = round(out["dashes"] / out["minutes"], 2) if out["minutes"] else 0.0
    out["attacks_per_min"] = round(out["attacks"] / out["minutes"], 1) if out["minutes"] else 0.0
    out["reactive_rate"] = pct(out["reactive"], out["dodges"])
    out["panic_rate"] = pct(out["panic_dodges"], out["dodges"])
    out["air_rate"] = pct(out["dodges_air"] + out["dodges_jump"], out["dodges"])
    out["pressure_dash_rate"] = pct(out["pressure_dash"], out["dashes"])
    out["reaction_avg_ms"] = round(sum(lat) / len(lat)) if lat else None
    out["reaction_median_ms"] = median(lat) if lat else None
    return out


def build_dataset(replay_dir, me_name=None, progress=True, only=None):
    """构建数据集。only = 只解析这些文件路径的集合（None = 全部，增量缓存用）。"""
    files = sorted(glob.glob(os.path.join(replay_dir, "*.replay")))
    if only is not None:
        only_set = {os.path.normcase(os.path.abspath(p)) for p in only}
        files = [f for f in files
                 if os.path.normcase(os.path.abspath(f)) in only_set]
    matches = []
    name_counter = Counter()
    parsed = []

    for idx, path in enumerate(files):
        try:
            rep = parse_replay_file(path)
        except (ReplayParseError, EOFError, ValueError) as e:
            if progress:
                print(f"[skip] {os.path.basename(path)}: {e}", file=sys.stderr)
            continue
        gd = rep.get("game_data")
        if not gd or not rep.get("inputs"):
            continue
        settings = gd["settings"]
        entities = gd["entities"]
        if len(entities) != 2 or settings.get("max_players") != 2:
            continue
        for e in entities:
            name_counter[e["name"]] += 1
        parsed.append((path, rep))

    if not parsed:
        raise SystemExit("no 1v1 replays parsed")

    if me_name is None:
        me_name = name_counter.most_common(1)[0][0]

    results = []
    for path, rep in parsed:
        gd = rep["game_data"]
        entities = gd["entities"]
        inputs = rep["inputs"]
        ents = {e["ent_id"]: e for e in entities}
        ids = [e["ent_id"] for e in entities]
        if len(ids) != 2:
            continue
        a, b = ids
        if a not in inputs or b not in inputs:
            continue
        if not inputs[a] or not inputs[b]:
            continue    # 空输入流（全程无按键记录）无法定时长，跳过防 IndexError
        # 比赛时长：以输入流最后时间戳为准。
        # 注意：results.length 在部分回放里是脏数据（写成 28 分钟，实际输入只有 18 秒），
        # 所以只在它和输入流长度大致吻合时才采纳。
        end_ms = max(inputs[a][-1][0], inputs[b][-1][0])
        length_ms = end_ms
        if rep["results"]:
            rl = max(r["length"] for r in rep["results"])
            if rl <= end_ms * 1.5:
                length_ms = max(length_ms, rl)
        minutes = length_ms / 60000.0
        if length_ms < 30000:      # 太短（掉线/秒退）不计入习惯统计
            continue

        seqs = {a: inputs[a], b: inputs[b]}
        stats = {k: analyse_entity(v, length_ms) for k, v in seqs.items()}
        lat = {
            a: match_reactive(stats[a], attack_timeline(seqs[b])),
            b: match_reactive(stats[b], attack_timeline(seqs[a])),
        }
        sums = {k: summarize(stats[k], minutes, lat[k]) for k in (a, b)}
        for k in (a, b):
            sums[k]["_lat"] = lat[k]

        a_name, b_name = ents[a]["name"], ents[b]["name"]
        me_id = a if a_name == me_name else (b if b_name == me_name else None)
        opp_id = b if me_id == a else (a if me_id == b else None)

        # 胜负：results.scores 是"名次"（1=第一名，2=第二名），不是比分——
        # 252 场有效对局上与"死亡次数更少者"100% 一致（v2 实测，见 probe 记录）。
        # 旧版 max(scores) 选出的是败者，胜负全部颠倒。
        # 名次不可识别时（异常值/平局）退回"死亡次数少者"。
        winner = None
        scores = {}
        if rep["results"]:
            scores = rep["results"][0]["scores"]
            vals = set(scores.values())
            if len(scores) == 2 and vals <= {1, 2} and len(vals) == 2:
                winner = min(scores, key=lambda k2: scores[k2])
            else:
                ko_list = rep.get("deaths") or []
                dc = Counter(d["ent_id"] for d in ko_list)
                cands = [e for e in ents if dc.get(e, 0) != max(dc.get(x, 0) for x in ents)]
                if len(cands) == 1:
                    winner = cands[0]

        deaths = rep["deaths"] or []
        results.append({
            "file": os.path.basename(path),
            "path": path,
            "mtime": os.path.getmtime(path),
            "version": rep["version"],
            "level_id": gd["level_id"],
            "playlist": (rep["header"] or {}).get("playlist_name"),
            "online": (rep["header"] or {}).get("online_game"),
            "duration_ms": length_ms,
            "minutes": round(minutes, 2),
            "players": {
                a_name: {
                    "ent_id": a,
                    "hero": ents[a]["player_data"]["heroes"][0]["hero_id"],
                    "sum": sums[a],
                    "score": scores.get(a),
                    "deaths": sum(1 for d in deaths if d["ent_id"] == a),
                },
                b_name: {
                    "ent_id": b,
                    "hero": ents[b]["player_data"]["heroes"][0]["hero_id"],
                    "sum": sums[b],
                    "score": scores.get(b),
                    "deaths": sum(1 for d in deaths if d["ent_id"] == b),
                },
            },
            "me": me_name if me_id is not None else None,
            "opponent": (b_name if me_id == a else a_name) if me_id is not None else None,
            "winner_name": (ents[winner]["name"] if winner in ents else None),
            "deaths": deaths,
        })
        if progress and (idx + 1) % 50 == 0:
            print(f"  parsed {idx + 1}/{len(files)}", file=sys.stderr)

    return {"me": me_name, "matches": results, "name_counts": dict(name_counter),
            "total_files": len(files)}


def aggregate_by_opponent(ds):
    """按对手名字聚合（只聚合"对手"这一侧的画像）。"""
    me = ds["me"]
    agg = {}
    self_agg = None
    for m in ds["matches"]:
        opp = m["opponent"]
        if not opp:
            continue
        opp_sum = m["players"][opp]["sum"]
        entry = agg.setdefault(opp, {
            "name": opp,
            "matches": 0, "wins": 0, "losses": 0,
            "minutes": 0.0,
            "sum": None,
            "heroes": Counter(),
            "maps": Counter(),
            "history": [],   # 按时间正序的每场交锋 {date, minutes, win, file}
        })
        entry["matches"] += 1
        entry["minutes"] = round(entry["minutes"] + m["minutes"], 2)
        entry["sum"] = merge_sums(entry["sum"], dict(opp_sum))
        entry["heroes"][m["players"][opp]["hero"]] += 1
        entry["maps"][m["level_id"]] += 1
        if m["winner_name"] == opp:
            entry["losses"] += 1
            entry["history"].append({"date": m["mtime"], "minutes": m["minutes"],
                                     "win": False, "file": m["file"]})
        elif m["winner_name"] == me:
            entry["wins"] += 1
            entry["history"].append({"date": m["mtime"], "minutes": m["minutes"],
                                     "win": True, "file": m["file"]})
        else:
            entry["history"].append({"date": m["mtime"], "minutes": m["minutes"],
                                     "win": None, "file": m["file"]})

        ms = m["players"].get(me)
        if ms:
            self_agg = merge_sums(self_agg, dict(ms["sum"]))
            self_agg["_matches"] = self_agg.get("_matches", 0) + 1

    for e in agg.values():
        e["heroes"] = dict(e["heroes"])
        e["maps"] = dict(e["maps"])
        e["history"].sort(key=lambda h: h["date"])
    return agg, self_agg


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else __import__("bh_paths").replay_dir()
    ds = build_dataset(d)
    print("me =", ds["me"], "| files:", ds["total_files"], "| 1v1 matches:", len(ds["matches"]))
    agg, me_sum = aggregate_by_opponent(ds)
    print("\n对手数:", len(agg))
    for name, e in sorted(agg.items(), key=lambda kv: -kv[1]["matches"])[:15]:
        s = e["sum"]
        print(f"{name:22s} {e['matches']:3d}局  {s['dodges']:5d}闪避  "
              f"{s['dodge_per_min']:5.1f}/min  受压闪避率{s['reactive_rate']:5.1f}%  "
              f"反应{s['reaction_avg_ms']}ms  原地闪{pct(s['dir_counts'].get('spot', 0), s['dodges']):4.1f}%")
