"""增量缓存：让 bh_query.py 从"每次 40 秒全量解析"变成"首次建库、之后秒开"。

原理：
- 对每个 .replay 文件算指纹 (mtime_ns, size)，和缓存里的指纹比对；
- 命中 → 直接复用上次解析好的 match 结果；未命中/新增 → 只解析这些；
- 缓存是纯 JSON，损坏或结构不符会自动重建（安全降级，不影响正确性）。

正确性保证：指纹变了的文件（新对局、被改动）一定会重新解析，
所以缓存不会让数据过期——只是省掉重复解析。
"""

import json
import os
import sys

CACHE_VERSION = 4   # 分析结构变更时 +1，旧缓存自动作废
# v4：闪避三分类（依据游戏机制：地面方向+闪避=Dash 位移，非闪避；真闪避只有
#     地面原地闪与空中闪避）。受压反应拆分为 纯防御/瞬时按键/进攻性/自发，
#     新增受压 dash 逃逸方向统计。旧缓存全部作废。
# 缓存文件路径由调用方拼接（bh_query: out_dir()/cache.json），通用路径见 bh_paths.py。


def fingerprint(path):
    st = os.stat(path)
    return [st.st_mtime_ns, st.st_size]


def load_cache(path):
    """读缓存；任何异常/版本不符都返回 None（调用方走全量解析）。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "files" not in data or "matches" not in data:
            return None
        if data.get("me") is None or data.get("name_counts") is None:
            return None
        # 分析结构变更（如闪避按离地状态分类）后旧缓存必须作废重跑
        if data.get("version") != CACHE_VERSION:
            return None
        return data
    except (ValueError, OSError):
        return None


def save_cache(path, ds, fps, replay_dir=None):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "me": ds["me"],
            "name_counts": ds["name_counts"],
            "total_files": ds["total_files"],
            "replay_dir": replay_dir,   # 换回放目录时让缓存作废（防 me 串目录）
            "files": fps,          # {path: [mtime_ns, size]}
            "matches": ds["matches"],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return True
    except (OSError, TypeError, ValueError):
        return False


def jsonable(obj):
    """把 match 结果转成可 JSON 化的纯结构（主要是 Counter/dict 的兜底）。"""
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
