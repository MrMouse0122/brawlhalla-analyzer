# -*- coding: utf-8 -*-
"""知识库检索模块：加载 kb/ 下所有内容，提供统一的检索与问答入口。

数据组织：
  kb/mechanics.json      游戏机制（人工核对的种子 + 可被 updater 补充）
  kb/legends.json        英雄表（id/name/weapons/stats）
  kb/weapons.json        武器表（含招式/连招的 best-effort 结构化数据）
  kb/raw/*.wikitext      updater 抓取的 wiki 原文（全文检索兜底）
  kb/notes/*             用户手写笔记（updater 不会覆盖）
  kb/profiles/*.json     由 bh_api 生成的"我"与常打对手的档案快照
"""
import os
import re
import json
import glob

_KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
_CACHE = None


def _kb_dir():
    """知识库目录解析：BH_KB_DIR 环境变量 > 源码旁 > %LOCALAPPDATA%\\BrawlhallaAnalyzer\\kb。
    自己机器上已有数据的用户用 setx BH_KB_DIR 指向既有目录即可无缝沿用。"""
    env = os.environ.get("BH_KB_DIR")
    if env:
        return env
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
    if os.path.isdir(here):
        return here
    from bh_paths import out_dir
    return os.path.join(out_dir(), "kb")


KB_DIR = _kb_dir()


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_kb(force=False):
    """加载全部知识库内容（进程内缓存）。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    kb = {"mechanics": [], "legends": {}, "weapons": {}, "raw": {}, "notes": {},
          "profiles": {}, "meta": {}}
    m = _read_json(os.path.join(KB_DIR, "mechanics.json"), {"entries": []})
    kb["mechanics"] = m.get("entries", [])
    lg = _read_json(os.path.join(KB_DIR, "legends.json"), {})
    kb["legends"] = lg.get("legends", lg) if isinstance(lg, dict) else {}
    wp = _read_json(os.path.join(KB_DIR, "weapons.json"), {})
    kb["weapons"] = wp.get("weapons", wp) if isinstance(wp, dict) else {}
    for p in sorted(glob.glob(os.path.join(KB_DIR, "raw", "*.wikitext"))):
        try:
            with open(p, encoding="utf-8") as f:
                kb["raw"][os.path.splitext(os.path.basename(p))[0]] = f.read()
        except OSError:
            pass
    nd = os.path.join(KB_DIR, "notes")
    for p in sorted(glob.glob(os.path.join(nd, "*.md"))):
        try:
            with open(p, encoding="utf-8") as f:
                kb["notes"][os.path.splitext(os.path.basename(p))[0]] = f.read()
        except OSError:
            pass
    nj = _read_json(os.path.join(nd, "opponent_notes.json"), {})
    kb["notes"]["opponent_notes"] = nj
    for p in sorted(glob.glob(os.path.join(KB_DIR, "profiles", "*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                kb["profiles"][os.path.splitext(os.path.basename(p))[0]] = json.load(f)
        except (OSError, ValueError):
            pass
    kb["meta"] = _read_json(os.path.join(KB_DIR, "meta.json"), {})
    _CACHE = kb
    return kb


def _tokenize(q):
    toks = []
    for t in re.split(r"[\s,，。?？!！/、;；:：]+", q.lower()):
        if not t:
            continue
        toks.append(t)
        # 中文整句无空格：补二元切分，保证"追击闪避是什么"也能命中
        if re.search(r"[\u4e00-\u9fff]", t) and len(t) > 2:
            toks.extend(t[i:i + 2] for i in range(len(t) - 1))
    return toks


def search_kb(query, top_k=6):
    """关键词检索：命中 标题/tags/正文/笔记/raw 原文，返回 [{source, title, text}]。"""
    kb = load_kb()
    toks = _tokenize(query)
    scored = []

    def add(source, title, text, tags=""):
        if not text:
            return
        hay = (title + " " + tags + " " + text).lower()
        score = sum(1 for t in toks if t in hay)
        if title.lower().find(" ".join(toks[:2])) >= 0 if toks else False:
            score += 2
        if score:
            scored.append((score, source, title, text[:1200]))

    for e in kb["mechanics"]:
        add("mechanics", e.get("title", e.get("id", "")), e.get("text", ""),
            " ".join(e.get("tags", [])))
    for wid, info in kb["legends"].items():
        if isinstance(info, dict):
            add("legend", f"{info.get('name', wid)} (ID {wid})",
                json.dumps(info, ensure_ascii=False),
                f"{info.get('name', '')} {info.get('weapons', '')}")
    for wname, info in kb["weapons"].items():
        if isinstance(info, dict):
            add("weapon", wname, json.dumps(info, ensure_ascii=False)[:3000], wname)
    for src, text in kb["raw"].items():
        # 原文太长：只挑命中行附近的段落
        low = text.lower()
        hits = [t for t in toks if t in low]
        if not hits:
            continue
        first = min(low.find(t) for t in hits if low.find(t) >= 0)
        seg = text[max(0, first - 300): first + 900]
        add("wiki:" + src, src, seg, src)
    for src, text in kb["notes"].items():
        if isinstance(text, str):
            add("note:" + src, src, text, src)
        elif isinstance(text, dict):
            add("note:opponent_notes", "opponent_notes",
                json.dumps(text, ensure_ascii=False)[:3000], "")
    for src, obj in kb["profiles"].items():
        add("profile:" + src, src, json.dumps(obj, ensure_ascii=False)[:3000], src)
    scored.sort(key=lambda x: -x[0])
    return [{"source": s, "title": t, "text": x} for _, s, t, x in scored[:top_k]]


def legend_knowledge(legend_names):
    """给情报卡用：查英雄的武器/属性/备注，拼一段简短知识文本。"""
    kb = load_kb()
    out = []
    for name in legend_names:
        info = None
        for wid, v in kb["legends"].items():
            if isinstance(v, dict) and v.get("name", "").lower() == name.lower():
                info = dict(v)
                info["id"] = wid
                break
        if not info:
            continue
        bits = [f"{info.get('name', name)}"]
        if info.get("weapons"):
            bits.append("武器: " + "/".join(info["weapons"]))
        st = info.get("stats") or {}
        if st:
            bits.append(f"力量{st.get('strength', '?')} 敏捷{st.get('dexterity', '?')} "
                        f"防御{st.get('defense', '?')} 速度{st.get('speed', '?')}")
        if info.get("notes"):
            bits.append(str(info["notes"]))
        out.append(" · ".join(bits))
    return "\n".join(out) if out else ""


def kb_stats():
    kb = load_kb()
    return {
        "mechanics": len(kb["mechanics"]),
        "legends": len(kb["legends"]),
        "weapons": len(kb["weapons"]),
        "raw_pages": len(kb["raw"]),
        "notes": len(kb["notes"]),
        "profiles": sorted(kb["profiles"].keys()),
        "meta": kb["meta"],
    }
