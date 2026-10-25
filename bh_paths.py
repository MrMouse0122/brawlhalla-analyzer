# -*- coding: utf-8 -*-
"""路径解析（全项目唯一的路径默认值来源）。

优先级：环境变量 > 通用默认值。
- 回放目录：BH_REPLAY_DIR → %USERPROFILE%\\BrawlhallaReplays（游戏官方默认位置）
- 数据目录：BH_OUT_DIR    → %LOCALAPPDATA%\\BrawlhallaAnalyzer
- 知识库：  BH_KB_DIR     → %LOCALAPPDATA%\\BrawlhallaAnalyzer\\kb

自己机器上已有数据的用户：用 setx 把三个环境变量指向既有目录即可无缝沿用，
源码里不保留任何个人路径。
"""
import os


def replay_dir():
    env = os.environ.get("BH_REPLAY_DIR")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), "BrawlhallaReplays")


def out_dir():
    env = os.environ.get("BH_OUT_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    p = os.path.join(base, "BrawlhallaAnalyzer")
    os.makedirs(p, exist_ok=True)
    return p


def kb_dir():
    env = os.environ.get("BH_KB_DIR")
    if env:
        return env
    # 开发机旁路（源码里直接有 kb/）优先，其次数据目录下的 kb
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
    if os.path.isdir(here):
        return here
    return os.path.join(out_dir(), "kb")
