# -*- coding: utf-8 -*-
"""知识库更新器：从 brawlhalla.wiki.gg 抓取/刷新知识库。

用法：
    python kb_update.py                # 全量更新（英雄属性/武器/机制原文/个人档案）
    python kb_update.py --no-legends   # 跳过逐个英雄页抓取（更快）
    python kb_update.py --offline      # 只刷新本地档案快照（不联网）

保证：
- kb/notes/ 下的用户笔记永远不会被覆盖；
- 单个页面抓取失败不影响整体（保留旧数据，meta.json 里记录失败项）。
"""
import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

KB = None  # 延迟到 main() 里解析（kb_store 按环境决定目录）
API = "https://brawlhalla.wiki.gg/api.php"
UA = "bh-replay-lab-kb/1.0 (local tool)"

WEAPONS = ["Sword", "Hammer", "Blasters", "Rifle", "Spear", "Rocket Lance",
           "Bow", "Gauntlets", "Katars", "Axe", "Scythe", "Cannon",
           "Orb", "Greatsword", "Chakram", "Battle Boots"]

STAT_NAMES = ["Strength", "Dexterity", "Defense", "Speed"]


def _get_wikitext(page, timeout=20):
    url = f"{API}?action=parse&page={urllib.parse.quote(page)}&format=json&prop=wikitext&redirects=1"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
    return d.get("parse", {}).get("wikitext", {}).get("*", "")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _write_raw(name, text):
    os.makedirs(os.path.join(KB, "raw"), exist_ok=True)
    with open(os.path.join(KB, "raw", name + ".wikitext"), "w", encoding="utf-8") as f:
        f.write(text or "")


def _strip_wiki(s):
    s = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", s or "")
    s = re.sub(r"'''?", "", s)
    return s.strip()


def update_legends_stats(log):
    """Stats 页只列 High/Low 极端名单，不完整——改用各英雄页 infobox（见下）。
    这里仅存原文备查。"""
    st = _get_wikitext("Stats")
    _write_raw("Stats", st)
    log("Stats 原文存档（完整属性改从各英雄页 infobox 获取）")


def update_legend_pages(log, do_pages=True):
    """逐个英雄页抓 infobox：武器(weapon1/2)、属性(atk/dex/def/spe)、称号(title)。"""
    lg_path = os.path.join(KB, "legends.json")
    lg = _read_json(lg_path, {})
    legends = lg.get("legends", {})
    done = fail = 0
    for wid, info in sorted(legends.items(), key=lambda kv: int(kv[0])):
        name = info.get("name", "")
        try:
            wt = _get_wikitext(name.replace(" ", "_"))

            def _param(key):
                m = re.search(rf"^\|\s*{key}\s*=\s*(.+)$", wt, re.I | re.M)
                return _strip_wiki(m.group(1)).strip() if m else None

            weapons = [w for w in (_param("weapon1"), _param("weapon2")) if w]
            stats = {}
            for pkey, sname in (("atk", "strength"), ("dex", "dexterity"),
                                ("def", "defense"), ("spe", "speed")):
                v = _param(pkey)
                if v and v.isdigit():
                    stats[sname] = int(v)
            if weapons:
                info["weapons"] = weapons
            if stats:
                info["stats"] = stats
            title = _param("title")
            if title:
                info["title"] = title
            if weapons or stats:
                done += 1
            else:
                fail += 1
                log(f"  legend {name}: infobox 参数未命中")
            time.sleep(0.15)   # 温和限速
        except Exception as e:  # noqa: BLE001
            fail += 1
            log(f"  legend {name}: {type(e).__name__}")
        if not do_pages:
            break
    _write_json(lg_path, {"legends": legends})
    log(f"legend pages: {done} 成功 / {fail} 失败")


def update_weapons(log):
    """武器页：存原文 + 抽取招式名与描述片段。"""
    wp_path = os.path.join(KB, "weapons.json")
    weapons = _read_json(wp_path, {}).get("weapons", {})
    for w in WEAPONS:
        try:
            wt = _get_wikitext(w.replace(" ", "_"))
            _write_raw("Weapon_" + w.replace(" ", "_"), wt)
            moves = {}
            cur = None
            for line in wt.split("\n"):
                m = re.match(r"^====\s*([^=]+?)\s*====", line)
                if m:
                    cur = m.group(1).strip()
                    moves.setdefault(cur, "")
                    continue
                if cur and line.strip():
                    moves[cur] += " " + line.strip()
            for k in list(moves):
                moves[k] = _strip_wiki(re.sub(r"\{\{[^}]*\}\}", " ", moves[k]))[:400].strip()
            weapons[w] = {"moves": moves}
            time.sleep(0.15)
        except Exception as e:  # noqa: BLE001
            log(f"  weapon {w}: {type(e).__name__}")
    _write_json(wp_path, {"weapons": weapons})
    log(f"weapons: {len(weapons)} 收录")


def update_movement(log):
    wt = _get_wikitext("Movement")
    _write_raw("Movement", wt)
    log(f"Movement 原文 {len(wt)} 字符")


def refresh_profiles(log):
    """从分析数据生成"我"与常打对手的档案快照（供检索/agent 上下文）。"""
    import bh_api
    bh_api.refresh()
    me = bh_api.my_profile()
    _write_json(os.path.join(KB, "profiles", "me.json"), me)
    opps = bh_api.list_opponents(limit=30)
    _write_json(os.path.join(KB, "profiles", "opponents.json"),
                {"top_opponents": opps})
    log(f"profiles: me + {len(opps)} 个常打对手")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    global KB
    from kb_store import KB_DIR
    KB = KB_DIR
    args = sys.argv[1:]
    offline = "--offline" in args
    do_legends = "--no-legends" not in args

    def log(msg):
        print(msg, flush=True)

    os.makedirs(os.path.join(KB, "notes"), exist_ok=True)
    # 初始 seed：legends.json 若不存在则从 legends.py 生成
    lg_path = os.path.join(KB, "legends.json")
    if not os.path.exists(lg_path):
        from legends import LEGENDS
        _write_json(lg_path, {"legends": {str(k): {"name": v} for k, v in LEGENDS.items()}})
        log("legends.json 初始化（来自 legends.py）")

    errors = []
    for label, fn in (("Movement", update_movement),
                      ("Weapons", update_weapons)):
        if offline:
            break
        try:
            fn(log)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {type(e).__name__}: {e}")
            log(f"[失败] {label}: {type(e).__name__}: {e}")
    if do_legends and not offline:
        try:
            update_legends_stats(log)
            update_legend_pages(log)
        except Exception as e:  # noqa: BLE001
            errors.append(f"LegendPages: {e}")
            log(f"[失败] LegendPages: {e}")
    try:
        refresh_profiles(log)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Profiles: {e}")
        log(f"[失败] Profiles: {e}")

    meta = _read_json(os.path.join(KB, "meta.json"), {})
    meta["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    meta["errors"] = errors
    _write_json(os.path.join(KB, "meta.json"), meta)
    log("完成。" + (f" {len(errors)} 项失败" if errors else " 全部成功"))


if __name__ == "__main__":
    main()
