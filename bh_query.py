"""对手画像速查：排位前 3 秒查出"这人怎么闪"。

用法：
    python bh_query.py                列出最常遇到的对手 Top 25
    python bh_query.py BCK            模糊匹配对手名（大小写不敏感、子串）
    python bh_query.py --self         看自己的画像
    python bh_query.py --all          导出全部对手速查卡到文本文件

数据来源：和 HTML 报告完全同一条管线（build_dataset -> aggregate_by_opponent），
所以这里看到的每个数字，底下的输入流都是 293/293 字节级往返验证过的。
v2：胜负判定已修正（scores=名次）；阈值与 HTML 报告统一。
"""

import os
import sys
import time

if sys.stdout is not None:          # 打包为窗口程序时 stdout 可能是 None
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import cache as bh_cache
from analyze import build_dataset, aggregate_by_opponent, pct, DIR_LABEL
from legends import legend_name

from bh_paths import replay_dir, out_dir
REPLAY_DIR = replay_dir()
OUT_DIR = out_dir()

# 九桶的排列顺序（热力面板用）
PAD_ORDER = ["up_left", "up", "up_right", "left", "spot", "right",
             "down_left", "down", "down_right"]
HORIZONTAL = ("left", "right", "up_left", "up_right", "down_left", "down_right")
VERTICAL = ("up", "down")


def bar(frac, width=22, fill="#", empty="."):
    n = int(round(frac * width))
    return fill * n + empty * (width - n)


def direction_block(dir_counts, total):
    """九桶方向分布 + 3x3 面板。"""
    lines = []
    counts = {k: dir_counts.get(k, 0) for k in PAD_ORDER}
    top = max(counts.values()) if counts else 0
    for k in PAD_ORDER:
        c = counts[k]
        p = pct(c, total)
        frac = (c / top) if top else 0
        lines.append(f"    {DIR_LABEL[k]:12s} {bar(frac):22s} {c:5d}  {p:5.1f}%")
    return lines, counts


def advice(counts, s, total, pron="他"):
    """根据画像生成针对性建议。返回 (结论列表, 建议列表)。
    v4：counts=真闪避方向（地面 dash 已剔除），阈值与 report.advice_for 一致。"""
    concl, tips = [], []

    horiz = sum(counts[k] for k in HORIZONTAL)
    vert = sum(counts[k] for k in VERTICAL)
    spot = counts["spot"]
    left = counts["left"] + counts["up_left"] + counts["down_left"]
    right = counts["right"] + counts["up_right"] + counts["down_right"]
    lr_bias = pct(max(left, right), horiz) if horiz else 0
    side = "左" if left > right else "右"

    concl.append(f"真闪避 {total} 次（横向 {pct(horiz, total)}%，垂直 {pct(vert, total)}%），"
                 f"另有地面 dash {s.get('dashes', 0)} 次（{s.get('dash_per_min', 0)}/min，位移非闪避）")
    if horiz and lr_bias >= 60:
        concl.append(f"横向闪避里偏向{side}（{lr_bias}%），不是均匀乱闪")
    if spot >= total * 0.25:
        concl.append(f"真·原地闪 {pct(spot, total)}%——站桩闪型对手")

    rr = s["reactive_rate"]
    lat = s["reaction_median_ms"]
    if rr >= 35:
        tips.append(f"纯受压闪避率 {rr}%，反应型：多用假动作/收招骗他先闪，再抓落地硬直")
    elif rr <= 12:
        tips.append(f"纯受压闪避率只有 {rr}%，闪避不跟你的节奏：少做假动作，直接抢线压空档")
    else:
        tips.append(f"纯受压闪避率 {rr}% 属正常区间：正常读招即可")

    inst = pct(s.get("reactive_instant", 0), total)
    if inst >= 20:
        tips.append(f"瞬时按键 {inst}%：常和你出招同一瞬间闪，是预输入不是反应——变节奏（慢出招/二段确认）让预判落空")
    off = pct(s.get("reactive_offensive", 0), total)
    if off >= 20:
        tips.append(f"进攻性位移 {off}%：不少'闪避'是他出招后的追击闪/贴脸 dash，是压你的动作——出招后准备接他身位")

    if lat is not None:
        if lat <= 160:
            tips.append(f"反应延迟中位数 {lat}ms（很快）：少用肉眼可见的大前摇，改抓闪避后的固定硬直")
        elif lat >= 210:
            tips.append(f"反应延迟中位数 {lat}ms（偏慢）：快起手/无前摇招能打出措手不及")
        else:
            tips.append(f"反应延迟中位数 {lat}ms：普通人类水平，拼读招不拼手速")

    pdr = s.get("pressure_dash_rate", 0)
    if s.get("dashes", 0) and pdr >= 20:
        pdd = s.get("pressure_dash_dir") or {}
        top = max(pdd.items(), key=lambda kv: kv[1]) if pdd else ("right", 0)
        tips.append(f"受压 dash 逃逸 {pdr}%，主要往{DIR_LABEL.get(top[0], top[0])}"
                    f"（{pct(top[1], s.get('pressure_dash', 1))}%）：dash 全程无无敌帧，抢路线直接打")
    if s.get("dash_per_min", 0) >= 30:
        tips.append(f"dash {s['dash_per_min']}/min（高机动）：地面 dash 无无敌帧，预判路线打点比等他闪避有效")

    if s["panic_rate"] >= 25:
        tips.append(f"1 秒内连闪率 {s['panic_rate']}%：他被压时会乱闪，第一段闪后别停，往第二个落点再压")

    dpm = s.get("dodge_per_min", 0)
    if dpm >= 20:
        tips.append(f"真闪避 {dpm}/min（很高）：极依赖闪避保命，盯冷却（地面1s/空中2.7s），刚闪完是最佳进攻窗口")
    elif 0 < dpm <= 8:
        tips.append(f"真闪避 {dpm}/min（低）：几乎不闪，正面硬拼/拼招可行")

    if s["attack_after_dodge"] and s["dodges"]:
        aad = pct(s["attack_after_dodge"], s["dodges"])
        if aad >= 25:
            tips.append(f"闪后 {aad}% 立刻反打：闪避不是逃跑信号，他闪完你要准备接招")
    if s["jump"] and s["dodges"]:
        jp = pct(s["jump"], s["dodges"])
        if jp >= 40:
            tips.append(f"跳跃/闪避比 {jp}%：{pron}很爱跳，空中抓")
    return concl, tips


def render(name, e):
    s = e["sum"]
    total = s["dodges"] or 1
    w, l = e.get("wins"), e.get("losses")
    if w is None or l is None:
        h2h = "--"
    else:
        h2h = f"{w}胜 {l}负" + (f"（胜率 {pct(w, w + l)}%）" if w + l else "")

    lines = []
    lines.append("=" * 68)
    lines.append(f"  {name}")
    lines.append(f"  {e['matches']} 局 / {round(e['minutes'], 1)} 分钟   对你战绩：{h2h}")
    lines.append("=" * 68)
    lines.append("")
    lines.append("【闪避方向分布】")
    block, counts = direction_block(s["dir_counts"], s["dodges"])
    lines.extend(block)
    lines.append("")
    lines.append("【关键指标】(v4 口径：真闪避不含地面 dash)")
    lat = s["reaction_median_ms"]
    lat_s = f"{lat} ms" if lat is not None else "n/a"
    lines.append(f"    真闪避            {s['dodges']:6d}  ({s['dodge_per_min']:.1f} 次/分钟)")
    lines.append(f"    地面 dash         {s.get('dashes', 0):6d}  "
                 f"({s.get('dash_per_min', 0):.1f} 次/分钟，位移非闪避)")
    lines.append(f"    纯受压闪避率      {s['reactive_rate']:6.1f}%   "
                 f"(你出招后 {80}-{400}ms 内的真闪避)")
    lines.append(f"      ├ 瞬时按键      {pct(s.get('reactive_instant', 0), s['dodges']):5.1f}%"
                 f"   (<80ms，同按/预输入，不是反应)")
    lines.append(f"      ├ 进攻性位移    {pct(s.get('reactive_offensive', 0), s['dodges']):5.1f}%"
                 f"   (他出招后的追击闪/dash，不是逃你)")
    lines.append(f"      └ 自发          {pct(s.get('spontaneous', 0), s['dodges']):5.1f}%")
    lines.append(f"    反应延迟中位数    {lat_s}   (仅纯防御样本)")
    lines.append(f"    1 秒内连闪率      {s['panic_rate']:6.1f}%")
    aad = pct(s["attack_after_dodge"], s["dodges"])
    lines.append(f"    闪后立刻反打率    {aad:6.1f}%")
    g = pct(s.get("dodges_ground", 0), total)
    a = pct(s.get("dodges_air", 0), total)
    j = pct(s.get("dodges_jump", 0), total)
    lines.append(f"    真闪避构成        地面原地 {g:.1f}% / 空中 {a:.1f}% / 起跳接闪 {j:.1f}%")
    ddc = s.get("dash_dir_counts") or {}
    if s.get("dashes"):
        tops = sorted(ddc.items(), key=lambda kv: -kv[1])[:3]
        lines.append("    dash 方向 top3    " + ", ".join(
            f"{DIR_LABEL[k]} {pct(v, s['dashes'])}%" for k, v in tops))
    lines.append(f"    受压 dash 率      {s.get('pressure_dash_rate', 0):5.1f}%"
                 f"   (被压时用 dash 逃逸 / 全部 dash)")
    lines.append(f"    轻/重攻击         {s['light']}/{s['heavy']}  "
                 f"(攻击频率 {s.get('attacks_per_min', 0)}/min)")
    lines.append(f"    跳跃 / 快落 / 嘲讽 {s['jump']} / {s['fastfall']} / {s['taunt']}")
    if e.get("heroes"):
        top_hero = max(e["heroes"], key=e["heroes"].get)
        lines.append(f"    最常用英雄        {legend_name(top_hero)} "
                     f"(hero_id={top_hero}, {e['heroes'][top_hero]} 局)")
    lines.append("")
    concl, tips = advice(counts, s, total, pron="你" if "（你自己）" in name else "他")
    lines.append("【画像结论】")
    for c in concl:
        lines.append(f"    - {c}")
    lines.append("")
    lines.append("【怎么打他】")
    for i, t in enumerate(tips, 1):
        lines.append(f"    {i}. {t}")
    lines.append("")
    return "\n".join(lines)


def load_dataset():
    """带增量缓存的数据集加载。

    首次全量解析并写缓存；之后只解析指纹变化的文件，其余复用。
    返回 (ds, stats_str)。缓存损坏/异常时安全降级为全量解析。
    """
    t0 = time.time()
    cache_path = os.path.join(OUT_DIR, "cache.json")
    all_files = sorted(
        os.path.join(REPLAY_DIR, f) for f in os.listdir(REPLAY_DIR)
        if f.lower().endswith(".replay")
    ) if os.path.isdir(REPLAY_DIR) else []

    fps = {}
    for p in all_files:
        try:
            fps[p] = bh_cache.fingerprint(p)
        except OSError:
            pass

    cached = bh_cache.load_cache(cache_path)
    if cached is not None:
        _cached_dir = os.path.normcase(os.path.normpath(
            cached.get("replay_dir") or ""))
        _cur_dir = os.path.normcase(os.path.normpath(REPLAY_DIR))
        if _cached_dir != _cur_dir:
            # 缓存属于另一个回放目录（BH_REPLAY_DIR 被改过）——作废重建，
            # 否则旧目录的 me 判定会串到新目录上（斜杠风格差异不算改目录）
            cached = None
    if cached is not None:
        old_files = cached.get("files") or {}
        # 未变化的文件直接复用；新增/改动的重解析；已删除的丢弃
        fresh = [p for p, fp in fps.items() if old_files.get(p) != fp]
        reuse = {p: m for p, m in zip(
            [m["path"] for m in cached["matches"]],
            cached["matches"]) if old_files.get(p) == fps.get(p)}
        me_name = cached.get("me")
        try:
            new_ds = build_dataset(REPLAY_DIR, me_name=me_name,
                                   progress=False, only=fresh)
            new_matches = new_ds["matches"]
        except SystemExit:
            new_matches = []
        matches = list(reuse.values()) + new_matches
        if not matches:
            # 缓存内容不匹配当前目录（比如目录被换），全量重建
            cached = None
        else:
            # name_counts / me 从合并后的全量 matches 重算（纯内存，很快）
            from collections import Counter
            nc = Counter()
            for m in matches:
                nc.update(m["players"].keys())
            me = me_name or (nc.most_common(1)[0][0] if nc else None)
            ds = {"me": me, "matches": matches,
                  "name_counts": dict(nc), "total_files": len(fps)}
            dt = time.time() - t0
            if fresh:   # 无新解析时跳过回写（整个数据集 JSON 可达数 MB）
                bh_cache.save_cache(cache_path, ds, fps, replay_dir=REPLAY_DIR)
            stats = (f"{len(matches)} 场 / {len(fresh)} 个新解析"
                     f" / {len(reuse)} 个缓存命中 / {dt:.1f}s")
            return ds, stats

    # 全量路径
    ds = build_dataset(REPLAY_DIR)
    bh_cache.save_cache(cache_path, ds, fps, replay_dir=REPLAY_DIR)
    dt = time.time() - t0
    return ds, f"{len(ds['matches'])} 场 / 全量解析 / {dt:.1f}s"


def main():
    args = [a for a in sys.argv[1:]]
    want_self = "--self" in args
    want_all = "--all" in args
    terms = [a for a in args if not a.startswith("--")]

    if not os.path.isdir(REPLAY_DIR):
        print(f"[!] 回放目录不存在: {REPLAY_DIR}")
        return 1

    ds, stats = load_dataset()
    agg, me_sum = aggregate_by_opponent(ds)
    me = ds["me"]
    print(f"{stats} / 我 = {me}", file=sys.stderr)

    if terms:
        # 模糊匹配：子串 + 大小写不敏感；多个词是"与"关系
        hits = {}
        for name, e in agg.items():
            low = name.lower()
            if all(t.lower() in low for t in terms):
                hits[name] = e
        if not hits:
            # 再试单个词的部分匹配（防止大小写/空格差异）
            for name, e in agg.items():
                low = name.lower()
                if any(t.lower() in low for t in terms):
                    hits[name] = e
        if not hits:
            print(f"没有找到匹配 {' + '.join(terms)!r} 的对手。")
            print("\n全部对手名：")
            for name in sorted(agg):
                print("   ", name)
            return 1
        for name in sorted(hits, key=lambda n: -hits[n]["matches"]):
            print(render(name, hits[name]))
        return 0

    if want_self:
        if not me_sum:
            print("没有找到你自己的对局数据。")
            return 1
        e = {"name": me, "matches": me_sum.get("_matches", 1), "wins": None, "losses": None,
             "minutes": me_sum["minutes"], "sum": me_sum, "heroes": {}, "maps": {}}
        print(render(f"{me}（你自己）", e))
        return 0

    if want_all:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, "opponents.txt")
        with open(path, "w", encoding="utf-8") as f:
            for name in sorted(agg, key=lambda n: -agg[n]["matches"]):
                f.write(render(name, agg[name]) + "\n")
        print(f"已导出 {len(agg)} 张速查卡 -> {path}")
        return 0

    # 默认：Top 25 速览表
    print()
    print(f"{'对手':<24}{'局':>4}{'战绩':>10}{'真闪':>7}{'/min':>7}"
          f"{'纯受压':>7}{'反应':>7}{'原地':>6}")
    print("-" * 74)
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["matches"], kv[0]))
    for name, e in ranked[:25]:
        s = e["sum"]
        wl = f"{e['wins']}-{e['losses']}"
        spot = pct(s["dir_counts"].get("spot", 0), s["dodges"])
        lat = s["reaction_median_ms"]
        lat_s = f"{lat}" if lat is not None else "-"
        print(f"{name:<24}{e['matches']:>4}{wl:>10}{s['dodges']:>7}"
              f"{s['dodge_per_min']:>7.1f}{s['reactive_rate']:>6.1f}%{lat_s:>6}{spot:>5.1f}%")
    print()
    print(f"共 {len(agg)} 个对手。查单个：python bh_query.py <名字关键词>")
    print("看自己：python bh_query.py --self    全部导出：python bh_query.py --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
