"""BitWriter + 完整回放序列化器，用于往返校验（round-trip verify）。

目的：证明 bh_replay.py 的解析不是"看起来对"，而是字段/位序完全正确。
把解析结果重新写回位流，与原始解压流逐字节比对。
"""

import zlib

from bh_replay import (
    REPLAY_BYTE_XOR, OBJ_END, OBJ_HEADER, OBJ_GAMEDATA, OBJ_RESULTS,
    OBJ_KNOCKOUT_FACES, OBJ_FACES, OBJ_INPUTS, parse_replay_bytes,
)


class BitWriter:
    """与 bh_replay.BitReader 完全对称：MSB-first，逐字节 XOR 循环表。"""

    def __init__(self):
        self.buf = bytearray()

    def write_bit(self, b: int):
        if not hasattr(self, "cur"):
            self.cur = 0
            self.nbits = 0
        self.cur = (self.cur << 1) | (1 if b else 0)
        self.nbits += 1
        if self.nbits == 8:
            self.buf.append(self.cur & 0xFF)
            self.cur = 0
            self.nbits = 0

    def write_bits(self, value: int, count: int):
        for i in range(count - 1, -1, -1):
            self.write_bit((value >> i) & 1)

    def write_bool(self, v: bool):
        self.write_bit(1 if v else 0)

    def write_u8(self, v): self.write_bits(v, 8)
    def write_u16(self, v): self.write_bits(v, 16)
    def write_i16(self, v):
        self.write_bits(v & 0xFFFF, 16)
    def write_u32(self, v): self.write_bits(v & 0xFFFFFFFF, 32)
    def write_i32(self, v):
        self.write_bits(v & 0xFFFFFFFF, 32)

    def write_string(self, s: str):
        raw = s.encode("utf-8")
        self.write_u16(len(raw))
        for b in raw:
            self.write_u8(b)

    def byte_align(self):
        # 用 0 补齐到字节边界（原实现的 bug：直接丢弃残余位，导致少一个字节）
        while getattr(self, "nbits", 0):
            self.write_bit(0)

    def to_bytes(self) -> bytes:
        self.byte_align()
        # 头部尾部各写一次版本号 + 魔数，与游戏端对齐后再 XOR
        out = bytes(b ^ REPLAY_BYTE_XOR[i & 63] for i, b in enumerate(self.buf))
        return out


def _owned_taunts(bw: BitWriter, pd, legacy: bool = False):
    """回写已拥有嘲讽。

    legacy（<=270）：while bool + 单个 int32（逐个存 taunt ID）。
    现代（>=271）：原样回写位域（保留尾部全零冗余位域）；
    没有原始位域时才按 ID 重算。
    """
    if legacy:
        for t in pd.get("owned_taunts") or []:
            bw.write_bool(True)
            bw.write_i32(t)
        bw.write_bool(False)
        return
    bfs = pd.get("owned_taunt_bitfields")
    if not bfs:
        owned = pd.get("owned_taunts") or []
        if not owned:
            bw.write_bool(False)
            return
        bfs = [0] * (max(owned) // 32 + 1)
        for t in owned:
            bfs[t // 32] |= 1 << (t % 32)
    for bf in bfs:
        bw.write_bool(True)
        bw.write_u32(bf)
    bw.write_bool(False)


def _write_hero(bw: BitWriter, h, legacy: bool = False):
    bw.write_u32(h["hero_id"])
    bw.write_u32(h["costume_id"])
    bw.write_u32(h["stance"])
    if legacy:
        bw.write_u16(h["weapon_skin2"])
        bw.write_u16(h["weapon_skin1"])
        return
    bw.write_bool(h.get("ignored", False))   # 被游戏忽略但原样写回的位
    bw.write_bits(h["weapon_skin2"], 15)
    bw.write_bool(h["morph_weapon2"])
    bw.write_bits(h["weapon_skin1"], 15)


def _write_player(bw: BitWriter, pd, legacy: bool = False):
    for k in ("color_scheme", "spawn_bot", "companion", "emitter",
              "trail_effect", "player_theme"):
        bw.write_u32(pd[k])
    for t in pd["taunts"]:
        bw.write_u32(t)
    bw.write_u16(pd["win_taunt"])
    bw.write_u16(pd["lose_taunt"])
    _owned_taunts(bw, pd, legacy)
    bw.write_u16(pd["avatar"])
    bw.write_i32(pd["team"])
    bw.write_i32(pd["connection_time"])
    for h in pd["heroes"]:
        _write_hero(bw, h, legacy)
    bw.write_bool(pd["is_bot"])
    bw.write_bool(pd["handicaps_enabled"])
    if pd["handicaps_enabled"]:
        bw.write_u32(pd["handicap_stock"])
        bw.write_u32(pd["handicap_dmg_done"])
        bw.write_u32(pd["handicap_dmg_taken"])


def _write_entity(bw: BitWriter, e, legacy: bool = False):
    bw.write_i32(e["ent_id"])
    bw.write_i32(e["player_id"])
    bw.write_string(e["name"])
    _write_player(bw, e["player_data"], legacy)


def _write_settings(bw: BitWriter, s):
    for k in ("flags", "max_players", "duration", "round_duration",
              "starting_lives", "scoring_type", "score_to_win", "game_speed",
              "damage_multiplier", "level_set", "item_spawn_ruleset",
              "weapon_spawn_rate", "gadget_spawn_rate", "custom_gadgets",
              "variation"):
        bw.write_u32(s[k])


def _write_gamedata(bw: BitWriter, gd, legacy: bool = False):
    _write_settings(bw, gd["settings"])
    bw.write_u32(gd["level_id"])
    bw.write_u16(len(gd["entities"][0]["player_data"]["heroes"]))
    for e in gd["entities"]:
        bw.write_bool(True)
        _write_entity(bw, e, legacy)
    bw.write_bool(False)
    bw.write_u32(gd["checksum"])


def _write_result(bw: BitWriter, r):
    bw.write_u32(r["length"])
    if r["scores"]:
        bw.write_bool(True)
        for ent, score in r["scores"].items():
            bw.write_bool(True)
            bw.write_bits(ent, 5)
            bw.write_i16(score)
        bw.write_bool(False)
    else:
        bw.write_bool(False)
    bw.write_u32(r["fanfare"])


def _write_faces(bw: BitWriter, faces):
    for f in faces:
        bw.write_bool(True)
        bw.write_bits(f["ent_id"], 5)
        bw.write_i32(f["time"])
    bw.write_bool(False)


def _write_inputs(bw: BitWriter, inputs):
    for ent, seq in inputs.items():
        bw.write_bool(True)
        bw.write_bits(ent, 5)
        bw.write_i32(len(seq))
        for ts, flags in seq:
            bw.write_i32(ts)
            if flags:
                bw.write_bool(True)
                bw.write_bits(flags, 14)
            else:
                bw.write_bool(False)
    bw.write_bool(False)


def replay_to_payload(rep: dict) -> bytes:
    """把解析结果重新序列化为「解压后的位流」（未压缩）。

    严格按解析时记录的对象顺序回写（11.00 的真实顺序是
    Header → GameData → Results… → Inputs → KO Faces → End），
    结尾只有一个 End 标签 + 字节对齐，没有版本号/魔数尾。
    """
    bw = BitWriter()
    legacy = bool(rep.get("legacy_layout", False))
    bw.write_u32(rep["version"])
    for tag in rep.get("order", []):
        bw.write_bits(tag, 4)
        if tag == OBJ_HEADER:
            bw.write_u32(rep["header"]["random_seed"])
            bw.write_u32(rep["header"]["playlist_id"])
            if rep["header"]["playlist_id"] != 0:
                bw.write_string(rep["header"]["playlist_name"])
            bw.write_bool(rep["header"]["online_game"])
        elif tag == OBJ_GAMEDATA:
            _write_gamedata(bw, rep["game_data"], legacy)
        elif tag == OBJ_RESULTS:
            # 按出现顺序取对应 result
            idx = _take(rep, "_res_i")
            _write_result(bw, rep["results"][idx])
        elif tag == OBJ_KNOCKOUT_FACES:
            _write_faces(bw, rep["deaths"] or [])
        elif tag == OBJ_FACES:
            _write_faces(bw, rep["faces"] or [])
        elif tag == OBJ_INPUTS:
            _write_inputs(bw, rep["inputs"])
        elif tag == OBJ_END:
            pass
    bw.byte_align()
    return bw.to_bytes()


def _take(rep, key):
    i = rep.get(key, 0)
    rep[key] = i + 1
    return i


def roundtrip_check(raw: bytes):
    """返回 (是否一致, 原解压流, 重序列化流, 首个差异字节位置)。"""
    orig = zlib.decompressobj().decompress(raw)
    rep = parse_replay_bytes(raw)
    mine = replay_to_payload(rep)
    if orig == mine:
        return True, orig, mine, -1
    n = min(len(orig), len(mine))
    i = 0
    while i < n and orig[i] == mine[i]:
        i += 1
    return False, orig, mine, i


if __name__ == "__main__":
    import sys, glob, os
    d = sys.argv[1] if len(sys.argv) > 1 else __import__("bh_paths").replay_dir()
    files = sorted(glob.glob(os.path.join(d, "*.replay")))
    ok = bad = err = 0
    fails = []
    for p in files:
        try:
            same, orig, mine, i = roundtrip_check(open(p, "rb").read())
        except Exception as e:
            err += 1
            fails.append((os.path.basename(p), f"exception {e}"))
            continue
        if same:
            ok += 1
        else:
            bad += 1
            fails.append((os.path.basename(p),
                          f"len {len(orig)}->{len(mine)}, first diff @byte {i} "
                          f"orig={orig[i:i+6].hex(' ')} mine={mine[i:i+6].hex(' ')}"))
    print(f"round-trip: {ok} identical / {bad} mismatch / {err} error  (of {len(files)})")
    for name, why in fails[:10]:
        print("  MISMATCH", name, "|", why)
