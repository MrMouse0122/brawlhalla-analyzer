# -*- coding: utf-8 -*-
"""长期记忆存储（跨会话持久，参考 Claude Code 的记忆分层设计）。

三层记忆：
  facts      语义记忆：关于用户的关键事实（偏好/目标/结论/训练计划/对手情报），
             由 LLM 在每轮对话后自动提炼更新，也可由模型通过 memory_write 主动追加。
  old_turns  情节记忆：被上下文裁剪掉的旧轮次（问题+回复摘要），轻量保留最近 N 条。
  （短期上下文 = Agent.history，见 agent.py）

存储：<数据目录>\\agent_memory.json（路径由 bh_paths.out_dir() 解析，
与回放缓存同区；可用 BH_OUT_DIR 环境变量覆盖）。
"""
import json
import os
import datetime
import threading

_MAX_FACTS = 18
_MAX_OLD_TURNS = 12
_OLD_TURN_CHARS = 220

_lock = threading.Lock()


def _sim(a, b):
    """字符二元组 Jaccard 相似度（中文短句够用的模糊去重）。"""
    def grams(s):
        s = s.strip()
        if len(s) <= 1:
            return {s}
        return {s[i:i + 2] for i in range(len(s) - 1)}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _dedupe(facts, sim_threshold=0.55):
    """近似事实合并：包含关系或高相似度的只留一条（后出现的表述覆盖旧的）。"""
    out = []
    for f in facts:
        f = str(f).strip()
        if not f:
            continue
        replaced = False
        for i, old in enumerate(out):
            contained = (len(f) >= 8 and f in old) or (len(old) >= 8 and old in f)
            if contained or _sim(old, f) >= sim_threshold:
                out[i] = f if len(f) >= len(old) else old   # 留信息量大的
                replaced = True
                break
        if not replaced:
            out.append(f)
    return out


def memory_path():
    from bh_paths import out_dir
    return os.path.join(out_dir(), "agent_memory.json")


def load():
    try:
        with open(memory_path(), encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict) and isinstance(m.get("facts"), list):
            return m
    except (OSError, ValueError):
        pass
    return {"facts": [], "old_turns": [], "updated": None}


def save(mem):
    try:
        os.makedirs(os.path.dirname(memory_path()), exist_ok=True)
        mem["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        with open(memory_path(), "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=1)
        return True
    except (OSError, TypeError, ValueError):
        return False


def add_fact(text):
    """模型主动记忆（memory_write 工具）或程序追加。近似事实自动合并。

    返回实际入库的那条表述（原文，或合并时留下的信息量更大的旧条目），
    供调用方回显确认——不能直接取列表末尾，合并后新文本不保证在末尾。"""
    with _lock:
        mem = load()
        text = str(text).strip()
        if not text:
            return ""
        merged = _dedupe(mem["facts"] + [text])
        if merged != mem["facts"]:
            mem["facts"] = merged[-_MAX_FACTS:]
            save(mem)
        facts = mem["facts"]
        if text in facts:
            return text
        for f in facts:
            if (len(f) >= 8 and (f in text or text in f)) or _sim(f, text) >= 0.55:
                return f
        return text


def push_old_turn(q, reply):
    """裁剪掉的旧轮次进入情节记忆（每条截断）。"""
    with _lock:
        mem = load()
        mem["old_turns"].append({
            "ts": datetime.datetime.now().strftime("%m-%d %H:%M"),
            "q": (q or "")[:120],
            "reply": (reply or "")[:_OLD_TURN_CHARS]})
        mem["old_turns"] = mem["old_turns"][-_MAX_OLD_TURNS:]
        save(mem)


def replace_facts(facts):
    """LLM 提炼后的整体替换（空/异常输入会被拒绝；输出前先去重）。"""
    if not isinstance(facts, list):
        return False
    clean = _dedupe([str(x) for x in facts if str(x).strip()])[:_MAX_FACTS]
    if not clean:
        return False
    with _lock:
        mem = load()
        # 与现存的合并去重（提炼结果优先）
        mem["facts"] = _dedupe(clean + [f for f in mem["facts"]
                                        if all(_sim(f, c) < 0.55 for c in clean)])
        mem["facts"] = mem["facts"][-_MAX_FACTS:]
        save(mem)
    return True


def prompt_block():
    """注入 system prompt 的记忆块。"""
    mem = load()
    parts = []
    if mem["facts"]:
        parts.append("【长期记忆（跨会话持久，已核实的事实）】")
        parts.extend(f"- {f}" for f in mem["facts"])
    if mem["old_turns"]:
        parts.append("【更早对话摘录（旧轮次，供背景参考）】")
        parts.extend(f"- ({t['ts']}) 用户问「{t['q']}」→ " +
                     t["reply"].replace("\n", " ")[:140]
                     for t in mem["old_turns"][-6:])
    return "\n".join(parts) if parts else ""


def stats():
    mem = load()
    return {"facts": len(mem["facts"]), "old_turns": len(mem["old_turns"]),
            "updated": mem.get("updated")}
