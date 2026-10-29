# -*- coding: utf-8 -*-
"""生成 HTML 报告 v6：对手闪避习惯画像 + 针对性打法建议。

UI 设计语言移植自 shadcn/ui（github.com/shadcn-ui/ui）官方 theming 规范：
  - 中性设计令牌（background/foreground/card/muted/border/ring），亮暗双主题
  - 发丝级 1px 边框 + 单一圆角半径派生，无阴影、无渐变、无发光
  - 数值用字重与字号突出，不靠彩色；彩色仅保留给图表（chart-1..5）与语义色
  - bg-card 卡片、bg-muted 次级表面、muted-foreground 辅助文本
数据口径（v4 算法）不变：真闪避 vs 地面 dash、受压意图四分类、胜负=名次。
单文件自包含（无外部资源），离线可用；主题选择记忆在 localStorage。
"""

import os
import json
import html
import datetime

from analyze import (
    build_dataset, aggregate_by_opponent, pct, median,
    DIR_LABEL, PAD_LAYOUT, merge_sums,
    REACT_WINDOW_MS, REACT_MIN_MS, CHASE_CTX_MS,
)
from legends import legend_name

from bh_paths import out_dir
OUT_DIR = out_dir()

PAD_ARROWS = {"up_left": "↖", "up": "↑", "up_right": "↗",
              "left": "←", "spot": "○", "right": "→",
              "down_left": "↙", "down": "↓", "down_right": "↘"}


def esc(x):
    return html.escape(str(x))


def intent_bar(s):
    """受压意图堆叠条：纯防御 / 瞬时 / 进攻性 / 自发（chart 色板）。"""
    n = s.get("dodges", 0)
    if not n:
        return ""
    colors = {"defensive": "var(--chart-2)", "instant": "var(--chart-4)",
              "offensive": "var(--destructive)", "spontaneous": "var(--border)"}
    labels = {"defensive": "纯防御", "instant": "瞬时按键",
              "offensive": "进攻性", "spontaneous": "自发"}

    def val(key):
        if key == "defensive":
            return s.get("reactive", 0)
        if key == "spontaneous":
            return s.get("spontaneous", 0)
        return s.get(f"reactive_{key}", 0)

    parts = ['<div class="stack">']
    for key in ("defensive", "instant", "offensive", "spontaneous"):
        v = val(key)
        if v:
            parts.append(f'<div class="stack-seg" style="width:{pct(v, n)}%;'
                         f'background:{colors[key]}" title="{labels[key]}：{v} 次'
                         f'（{pct(v, n)}%）"></div>')
    parts.append('</div><div class="stack-legend">')
    for key in ("defensive", "instant", "offensive", "spontaneous"):
        v = val(key)
        parts.append(f'<span class="dot" style="background:{colors[key]}"></span>'
                     f'{labels[key]} {pct(v, n)}%')
    parts.append("</div>")
    return '<div class="block">' + "".join(parts) + "</div>"


def dir_pad(dir_counts, total, title=""):
    """3x3 方向热力面板（按下帧方向；热力用 chart-1 透明度，文本保持中性）。"""
    if not total:
        return '<div class="muted">无数据</div>'
    cells = []
    maxv = max((dir_counts.get(k, 0) for row in PAD_LAYOUT for k in row), default=0) or 1
    for row in PAD_LAYOUT:
        cells.append('<div class="pad-row">')
        for k in row:
            v = dir_counts.get(k, 0)
            p = pct(v, total)
            heat = (v / maxv) if maxv else 0
            a = 0.05 + 0.80 * heat
            hi = heat > 0.5
            fg = "#fff" if hi else "var(--foreground)"
            sub = "rgba(255,255,255,.75)" if hi else "var(--muted-foreground)"
            cells.append(
                f'<div class="pad-cell" style="background:rgba(234,88,12,{a:.3f})" '
                f'title="{DIR_LABEL[k]}：{v} 次，占 {p:.1f}%">'
                f'<div class="pad-arrow" style="color:{fg}">{PAD_ARROWS[k]}</div>'
                f'<div class="pad-p" style="color:{fg}">{p:.1f}%</div>'
                f'<div class="pad-n" style="color:{sub}">{v}</div></div>'
            )
        cells.append("</div>")
    legend = ('<div class="pad-legend">颜色越深占比越高 · 按下闪避键瞬间按住的方向'
              '（屏幕绝对方向）</div>')
    t = f'<div class="pad-title">{title}</div>' if title else ""
    return '<div class="pad">' + t + "".join(cells) + legend + "</div>"


def lr_bias(dir_counts):
    """左右偏向条（chart-3 左 / chart-1 右）。"""
    left = sum(dir_counts.get(k, 0) for k in ("left", "up_left", "down_left"))
    right = sum(dir_counts.get(k, 0) for k in ("right", "up_right", "down_right"))
    t = left + right
    if not t:
        return ""
    lp, rp = pct(left, t), pct(right, t)
    tip = f"横向里 左 {left} : 右 {right}"
    return (f'<div class="lrwrap" title="{tip}">'
            f'<div class="lr-half"><div class="lr-fill" style="width:{lp}%;'
            f'background:var(--chart-3)"><span>← {lp}%</span></div></div>'
            f'<div class="lr-half"><div class="lr-fill" style="width:{rp}%;'
            f'background:var(--chart-1)"><span>{rp}% →</span></div></div></div>')


def cat_split(s):
    """真闪避构成堆叠条：地面原地闪 / 空中闪 / 起跳接闪。"""
    n = s.get("dodges", 0)
    if not n:
        return ""
    segs = [("地面原地闪", s.get("dodges_ground", 0), "var(--chart-3)"),
            ("空中闪", s.get("dodges_air", 0), "var(--chart-4)"),
            ("起跳接闪", s.get("dodges_jump", 0), "var(--chart-2)")]
    parts = ['<div class="stack">']
    for label, v, color in segs:
        w = pct(v, n)
        parts.append(f'<div class="stack-seg" style="width:{w}%;background:{color}" '
                     f'title="{label}：{v} 次（{w}%）"></div>')
    parts.append('</div><div class="stack-legend">')
    for label, v, color in segs:
        parts.append(f'<span class="dot" style="background:{color}"></span>'
                     f'{label} {pct(v, n)}%')
    parts.append("</div>")
    return '<div class="block">' + "".join(parts) + "</div>"


def hist_svg(latencies, width=560, height=150, buckets=8):
    """反应延迟直方图（SVG）：只含纯防御闪避（>= REACT_MIN_MS）。"""
    lat = [x for x in latencies if REACT_MIN_MS <= x <= REACT_WINDOW_MS]
    if len(lat) < 3:
        return '<div class="muted">纯防御样本不足</div>'
    lo, hi = REACT_MIN_MS, REACT_WINDOW_MS
    step = (hi - lo) / buckets
    counts = [0] * buckets
    for x in lat:
        counts[min(int((x - lo) / step), buckets - 1)] += 1
    mx = max(counts) or 1
    bw = width / buckets
    med = median(lat)
    pad_l = 6
    parts = [f'<svg viewBox="0 0 {width + pad_l * 2} {height + 40}" width="100%" '
             f'style="max-width:{width + pad_l * 2}px" role="img">']
    for gy in (0.25, 0.5, 0.75):
        y = height - (height - 8) * gy
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width + pad_l}" y2="{y:.1f}" '
                     f'stroke="var(--border)" stroke-width="1"/>')
    for i, c in enumerate(counts):
        h = (height - 8) * c / mx
        x = pad_l + i * bw
        parts.append(
            f'<rect x="{x + 2:.1f}" y="{height - h:.1f}" width="{bw - 4:.1f}" height="{h:.1f}" '
            f'fill="var(--chart-1)" rx="3"><title>{int(lo + i * step)}–{int(lo + (i + 1) * step)}ms：'
            f'{c} 次</title></rect>')
        if i % 2 == 0:
            parts.append(
                f'<text x="{x + bw / 2:.1f}" y="{height + 14}" font-size="10" '
                f'fill="var(--muted-foreground)" text-anchor="middle">{int(lo + i * step)}</text>')
    mx_pos = pad_l + (min(max(med, lo), hi) - lo) / (hi - lo) * width
    parts.append(f'<line x1="{mx_pos:.1f}" y1="0" x2="{mx_pos:.1f}" y2="{height}" '
                 f'stroke="var(--foreground)" stroke-width="1.5" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{mx_pos + 4:.1f}" y="12" font-size="11" '
                 f'fill="var(--foreground)">中位 {med}ms</text>')
    parts.append(f'<text x="{(width + pad_l * 2) / 2}" y="{height + 32}" font-size="10" '
                 f'fill="var(--muted-foreground)" text-anchor="middle">对手出招 → 纯防御闪避 的延迟 (ms)'
                 f' · 仅统计 {lo}–{hi}ms 反应窗内的真闪避</text>')
    parts.append("</svg>")
    return "".join(parts)


def sparkline_weekly(matches, me, weeks=10, width=560, height=110):
    """近期状态：按周胜率折线（仅含我参与且分出胜负的对局）。"""
    by_week = {}
    for m in matches:
        if not m.get("opponent") or m["winner_name"] not in (me, m.get("opponent")):
            continue
        if me not in m["players"]:
            continue
        d = datetime.datetime.fromtimestamp(m["mtime"])
        wk = d.isocalendar()[:2]
        ent = by_week.setdefault(wk, [0, 0])
        ent[0 if m["winner_name"] == me else 1] += 1
    if not by_week:
        return ""
    keys = sorted(by_week)[-weeks:]
    pts = []
    for wk in keys:
        w, l = by_week[wk]
        t = w + l
        pts.append((wk, 100.0 * w / t if t else None, t))
    valid = [(i, r) for i, (_, r, t) in enumerate(pts) if r is not None]
    if len(valid) < 2:
        return ""
    step = width / max(len(pts) - 1, 1)
    coord = [(10 + i * step, height - 14 - (r / 100) * (height - 30)) for i, r in valid]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coord)
    area = (f"M {coord[0][0]:.1f},{height - 14} L "
            + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coord)
            + f" L {coord[-1][0]:.1f},{height - 14} Z")
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="var(--chart-1)">'
                   f'<title>周{pts[i][0][1]}：{pts[i][2]}场 {r:.0f}%</title></circle>'
                   for (x, y), (i, r) in zip(coord, valid))
    last_r = valid[-1][1]
    parts = [f'<svg viewBox="0 0 {width} {height + 16}" width="100%" style="max-width:{width}px">']
    mid_y = height - 14 - 0.5 * (height - 30)
    parts.append(f'<line x1="10" y1="{mid_y:.1f}" x2="{width - 10}" y2="{mid_y:.1f}" '
                 f'stroke="var(--border)" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{width - 10}" y="{mid_y - 4:.1f}" font-size="9" '
                 f'fill="var(--muted-foreground)" text-anchor="end">50%</text>')
    parts.append(f'<path d="{area}" fill="var(--chart-1)" opacity="0.08"/>')
    parts.append(f'<polyline points="{poly}" fill="none" stroke="var(--chart-1)" '
                 f'stroke-width="2"/>')
    parts.append(dots)
    parts.append(f'<text x="10" y="{height + 10}" font-size="10" '
                 f'fill="var(--muted-foreground)">'
                 f'近 {len(pts)} 周胜率走势 · 最近一周 {last_r:.0f}%（'
                 f'{pts[-1][2]} 场）</text>')
    parts.append("</svg>")
    return '<div class="block" style="border:1px solid var(--border);border-radius:' \
           'var(--radius);padding:10px;margin-top:10px">' + "".join(parts) + "</div>"


def form_chips(history, n=5):
    """最近 n 场胜负徽章（旧→新）。"""
    if not history:
        return ""
    chips = []
    for h in history[-n:]:
        if h["win"] is True:
            chips.append('<span class="badge win">胜</span>')
        elif h["win"] is False:
            chips.append('<span class="badge lose">负</span>')
        else:
            chips.append('<span class="badge">平</span>')
    return '<span title="最近交锋（旧→新）">' + "".join(chips) + "</span>"


def advice_for(sum_data, is_opponent=True, lang="zh"):
    """根据画像生成中文/英文打法建议（v4 阈值，基于真闪避语义定标）。"""
    zh = (lang != "en")
    T = _ADVICE_TEXT[lang]
    tips = []
    n = sum_data.get("dodges", 0)
    nd = sum_data.get("dashes", 0)
    pron = T["pron_opp"] if is_opponent else T["pron_self"]
    if n < 15:
        return [T["small_sample"]]
    dc = sum_data.get("dir_counts", {})
    minutes = max(sum_data.get("minutes", 0.01), 0.01)
    spot_p = pct(dc.get("spot", 0), n)
    vert = {
        "up": dc.get("up", 0) + dc.get("up_left", 0) + dc.get("up_right", 0),
        "down": dc.get("down", 0) + dc.get("down_left", 0) + dc.get("down_right", 0),
    }
    left = dc.get("left", 0) + dc.get("up_left", 0) + dc.get("down_left", 0)
    right = dc.get("right", 0) + dc.get("up_right", 0) + dc.get("down_right", 0)
    horiz = left + right

    if spot_p >= 40:
        tips.append(T["spot40"].format(p=spot_p, pron=pron))
    elif spot_p >= 25:
        tips.append(T["spot25"].format(p=spot_p, pron=pron))
    if vert["up"] > vert["down"] and pct(vert["up"], n) >= 30:
        tips.append(T["up"].format(p=pct(vert["up"], n), pron=pron))
    if vert["down"] >= vert["up"] and pct(vert["down"], n) >= 30:
        tips.append(T["down"].format(p=pct(vert["down"], n), pron=pron))
    if horiz and pct(horiz, n) >= 30:
        bias_side = ("左" if left > right else "右") if zh else                     ("left" if left > right else "right")
        bias = pct(max(left, right), horiz)
        bias_note = (T["bias_note"].format(b=bias, side=bias_side)
                     if bias >= 60 else "")
        tips.append(T["horiz"].format(p=pct(horiz, n), pron=pron,
                                      side=("左" if left > right else "右")
                                      if zh else ("left" if left > right else "right"),
                                      bias=bias_note))
    def_rate = sum_data.get("reactive_rate", 0)
    inst_share = pct(sum_data.get("reactive_instant", 0), n)
    off_share = pct(sum_data.get("reactive_offensive", 0), n)
    if def_rate >= 35:
        tips.append(T["react_hi"].format(p=def_rate, pron=pron))
    elif def_rate <= 12:
        tips.append(T["react_lo"].format(p=def_rate, pron=pron))
    if inst_share >= 20:
        tips.append(T["instant"].format(p=inst_share, pron=pron))
    if off_share >= 20:
        tips.append(T["offensive"].format(p=off_share, pron=pron))
    rmed = sum_data.get("reaction_median_ms")
    if rmed:
        if rmed <= 160:
            tips.append(T["react_fast"].format(ms=rmed))
        elif rmed >= 210:
            tips.append(T["react_slow"].format(ms=rmed))
    pd_n = sum_data.get("pressure_dash", 0)
    if nd and pd_n and sum_data.get("pressure_dash_rate", 0) >= 20:
        pdd = sum_data.get("pressure_dash_dir") or {}
        top_pd = max(pdd.items(), key=lambda kv: kv[1]) if pdd else ("right", 0)
        tips.append(T["pdash"].format(p=sum_data["pressure_dash_rate"], pron=pron,
                                      d=DIR_LABEL.get(top_pd[0], top_pd[0]),
                                      dp=pct(top_pd[1], pd_n)))
    dshpm = sum_data.get("dash_per_min", 0)
    if dshpm >= 30:
        tips.append(T["dash_hi"].format(d=dshpm, pron=pron))
    if sum_data.get("panic_rate", 0) >= 25:
        tips.append(T["panic"].format(p=sum_data["panic_rate"], pron=pron))
    dpm = sum_data.get("dodge_per_min", 0)
    if dpm >= 20:
        tips.append(T["dodge_hi"].format(d=dpm, pron=pron))
    elif 0 < dpm <= 8:
        tips.append(T["dodge_lo"].format(d=dpm, pron=pron))
    aad = sum_data.get("attack_after_dodge", 0)
    if n and pct(aad, n) >= 25:
        tips.append(T["aad"].format(p=pct(aad, n), pron=pron))
    jpm = sum_data.get("jump", 0) / minutes
    if jpm >= 25:
        tips.append(T["jump"].format(j=jpm, pron=pron))
    return tips


_ADVICE_TEXT = {
    "zh": {
        "pron_opp": "他", "pron_self": "你",
        "small_sample": "<b>真闪避样本偏少</b>（< 15 次），结论仅供参考，多打几局再下判断。",
        "spot40": "<b>原地闪 {p}%</b>：{pron}是真·站桩闪型（地面从不带方向闪）。"
                  "别急着抓闪避后的硬直，用范围大的横扫/多段招罩住，"
                  "或出招慢 2–3 帧等 16 帧无敌过完再打。",
        "spot25": "<b>原地闪 {p}%</b>：{pron}常在地面站桩闪。多段招和延迟出招能穿掉"
                  "无敌帧，闪避结束的硬直可以抓。",
        "up": "<b>向上/上斜闪 {p}%</b>：{pron}真闪避多为向上逃（空中闪为主）。"
              "多准备对空招和上方向追击。",
        "down": "<b>向下/下斜闪 {p}%</b>：{pron}真闪避多往下钻，"
                "往落点压下劈/俯冲类招式。",
        "bias_note": "，且 {b}% 偏向{side}",
        "horiz": "<b>横向闪 {p}%</b>{bias}：用横向覆盖大的起手去跟，重点封{side}侧。",
        "react_hi": "<b>纯受压闪避率 {p}%</b>：{pron}是真的反应型——被你出招压住才闪。"
                    "对策：走位/假动作先骗一次闪，再抓闪避结束的硬直，"
                    "别把第一下真打出去。",
        "react_lo": "<b>纯受压闪避率仅 {p}%</b>：{pron}的闪几乎不跟着你的节奏走，"
                    "更多是主动位移。少做假动作，直接抢线压空档。",
        "instant": "<b>瞬时按键 {p}%</b>：{pron}经常和你的出招<b>同一瞬间</b>闪——"
                   "这是预输入/预判，不是反应。骗反应没用，要变节奏：慢出招、"
                   "打二段确认，让他的预判落空。",
        "offensive": "<b>进攻性位移 {p}%</b>：{pron}不少'闪避'其实是自己出招后的"
                     "追击闪/贴脸 dash（chase dodge 类），是压你的动作不是逃你。"
                     "他出招后就准备接他身位，别当他在躲。",
        "react_fast": "<b>反应中位 {ms}ms（快）</b>：慢起手的大招/蓄力容易被闪掉，"
                      "优先用启动快的招和确反连段。",
        "react_slow": "<b>反应中位 {ms}ms（偏慢）</b>：快招、贴身连段、多段攻击的"
                      "后续段更容易吃到。",
        "pdash": "<b>受压 dash 逃逸 {p}%</b>：被压时{pron}更常用地面 dash 跑路"
                 "（真闪避反而不多），主要往{d}（{dp}%）。封那条路线的落点，"
                 "dash 途中无无敌帧可以直接打。",
        "dash_hi": "<b>dash {d}/min（高机动）</b>：{pron}大量用地面 dash 走位，"
                   "地面 dash 没有无敌帧——预判路线直接打点比等他闪避有效得多。",
        "panic": "<b>1 秒内连闪 {p}%</b>：{pron}慌了会连按闪避（多为空连闪）。"
                 "第一段闪后别停，往第二个落点再压一次。",
        "dodge_hi": "<b>真闪避 {d}/min（很高）</b>：{pron}极依赖闪避保命，"
                    "盯紧闪避冷却（地面 1s / 空中 2.7s），刚闪完是最佳进攻窗口。",
        "dodge_lo": "<b>真闪避 {d}/min（低）</b>：{pron}几乎不闪，正面硬拼可行，"
                    "注意{pron}主要靠走位和拼招。",
        "aad": "<b>闪完立刻还击 {p}%</b>：{pron}习惯闪完反打（含 gravity cancel），"
               "追击时留一步距离，别贴脸送确反。",
        "jump": "<b>跳跃 {j:.0f}/min</b>：{pron}很爱待在空中，多准备对空招。",
    },
    "en": {
        "pron_opp": "He", "pron_self": "You",
        "small_sample": "<b>Sample too small</b> (< 15 true dodges) — treat all "
                        "conclusions as rough hints; play a few more matches.",
        "spot40": "<b>Spot dodges {p}%</b>: {pron} genuinely spot-dodge on the "
                  "ground. Don't rush the punish — cover with wide multi-hit moves, "
                  "or delay your attack 2-3 frames until the 16f invulnerability ends.",
        "spot25": "<b>Spot dodges {p}%</b>: {pron} often spot-dodges on the ground. "
                  "Multi-hit and delayed attacks beat the invulnerability; the "
                  "recovery afterwards is punishable.",
        "up": "<b>Up / up-diagonal dodges {p}%</b>: {pron} escape upward "
              "(mostly air dodges). Prepare anti-airs and upward pursuit.",
        "down": "<b>Down / down-diagonal dodges {p}%</b>: {pron} dive downward — "
                "chase the landing spot with spikes / dive attacks.",
        "bias_note": ", {b}% toward {side}",
        "horiz": "<b>Horizontal dodges {p}%</b>{bias}: chase with wide horizontal "
                 "starters and cover the {side} side.",
        "react_hi": "<b>Pressured-dodge rate {p}%</b>: {pron} genuinely react to "
                    "your attacks. Bait the dodge with movement/feints, then punish "
                    "the recovery — don't commit the first hit.",
        "react_lo": "<b>Pressured-dodge rate only {p}%</b>: {pron} barely dodge on "
                    "your rhythm — skip the feints and take space directly.",
        "instant": "<b>Simultaneous presses {p}%</b>: {pron} often dodge on the "
                   "<b>exact frame</b> of your attack — pre-buffered input, not a "
                   "reaction. Change tempo: slow starters and two-hit confirms.",
        "offensive": "<b>Offensive movement {p}%</b>: many of {pron}'s 'dodges' are "
                     "chase dodges / dash-ins after own attacks — pressure, not "
                     "panic. Expect to be challenged right after his swings.",
        "react_fast": "<b>Reaction median {ms}ms (fast)</b>: big startups and "
                      "charged moves get dodged — prefer fast starters and "
                      "sure-confirm punishes.",
        "react_slow": "<b>Reaction median {ms}ms (slow)</b>: fast moves and tight "
                      "strings land more often.",
        "pdash": "<b>Pressured dash escapes {p}%</b>: under pressure {pron} mostly "
                 "dash away instead of dodging, mainly {d} ({dp}%). Cover that "
                 "lane — dashes have no invulnerability, hit them mid-dash.",
        "dash_hi": "<b>Dash {d}/min (high mobility)</b>: {pron} use ground dashes "
                   "heavily; dashes have no invulnerability — predict the route "
                   "and hit the spot instead of waiting for dodges.",
        "panic": "<b>Chained dodges {p}%</b>: {pron} panic-mash dodge when "
                 "pressured. After the first dodge, immediately pressure the "
                 "second landing spot.",
        "dodge_hi": "<b>True dodges {d}/min (very high)</b>: {pron} rely heavily "
                    "on dodges — track cooldowns (ground 1s / air 2.7s); right "
                    "after a dodge is the best window to attack.",
        "dodge_lo": "<b>True dodges {d}/min (low)</b>: {pron} rarely dodge — "
                    "head-on trades are viable; watch out for movement and swings.",
        "aad": "<b>Counter after dodge {p}%</b>: {pron} swing right after dodging "
               "(incl. gravity cancel) — keep a step of distance when chasing.",
        "jump": "<b>Jumps {j:.0f}/min</b>: {pron} live in the air — bring anti-airs.",
    },
}


def kpi(label, value, sub=""):
    """shadcn 风格统计卡：label(muted) 在上，value(2xl semibold) 在下，sub 独立行。"""
    s = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (f'<div class="kpi"><div class="kpi-k">{label}</div>'
            f'<div class="kpi-v">{value}</div>{s}</div>')


CSS = """
/* ---- shadcn/ui design tokens (theme: neutral) ---- */
:root{
  --radius:10px;
  --background:#ffffff;--foreground:#0a0a0a;
  --card:#ffffff;--card-foreground:#0a0a0a;
  --primary:#262626;--primary-foreground:#fafafa;
  --secondary:#f5f5f5;--secondary-foreground:#262626;
  --muted:#f5f5f5;--muted-foreground:#737373;
  --accent:#f5f5f5;--accent-foreground:#262626;
  --destructive:#dc2626;--destructive-fg:#b91c1c;
  --border:#e5e5e5;--input:#e5e5e5;--ring:#a3a3a3;
  --chart-1:#ea580c;--chart-2:#0d9488;--chart-3:#3b6a9e;
  --chart-4:#ca8a04;--chart-5:#c2564a;
  --win:#15803d;--lose:#737373;
}
.dark{
  --background:#0a0a0a;--foreground:#fafafa;
  --card:#171717;--card-foreground:#fafafa;
  --primary:#e5e5e5;--primary-foreground:#1a1a1a;
  --secondary:#262626;--secondary-foreground:#e5e5e5;
  --muted:#262626;--muted-foreground:#a3a3a3;
  --accent:#262626;--accent-foreground:#e5e5e5;
  --destructive:#ef4444;--destructive-fg:#f87171;
  --border:rgba(255,255,255,.10);--input:rgba(255,255,255,.15);--ring:#a3a3a3;
  --chart-1:#4f5df0;--chart-2:#10b981;--chart-3:#f59e0b;
  --chart-4:#a855f7;--chart-5:#ef4444;
  --win:#4ade80;--lose:#a3a3a3;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--background);color:var(--foreground);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",
  "Microsoft YaHei UI","PingFang SC",sans-serif;
  font-size:14px;line-height:1.6;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 80px}
a{color:inherit;text-decoration:none}
h1{font-size:20px;font-weight:600;letter-spacing:-.01em;margin:0}
h2{font-size:16px;font-weight:600;letter-spacing:-.01em;margin:40px 0 12px;
  display:flex;align-items:baseline;gap:8px}
h2 .no{font-size:12px;color:var(--muted-foreground);font-weight:500;
  font-variant-numeric:tabular-nums}
h3{font-size:14px;font-weight:600;margin:18px 0 8px}
.muted{color:var(--muted-foreground);font-size:13px}
.sub{color:var(--muted-foreground);font-size:13.5px;margin-top:4px}
/* 头部 */
.head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.iconbtn{width:34px;height:34px;border:1px solid var(--border);border-radius:8px;
  background:transparent;color:var(--foreground);font-size:15px;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center}
.iconbtn:hover{background:var(--accent)}
/* Tabs 导航（shadcn tabs：下划线式） */
.tabs{position:sticky;top:0;z-index:9;display:flex;gap:2px;margin:20px -24px 0;
  padding:0 24px;background:color-mix(in srgb,var(--background) 88%,transparent);
  backdrop-filter:blur(6px);border-bottom:1px solid var(--border)}
.tabs a{padding:9px 12px;font-size:13px;color:var(--muted-foreground);
  border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs a:hover{color:var(--foreground)}
.tabs a.active{color:var(--foreground);border-bottom-color:var(--foreground)}
/* 卡片 */
.card{background:var(--card);color:var(--card-foreground);
  border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;
  margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  padding:14px 16px}
.kpi-k{font-size:12.5px;color:var(--muted-foreground)}
.kpi-v{font-size:22px;font-weight:600;letter-spacing:-.02em;margin-top:2px;
  font-variant-numeric:tabular-nums}
.kpi-v small{font-size:13px;font-weight:500;color:var(--muted-foreground);margin-left:2px}
.kpi-sub{font-size:12px;font-weight:500;color:var(--muted-foreground);margin-top:3px}
/* alert（shadcn alert：细边框 + muted 底） */
.alert{border:1px solid var(--border);background:var(--muted);
  border-radius:var(--radius);padding:12px 16px;font-size:13px;
  color:var(--muted-foreground);margin:12px 0}
.alert b{color:var(--foreground);font-weight:600}
/* flex 布局 */
.flex{display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start}
.grow{flex:1;min-width:320px}
.padcols{display:flex;gap:14px;flex-wrap:wrap}
/* 方向面板 */
.pad{border:1px solid var(--border);border-radius:var(--radius);padding:12px;
  background:var(--card)}
.pad-title{font-size:13px;font-weight:600;margin-bottom:10px}
.pad-row{display:flex;gap:6px;margin-bottom:6px}
.pad-row:last-child{margin-bottom:0}
.pad-cell{width:96px;height:80px;border-radius:calc(var(--radius) - 2px);
  position:relative;display:flex;flex-direction:column;align-items:center;
  justify-content:center;border:1px solid var(--border);transition:opacity .12s}
.pad-cell:hover{opacity:.88}
.pad-arrow{font-size:17px;line-height:1.1}
.pad-p{font-size:14px;font-weight:600;font-variant-numeric:tabular-nums}
.pad-n{font-size:10px;font-variant-numeric:tabular-nums}
.pad-legend{margin-top:10px;font-size:11px;color:var(--muted-foreground);max-width:310px}
/* 表格（shadcn table） */
.tablewrap{border:1px solid var(--border);border-radius:var(--radius);
  background:var(--card);overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:9px 12px;border-bottom:1px solid var(--border);
  text-align:left;white-space:nowrap;font-variant-numeric:tabular-nums;
  vertical-align:middle}
th{font-size:12px;font-weight:500;color:var(--muted-foreground);
  position:sticky;top:0;background:var(--card);cursor:pointer;user-select:none}
th:hover{color:var(--foreground)}
th.sort-asc::after{content:" ↑"}
th.sort-desc::after{content:" ↓"}
td.num,th.num{text-align:right}
tbody tr:hover td{background:var(--muted)}
tbody tr:last-child td{border-bottom:none}
td small{color:var(--muted-foreground)}
.bar-track{height:6px;background:var(--muted);border-radius:999px;
  overflow:hidden;min-width:56px;display:inline-block;vertical-align:middle;
  margin-right:8px}
.bar-fill{height:100%;background:var(--chart-1);border-radius:999px}
/* 堆叠条 */
.block{margin:10px 0}
.stack{display:flex;height:8px;border-radius:999px;overflow:hidden;margin:6px 0 6px;
  background:var(--muted)}
.stack-seg{height:100%}
.stack-legend{font-size:12px;color:var(--muted-foreground);display:flex;
  gap:14px;flex-wrap:wrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
/* 左右偏向 */
.lrwrap{display:flex;gap:2px;height:18px;border-radius:999px;overflow:hidden;
  margin:6px 0;max-width:420px;background:var(--muted)}
.lr-half{flex:1;display:flex}
.lr-fill{height:100%;display:flex;align-items:center;font-size:10.5px;
  color:#fff;font-weight:600;padding:0 6px;white-space:nowrap;justify-content:flex-end}
/* 徽章（shadcn badge：secondary） */
.badge{display:inline-flex;align-items:center;border-radius:6px;padding:1px 7px;
  font-size:11.5px;font-weight:500;line-height:18px;margin-left:3px;
  background:var(--secondary);color:var(--secondary-foreground)}
.badge.win{background:color-mix(in srgb,var(--win) 12%,transparent);color:var(--win)}
.badge.lose{background:transparent;border:1px solid var(--border);
  color:var(--lose);line-height:16px}
.tag{display:inline-flex;align-items:center;border-radius:6px;padding:1px 8px;
  font-size:11.5px;font-weight:500;margin:2px 4px 2px 0;
  background:var(--secondary);color:var(--secondary-foreground)}
.win{color:var(--win);font-weight:500}.lose{color:var(--lose);font-weight:500}
/* 建议（shadcn list：克制、无彩色符号） */
.tips{margin:8px 0 0;padding:0;list-style:none}
.tips li{margin:8px 0;font-size:13.5px;color:color-mix(in srgb,
  var(--foreground) 88%,var(--muted-foreground));padding-left:16px;position:relative}
.tips li::before{content:"";position:absolute;left:2px;top:9px;width:5px;height:5px;
  border-radius:50%;background:var(--border)}
.tips b{color:var(--foreground);font-weight:600}
/* 工具条 */
.toolbar{display:flex;align-items:center;gap:10px;margin:10px 0}
.toolbar input{background:var(--background);border:1px solid var(--input);
  color:var(--foreground);border-radius:calc(var(--radius) - 2px);
  padding:7px 12px;font-size:13px;outline:none;width:240px}
.toolbar input:focus{border-color:var(--ring);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--ring) 25%,transparent)}
.note{border:1px solid var(--border);border-radius:var(--radius);
  padding:12px 16px;font-size:13px;color:var(--muted-foreground);margin:12px 0;
  background:var(--card)}
.minitable td{padding:4px 10px;font-size:12px}
.foot{margin-top:44px;color:var(--muted-foreground);font-size:12px;text-align:center;
  border-top:1px solid var(--border);padding-top:16px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.two{grid-template-columns:1fr}}
"""

JS = """
(function(){
  try{
    var t=localStorage.getItem('bh-theme');
    if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches))
      document.documentElement.classList.add('dark');
  }catch(e){}
})();
function toggleTheme(){
  var el=document.documentElement;
  el.classList.toggle('dark');
  try{localStorage.setItem('bh-theme',el.classList.contains('dark')?'dark':'light');}catch(e){}
}
function sortTable(th){
  var table=th.closest('table'),tbody=table.tBodies[0];
  var idx=Array.prototype.indexOf.call(th.parentElement.children,th);
  var asc=th.dataset.d!=='asc';
  th.dataset.d=asc?'asc':'desc';
  var ths=th.parentElement.children;
  for(var i=0;i<ths.length;i++){ths[i].classList.remove('sort-asc','sort-desc');}
  th.classList.add(asc?'sort-asc':'sort-desc');
  var rows=Array.prototype.slice.call(tbody.rows);
  rows.sort(function(a,b){
    var av=a.children[idx].getAttribute('data-v'),bv=b.children[idx].getAttribute('data-v');
    if(av===''&&bv==='')return 0; if(av==='')return 1; if(bv==='')return -1;
    var an=parseFloat(av),bn=parseFloat(bv);
    var r=(!isNaN(an)&&!isNaN(bn))?an-bn:av.localeCompare(bv,'zh');
    return asc?r:-r;
  });
  rows.forEach(function(r){tbody.appendChild(r);});
}
function filterOpp(){
  var q=document.getElementById('oppSearch').value.trim().toLowerCase();
  var n=0;
  var rows=document.querySelectorAll('#oppTable tbody tr');
  for(var i=0;i<rows.length;i++){
    var show=!q||rows[i].getAttribute('data-name').indexOf(q)>=0;
    rows[i].style.display=show?'':'none'; if(show)n++;
  }
  document.getElementById('oppCount').textContent=n;
}
(function(){
  var links=document.querySelectorAll('.tabs a');
  var secs=[];
  for(var i=0;i<links.length;i++){
    var id=links[i].getAttribute('href').slice(1);
    var el=document.getElementById(id);
    if(el)secs.push([el,links[i]]);
  }
  function spy(){
    var pos=window.scrollY+120,cur=null;
    for(var i=0;i<secs.length;i++){
      if(secs[i][0].offsetTop<=pos)cur=secs[i][1];
    }
    for(var j=0;j<links.length;j++)links[j].classList.remove('active');
    if(cur)cur.classList.add('active');
  }
  window.addEventListener('scroll',spy,{passive:true});
  spy();
})();
"""


def render(ds, agg, me_sum, out_path):
    me = ds["me"]
    matches = ds["matches"]
    total_min = sum(m["minutes"] for m in matches)
    total_dodges = sum(m["players"][m["opponent"]]["sum"]["dodges"]
                       for m in matches if m["opponent"])
    total_dashes = sum(m["players"][m["opponent"]]["sum"]["dashes"]
                       for m in matches if m["opponent"])
    dates = [m["mtime"] for m in matches]
    span = ""
    if dates:
        span = (datetime.datetime.fromtimestamp(min(dates)).strftime("%Y-%m-%d")
                + " ~ " + datetime.datetime.fromtimestamp(max(dates)).strftime("%Y-%m-%d"))

    # 全体对手聚合（v2 修复：不再重复计第一场）
    all_sum = None
    for m in matches:
        if m["opponent"]:
            all_sum = merge_sums(all_sum, dict(m["players"][m["opponent"]]["sum"]))
    if all_sum is None:
        all_sum = {"dodges": 0, "dashes": 0, "minutes": 0, "dir_counts": {},
                   "dash_dir_counts": {}, "reactive_rate": 0, "reactive_instant": 0,
                   "reactive_offensive": 0, "spontaneous": 0, "panic_rate": 0,
                   "dodge_per_min": 0, "dash_per_min": 0,
                   "reaction_median_ms": None, "attack_after_dodge": 0, "jump": 0,
                   "attacks_per_min": 0, "dodges_ground": 0, "dodges_air": 0,
                   "dodges_jump": 0, "pressure_dash": 0, "pressure_dash_rate": 0,
                   "pressure_dash_dir": {}}

    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["matches"], -kv[1]["sum"]["dodges"]))
    repeat = [(n, e) for n, e in ranked if e["matches"] >= 2]
    total_wins = sum(e["wins"] for _, e in ranked)
    total_losses = sum(e["losses"] for _, e in ranked)
    decided = total_wins + total_losses

    # 我的英雄/地图胜率
    hero_stats, map_stats = {}, {}
    for m in matches:
        ms = m["players"].get(me)
        if not ms or m["winner_name"] not in (me, m.get("opponent")):
            continue
        win = m["winner_name"] == me
        for store, key in ((hero_stats, ms["hero"]), (map_stats, m["level_id"])):
            st = store.setdefault(key, [0, 0])
            st[0 if win else 1] += 1

    H = []
    A = H.append
    A(f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
      f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>Brawlhalla 对手闪避习惯分析报告</title>"
      f"<script>{JS}</script>"
      f"<style>{CSS}</style></head><body><div class='wrap'>")

    # ===== 头部 =====
    A("<div class='head'><div>"
      "<h1>对手闪避习惯分析报告</h1>"
      f"<div class='sub'>玩家 <b style='font-weight:600'>{esc(me)}</b> · "
      f"BrawlhallaReplays · {span} · 生成于 "
      f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}</div>"
      f"<div style='margin-top:8px'><span class='tag'>v4 算法口径</span>"
      f"<span class='tag'>dash ≠ 闪避</span>"
      f"<span class='tag'>胜负 = 名次</span></div>"
      "</div>"
      "<button class='iconbtn' title='切换亮/暗主题' onclick='toggleTheme()'>◐</button>"
      "</div>")
    A("<div class='tabs'>"
      "<a href='#sec-me'>我的画像</a><a href='#sec-hero'>英雄地图</a>"
      "<a href='#sec-all'>通用对策</a>"
      "<a href='#sec-rank'>对手榜单</a><a href='#sec-vs'>专项对策</a>"
      "<a href='#sec-recent'>最近对局</a><a href='#sec-def'>指标说明</a></div>")

    # ===== 概览 KPI =====
    A("<div class='grid' style='margin-top:16px'>")
    A(kpi("回放文件", ds["total_files"]))
    A(kpi("有效 1v1 对局", len(matches)))
    A(kpi("不同对手", len(agg)))
    A(kpi("累计时长", f"{total_min:.0f}", "分钟"))
    A(kpi("对手真闪避样本", total_dodges))
    A(kpi("对手 dash 样本", total_dashes))
    A(kpi("你的胜率", f"{pct(total_wins, decided) if decided else 0}%",
          f"{total_wins}胜{total_losses}负"))
    A("</div>")
    A("<div class='alert'><b>v4 口径</b> · 依据游戏机制（地面闪避键+方向 = dash 位移，"
      "不算闪避），真闪避只含 地面原地闪 与 空中闪避；旧版把地面 dash 也算成了方向闪避。"
      "本报告所有「闪避」均为真闪避，dash 单独统计。</div>")

    # ===== 我的画像 =====
    A("<h2 id='sec-me'><span class='no'>01</span>你的习惯（基线）</h2>")
    if me_sum and me_sum.get("dodges"):
        n_me = me_sum["dodges"]
        A("<div class='card'><div class='flex'>")
        A("<div class='padcols'>" +
          dir_pad(me_sum["dir_counts"], n_me, "真闪避方向") +
          dir_pad(me_sum.get("dash_dir_counts") or {}, me_sum.get("dashes", 0),
                  "地面 dash 方向（位移）") +
          "</div>")
        A("<div class='grow'>")
        A("<div class='grid'>")
        for k, v in [
            ("真闪避频率", f"{me_sum['dodge_per_min']}<small>/min</small>"),
            ("dash 频率", f"{me_sum['dash_per_min']}<small>/min</small>"),
            ("纯受压闪避率", f"{me_sum['reactive_rate']}<small>%</small>"),
            ("反应中位", f"{me_sum.get('reaction_median_ms') or '-'}<small>ms</small>"),
            ("攻击频率", f"{me_sum.get('attacks_per_min', 0)}<small>/min</small>"),
            ("原地闪", f"{pct(me_sum['dir_counts'].get('spot', 0), n_me)}<small>%</small>"),
            ("连闪率", f"{me_sum['panic_rate']}<small>%</small>"),
            ("闪后反打", f"{pct(me_sum.get('attack_after_dodge', 0), n_me)}<small>%</small>"),
        ]:
            A(kpi(k, v))
        A("</div>")
        A(cat_split(me_sum))
        A(intent_bar(me_sum))
        A(lr_bias(me_sum["dir_counts"]))
        A(sparkline_weekly(matches, me))
        A("<h3>你的可改进点</h3><ul class='tips'>")
        for t in advice_for(me_sum, is_opponent=False):
            A(f"<li>{t}</li>")
        A("</ul></div></div></div>")

        # 英雄 / 地图胜率
        if hero_stats or map_stats:
            A("<h2 id='sec-hero'><span class='no'>02</span>你的英雄 / 地图胜率</h2>")
            A("<div class='card'><div class='two'>")
            for title, store in (("常用英雄", hero_stats), ("地图（level_id）", map_stats)):
                A(f"<div><table><thead><tr><th>{title}</th><th class='num'>场次</th>"
                  f"<th class='num'>战绩</th><th class='num'>胜率</th></tr></thead><tbody>")
                rows = [(k, v) for k, v in store.items() if v[0] + v[1] >= 2]
                rows.sort(key=lambda kv: -(kv[1][0] + kv[1][1]))
                if not rows:
                    A("<tr><td colspan='4' class='muted'>样本不足</td></tr>")
                for k, (w, l) in rows[:8]:
                    name = (f"{legend_name(k)} <small>ID {k}</small>"
                            if title == "常用英雄" else str(k))
                    A(f"<tr><td>{name}</td><td class='num'>{w + l}</td>"
                      f"<td class='num'>{w}-{l}</td><td class='num'>{pct(w, w + l)}%</td></tr>")
                A("</tbody></table></div>")
            A("</div></div>")
    else:
        A("<div class='card muted'>没有找到你自己的对局数据。</div>")

    # ===== 全体对手 =====
    A("<h2 id='sec-all'><span class='no'>03</span>所有对手的平均画像（通用对策）</h2>")
    A("<div class='card'><div class='flex'>")
    A("<div class='padcols'>" +
      dir_pad(all_sum["dir_counts"], all_sum["dodges"], "真闪避方向") +
      dir_pad(all_sum.get("dash_dir_counts") or {}, all_sum.get("dashes", 0),
              "地面 dash 方向（位移）") +
      "</div>")
    A("<div class='grow'>")
    A("<div class='grid'>")
    for k, v in [
        ("纯受压闪避率", f"{all_sum['reactive_rate']}<small>%</small>"),
        ("反应中位", f"{all_sum.get('reaction_median_ms') or '-'}<small>ms</small>"),
        ("攻击频率", f"{all_sum.get('attacks_per_min', 0)}<small>/min</small>"),
        ("连闪率", f"{all_sum['panic_rate']}<small>%</small>"),
    ]:
        A(kpi(k, v))
    A("</div>")
    A(f"<div class='block' style='border:1px solid var(--border);border-radius:"
      f"var(--radius);padding:10px;margin-top:10px'>{hist_svg(all_sum.get('_lat') or [])}</div>")
    A(intent_bar(all_sum))
    A("<h3>通用建议</h3><ul class='tips'>")
    for t in advice_for(all_sum, is_opponent=True):
        A(f"<li>{t}</li>")
    A("</ul></div></div></div>")

    # ===== 对手榜单 =====
    A("<h2 id='sec-rank'><span class='no'>04</span>对手排行榜</h2>")
    A(f"<div class='toolbar'><input id='oppSearch' placeholder='搜索对手名…' "
      f"oninput='filterOpp()'><span class='muted'>显示 <b id='oppCount'>{len(ranked)}</b> / "
      f"{len(ranked)} 人 · 点击表头排序</span></div>")
    A("<div class='tablewrap'><table id='oppTable'><thead><tr>"
      "<th>对手</th><th class='num'>局</th><th>胜负</th><th class='num'>胜率</th>"
      "<th>最近</th><th class='num'>时长</th>"
      "<th class='num'>真闪避/min</th><th class='num'>dash/min</th><th>主闪方向</th>"
      "<th>纯受压率</th><th class='num'>反应中位</th><th class='num'>原地闪</th>"
      "<th class='num'>连闪率</th><th class='num'>攻击/min</th>"
      "</tr></thead><tbody>")
    for name, e in ranked:
        s = e["sum"]
        dc = s["dir_counts"]
        n = s["dodges"] or 1
        top = max(dc.items(), key=lambda kv: kv[1])[0] if dc else "spot"
        wr = pct(e["wins"], e["wins"] + e["losses"]) if (e["wins"] + e["losses"]) else None
        wr_cell = f"{wr}<small>%</small>" if wr is not None else "-"
        spot_p = pct(dc.get("spot", 0), n)
        A(f"<tr data-name='{esc(name.lower())}'>"
          f"<td style='font-weight:600'>{esc(name)}</td>"
          f"<td class='num' data-v='{e['matches']}'>{e['matches']}</td>"
          f"<td data-v='{e['wins'] * 10000 - e['losses']}'>"
          f"<span class='win'>{e['wins']}</span>-<span class='lose'>{e['losses']}</span></td>"
          f"<td class='num' data-v='{wr if wr is not None else -1}'>{wr_cell}</td>"
          f"<td data-v=''>{form_chips(e.get('history'))}</td>"
          f"<td class='num' data-v='{e['minutes']}'>{e['minutes']:.1f}min</td>"
          f"<td class='num' data-v='{s['dodge_per_min']}'>{s['dodge_per_min']}</td>"
          f"<td class='num' data-v='{s['dash_per_min']}'>{s['dash_per_min']}</td>"
          f"<td data-v='{pct(dc.get(top, 0), n)}'>{DIR_LABEL.get(top, top)} "
          f"<small>{pct(dc.get(top, 0), n)}%</small></td>"
          f"<td data-v='{s['reactive_rate']}'>"
          f"<div style='display:flex;align-items:center;gap:8px'>"
          f"<span class='bar-track'><span class='bar-fill' "
          f"style='width:{min(s['reactive_rate'] * 2, 100):.0f}%;display:block'></span></span>"
          f"{s['reactive_rate']}%</div></td>"
          f"<td class='num' data-v='{s.get('reaction_median_ms') or 99999}'>"
          f"{str(s.get('reaction_median_ms')) + 'ms' if s.get('reaction_median_ms') else '-'}</td>"
          f"<td class='num' data-v='{spot_p}'>{spot_p}<small>%</small></td>"
          f"<td class='num' data-v='{s['panic_rate']}'>{s['panic_rate']}%</td>"
          f"<td class='num' data-v='{s.get('attacks_per_min', 0)}'>"
          f"{s.get('attacks_per_min', 0)}</td>"
          f"</tr>")
    A("</tbody></table></div>")

    # ===== 专项对策 =====
    A("<h2 id='sec-vs'><span class='no'>05</span>重复遇到的对手 · 专项对策</h2>")
    if not repeat:
        A("<div class='card muted'>还没有遇到过 2 次以上的对手。</div>")
    for name, e in repeat[:25]:
        s = e["sum"]
        dc = s["dir_counts"]
        n = s["dodges"] or 1
        heroes_sorted = sorted(e["heroes"].items(), key=lambda kv: -kv[1])
        hero_s = " / ".join(f"{legend_name(h)}×{c}" for h, c in heroes_sorted[:2]) or "-"
        wr = pct(e["wins"], e["wins"] + e["losses"]) if (e["wins"] + e["losses"]) else None
        A("<div class='card'>")
        A(f"<h3 style='margin:0 0 2px;font-size:15px'>{esc(name)} "
          f"{form_chips(e.get('history'))}</h3>")
        wl = (f"<span class='win'>{e['wins']}胜</span> / <span class='lose'>{e['losses']}负</span>"
              + (f" · 胜率 {wr}%" if wr is not None else ""))
        A(f"<div class='muted' style='margin-bottom:10px'>{e['matches']} 局 · "
          f"{e['minutes']:.1f} 分钟 · {wl} · 英雄 {hero_s} · "
          f"地图 {sorted(e['maps'].items(), key=lambda kv: -kv[1])[0][0] if e['maps'] else '-'}</div>")
        A("<div class='flex'>")
        A("<div class='padcols'>" +
          dir_pad(dc, n, "真闪避方向") +
          dir_pad(s.get("dash_dir_counts") or {}, s.get("dashes", 0),
                  "地面 dash 方向") +
          "</div>")
        A("<div class='grow'>")
        A("<div class='grid'>")
        for k, v in [
            ("真闪避/min", f"{s['dodge_per_min']}"),
            ("dash/min", f"{s['dash_per_min']}"),
            ("纯受压率", f"{s['reactive_rate']}<small>%</small>"),
            ("反应中位", f"{s.get('reaction_median_ms') or '-'}<small>ms</small>"),
            ("连闪率", f"{s['panic_rate']}<small>%</small>"),
            ("闪后反打", f"{pct(s.get('attack_after_dodge', 0), n)}<small>%</small>"),
            ("受压dash率", f"{s.get('pressure_dash_rate', 0)}<small>%</small>"),
            ("攻击/min", f"{s.get('attacks_per_min', 0)}"),
        ]:
            A(kpi(k, v))
        A("</div>")
        A(cat_split(s))
        A(intent_bar(s))
        A(lr_bias(dc))
        A("<h3>怎么打他</h3><ul class='tips'>")
        for t in advice_for(s, is_opponent=True):
            A(f"<li>{t}</li>")
        A("</ul>")
        hist = e.get("history") or []
        if hist:
            A("<h3 style='margin-top:14px'>交锋记录</h3><table class='minitable'><tbody>")
            for h in reversed(hist[-8:]):
                d = datetime.datetime.fromtimestamp(h["date"]).strftime("%m-%d %H:%M")
                res = ("<span class='win'>胜</span>" if h["win"] else
                       "<span class='lose'>负</span>" if h["win"] is False else "—")
                A(f"<tr><td class='muted' style='width:90px'>{d}</td>"
                  f"<td style='width:40px'>{res}</td>"
                  f"<td class='muted'>{h['minutes']:.1f}min</td>"
                  f"<td class='muted' style='max-width:220px;overflow:hidden;"
                  f"text-overflow:ellipsis'>{esc(os.path.basename(h['file']))}</td></tr>")
            A("</tbody></table>")
        A("</div></div></div>")

    # ===== 最近对局 =====
    A("<h2 id='sec-recent'><span class='no'>06</span>全部对局（新 → 旧）</h2>")
    A("<div class='tablewrap'><table><thead><tr>"
      "<th>时间</th><th>对手</th><th class='num'>时长</th><th>结果</th>"
      "<th>文件</th>"
      "<th class='num'>对手真闪避/min</th><th class='num'>对手dash/min</th>"
      "<th class='num'>你真闪避/min</th><th class='num'>纯受压率</th>"
      "</tr></thead><tbody>")
    for m in sorted(matches, key=lambda x: -x["mtime"]):
        opp = m["opponent"] or "-"
        os_ = m["players"].get(opp, {}).get("sum", {}) if opp != "-" else {}
        ms_ = m["players"].get(me, {}).get("sum", {})
        res = "-" if not m["winner_name"] else (
            "<span class='win'>胜</span>" if m["winner_name"] == me else "<span class='lose'>负</span>")
        ts = datetime.datetime.fromtimestamp(m["mtime"]).strftime("%m-%d %H:%M")
        A(f"<tr><td data-v='{int(m['mtime'])}'>{ts}</td><td>{esc(opp)}</td>"
          f"<td class='num' data-v='{m['minutes']}'>{m['minutes']:.1f}min</td><td>{res}</td>"
          f"<td class='muted'>{esc(m['file'])}</td>"
          f"<td class='num' data-v='{os_.get('dodge_per_min', 0)}'>"
          f"{os_.get('dodge_per_min', '-')}</td>"
          f"<td class='num' data-v='{os_.get('dash_per_min', 0)}'>"
          f"{os_.get('dash_per_min', '-')}</td>"
          f"<td class='num' data-v='{ms_.get('dodge_per_min', 0)}'>"
          f"{ms_.get('dodge_per_min', '-')}</td>"
          f"<td class='num' data-v='{os_.get('reactive_rate', 0)}'>"
          f"{os_.get('reactive_rate', '-')}%</td></tr>")
    A("</tbody></table></div>")

    # ===== 指标定义 =====
    A("<h2 id='sec-def'><span class='no'>07</span>指标定义与数据边界</h2>")
    A("<div class='card'><ul class='tips'>"
      "<li><b>闪避 vs dash（v4 核心修正）</b>：游戏机制上，地面按闪避键+方向 = "
      "<b>dash（冲刺位移）</b>——没有无敌帧、不消耗闪避冷却，不算 dodge；"
      "真闪避只有 地面原地闪（16 帧无敌）与 空中闪避（方向/原地，冷却 2.7s）。"
      "旧版把 84% 的地面 dash 当成了『方向闪避』，v4 全部拆出。</li>"
      "<li><b>胜负判定</b>：results.scores 存的是名次（1=第一名）。旧版取最高分当赢家，"
      "胜负全部颠倒；已在 252 场上与「死亡次数更少者」100% 吻合验证。</li>"
      "<li><b>纯受压闪避率</b>：你出招后 400ms 内的真闪避，剔除两类噪声——"
      f"延迟 &lt;{REACT_MIN_MS}ms 的『瞬时按键』（同按/预输入，不是反应）和"
      f"他自己出招后 {CHASE_CTX_MS}ms 内的位移（追击闪避/dash-in，是进攻不是防御）。"
      "回放没有受击事件，『被打中』不可见，这是可获得的最近似代理。</li>"
      "<li><b>反应中位</b>：仅统计纯防御闪避的延迟中位数（旧版混入了瞬时按键，"
      "数值被人为压低）。</li>"
      "<li><b>受压 dash 率</b>：反应窗内、非进攻上下文的地面 dash / 全部 dash——"
      "衡量『被压时他靠跑路而不是闪避』的程度，方向分布见 dash 面板。</li>"
      "<li><b>dash 无敌帧提示</b>：地面 dash 全程无无敌帧，dash 途中可以直接打——"
      "所以受压 dash 多的对手，抢他 dash 路线比等他闪避更划算。</li>"
      "<li><b>数据边界</b>：回放只存输入流和死亡时间戳，无坐标/伤害/受击事件。"
      "方向是屏幕绝对方向（分不出朝向前后）；时长以输入流末时间戳为准，"
      "&lt;30 秒的局已过滤。</li>"
      "</ul></div>")
    A("<div class='note'>解析器经过 <b>293/293 字节级往返校验</b>（解压→解析→重序列化→"
      "逐字节比对）；闪避/胜负口径修正依据 Brawlhalla Wiki Movement 词条与 252 场实测。</div>")
    A("<div class='foot'>bh-replay-lab v6 · UI 设计语言源自 shadcn/ui theming · "
      "数据与建议仅供参考，打法请结合实战判断</div>")
    A("</div></body></html>")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(H))

    # 结构化数据导出
    slim = {
        "me": me,
        "matches": len(matches),
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "algorithm": ("v4 (winner=placement; ground-dir=dash not dodge; "
                      "intents=defensive/instant/offensive/spontaneous)"),
        "opponents": {
            n: {"matches": e["matches"], "wins": e["wins"], "losses": e["losses"],
                "minutes": e["minutes"], "heroes": e.get("heroes", {}),
                "history": e.get("history", []),
                "sum": {k: v for k, v in e["sum"].items() if k != "_lat"}}
            for n, e in ranked
        },
    }
    with open(os.path.join(OUT_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    return out_path


def generate_report(replay_dir):
    """生成完整 HTML 报告（CLI 与桌面端共用）。

    走 bh_query 的增量缓存：重复生成 0 秒（此前每次全量重解析约 6 秒）；
    新增/改动的回放文件会按指纹自动重解析，正确性不受缓存影响。"""
    import bh_query
    bh_query.REPLAY_DIR = replay_dir
    bh_query.OUT_DIR = OUT_DIR
    ds, _stats = bh_query.load_dataset()
    agg, me_sum = aggregate_by_opponent(ds)
    out_path = os.path.join(OUT_DIR, "report.html")
    render(ds, agg, me_sum, out_path)
    return out_path, len(agg), len(ds["matches"])


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else __import__("bh_paths").replay_dir()
    p, n_opp, n_match = generate_report(d)
    print("report ->", p)
    print("opponents:", n_opp, "matches:", n_match)
