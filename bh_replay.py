"""Brawlhalla .replay parser (patch 10.09+ / 11.00 format), pure Python.

File layout:
    whole file = zlib stream
    decompressed payload = MSB-first bit stream, every byte XORed with
    REPLAY_BYTE_XOR[index % 64]

    uint32 version
    then a sequence of objects:
        4-bit type tag
        ... payload ...
    until type == End(2)

Object types: 1 Inputs, 2 End, 3 Header, 4 GameData,
              5 KnockoutFaces(=deaths), 6 Results, 7 Faces, 8 Invalid
"""

import zlib

REPLAY_BYTE_XOR = bytes([
    0x6B, 0x10, 0xDE, 0x3C, 0x44, 0x4B, 0xD1, 0x46, 0xA0, 0x10, 0x52, 0xC1,
    0xB2, 0x31, 0xD3, 0x6A, 0xFB, 0xAC, 0x11, 0xDE, 0x06, 0x68, 0x08, 0x78,
    0x8C, 0xD5, 0xB3, 0xF9, 0x6A, 0x40, 0xD6, 0x13, 0x0C, 0xAE, 0x9D, 0xC5,
    0xD4, 0x6B, 0x54, 0x72, 0xFC, 0x57, 0x5D, 0x1A, 0x06, 0x73, 0xC2, 0x51,
    0x4B, 0xB0, 0xC9, 0x8C, 0x78, 0x04, 0x11, 0x7A, 0xEF, 0x74, 0x3E, 0x46,
    0x39, 0xA0, 0xC7, 0xA6,
])

OBJ_INPUTS = 1
OBJ_END = 2
OBJ_HEADER = 3
OBJ_GAMEDATA = 4
OBJ_KNOCKOUT_FACES = 5
OBJ_RESULTS = 6
OBJ_FACES = 7
OBJ_INVALID = 8

# input flag bits (14-bit field)
F_UP = 0x0001
F_DROP = 0x0002
F_LEFT = 0x0004
F_RIGHT = 0x0008
F_JUMP = 0x0010
F_NEUTRAL_PRIORITY = 0x0020
F_HEAVY = 0x0040
F_LIGHT = 0x0080
F_DODGE = 0x0100
F_PICKUP_THROW = 0x0200
F_TAUNT_UP = 0x0400
F_TAUNT_RIGHT = 0x0800
F_TAUNT_DOWN = 0x1000
F_TAUNT_LEFT = 0x2000


class ReplayParseError(Exception):
    pass


class BitReader:
    def __init__(self, data: bytes):
        self.buf = bytes(b ^ REPLAY_BYTE_XOR[i & 63] for i, b in enumerate(data))
        self.n = len(self.buf)
        self.byte = 0
        self.bit = 8  # force load on first read
        self.cur = 0

    def read_bit(self) -> int:
        if self.bit == 8:
            if self.byte >= self.n:
                raise EOFError("replay bit stream exhausted")
            self.cur = self.buf[self.byte]
            self.byte += 1
            self.bit = 0
        v = (self.cur >> (7 - self.bit)) & 1
        self.bit += 1
        return v

    def read_bits(self, count: int) -> int:
        v = 0
        for _ in range(count):
            v = (v << 1) | self.read_bit()
        return v

    def read_bool(self) -> bool:
        return bool(self.read_bit())

    def read_u8(self):
        return self.read_bits(8)

    def read_u16(self):
        return self.read_bits(16)

    def read_i16(self):
        v = self.read_bits(16)
        return v - 0x10000 if v & 0x8000 else v

    def read_u32(self):
        return self.read_bits(32)

    def read_i32(self):
        v = self.read_bits(32)
        return v - 0x100000000 if v & 0x80000000 else v

    def read_string(self) -> str:
        size = self.read_u16()
        raw = bytes(self.read_u8() for _ in range(size))
        return raw.decode("utf-8", errors="replace")


def _owned_taunts(br: BitReader, legacy: bool = False):
    """返回 (解码出的嘲讽 ID 列表, 原始位域列表)。

    位域要原样保留：游戏会写入尾部全零的冗余位域，按 ID 重算数量会丢位。

    版本差异（旧项目 10.11 实测记录）：
    - legacy（version <= 270）：while bool + 单个 int32（逐个存 taunt ID）
    - 现代（version >= 271）：while bool + uint32 位域（每域 32 个 ID 压缩）
    """
    owned = []
    bitfields = []
    if legacy:
        while br.read_bool():
            owned.append(br.read_i32())
        return owned, []
    taunt = 0
    while br.read_bool():
        bitfield = br.read_u32()
        bitfields.append(bitfield)
        for j in range(32):
            if bitfield & (1 << j):
                owned.append(taunt)
            taunt += 1
    return owned, bitfields


def _read_hero_type(br: BitReader, legacy: bool = False):
    """读取单个 hero 条目。

    版本差异（由旧项目 brawlhalla-dodge 的 10.11 实测记录确认）：
    - legacy（version <= 270，即 10.11）：weapon_skin 是两个 16bit
    - 现代（version >= 271，即 11.00）：ignored bool + 15bit + morph bool + 15bit
    两边总长都是 128 bit，但内部布局不同——直接混用会静默错位。
    """
    hero_id = br.read_u32()
    costume_id = br.read_u32()
    stance = br.read_u32()
    if legacy:
        weapon_skin2 = br.read_u16()
        weapon_skin1 = br.read_u16()
        return {
            "hero_id": hero_id,
            "costume_id": costume_id,
            "stance": stance,
            "ignored": False,
            "weapon_skin1": weapon_skin1,
            "weapon_skin2": weapon_skin2,
            "morph_weapon2": False,
        }
    ignored = br.read_bool()  # 游戏会原样写回，必须保留才能往返一致
    weapon_skin2 = br.read_bits(15)
    morph2 = br.read_bool()
    weapon_skin1 = br.read_bits(15)
    return {
        "hero_id": hero_id,
        "costume_id": costume_id,
        "stance": stance,
        "ignored": bool(ignored),
        "weapon_skin1": weapon_skin1,
        "weapon_skin2": weapon_skin2,
        "morph_weapon2": bool(morph2),
    }


def _read_player_data(br: BitReader, hero_count: int, legacy: bool = False):
    pd = {
        "color_scheme": br.read_u32(),
        "spawn_bot": br.read_u32(),
        "companion": br.read_u32(),
        "emitter": br.read_u32(),
        "trail_effect": br.read_u32(),
        "player_theme": br.read_u32(),
    }
    pd["taunts"] = [br.read_u32() for _ in range(8)]
    pd["win_taunt"] = br.read_u16()
    pd["lose_taunt"] = br.read_u16()
    pd["owned_taunts"], pd["owned_taunt_bitfields"] = _owned_taunts(br, legacy)
    pd["avatar"] = br.read_u16()
    pd["team"] = br.read_i32()
    pd["connection_time"] = br.read_i32()
    pd["heroes"] = [_read_hero_type(br, legacy) for _ in range(hero_count)]
    pd["is_bot"] = br.read_bool()
    handicap = br.read_bool()
    pd["handicaps_enabled"] = handicap
    if handicap:
        pd["handicap_stock"] = br.read_u32()
        pd["handicap_dmg_done"] = br.read_u32()
        pd["handicap_dmg_taken"] = br.read_u32()
    return pd


def _read_entity(br: BitReader, hero_count: int, legacy: bool = False):
    ent_id = br.read_i32()
    player_id = br.read_i32()
    name = br.read_string()
    return {
        "ent_id": ent_id,
        "player_id": player_id,
        "name": name,
        "player_data": _read_player_data(br, hero_count, legacy),
    }


def _read_game_settings(br: BitReader):
    return {
        "flags": br.read_u32(),
        "max_players": br.read_u32(),
        "duration": br.read_u32(),
        "round_duration": br.read_u32(),
        "starting_lives": br.read_u32(),
        "scoring_type": br.read_u32(),
        "score_to_win": br.read_u32(),
        "game_speed": br.read_u32(),
        "damage_multiplier": br.read_u32(),
        "level_set": br.read_u32(),
        "item_spawn_ruleset": br.read_u32(),
        "weapon_spawn_rate": br.read_u32(),
        "gadget_spawn_rate": br.read_u32(),
        "custom_gadgets": br.read_u32(),
        "variation": br.read_u32(),
    }


def _read_game_data(br: BitReader, legacy: bool = False):
    gd = {"settings": _read_game_settings(br)}
    gd["level_id"] = br.read_u32()
    hero_count = br.read_u16()
    if not (1 <= hero_count <= 5):
        raise ReplayParseError(f"bad hero count {hero_count}")
    entities = []
    while br.read_bool():
        entities.append(_read_entity(br, hero_count, legacy))
    gd["entities"] = entities
    gd["checksum"] = br.read_u32()
    return gd


def _read_result(br: BitReader):
    res = {"length": br.read_u32(), "scores": {}}
    if br.read_bool():
        while br.read_bool():
            ent = br.read_bits(5)
            score = br.read_i16()
            res["scores"][ent] = score
    res["fanfare"] = br.read_u32()
    return res


def _read_faces(br: BitReader):
    faces = []
    while br.read_bool():
        ent = br.read_bits(5)
        ts = br.read_i32()
        faces.append({"ent_id": ent, "time": ts})
    return faces


def _read_inputs(br: BitReader):
    inputs = {}
    while br.read_bool():
        ent = br.read_bits(5)
        count = br.read_i32()
        seq = []
        for _ in range(count):
            ts = br.read_i32()
            flags = br.read_bits(14) if br.read_bool() else 0
            seq.append((ts, flags))
        inputs[ent] = seq
    return inputs


def parse_replay_bytes(raw: bytes) -> dict:
    """Parse a raw .replay file into a dict."""
    try:
        payload = zlib.decompressobj().decompress(raw)
    except zlib.error as e:
        raise ReplayParseError(f"zlib: {e}") from e
    br = BitReader(payload)

    rep = {
        "version": br.read_u32(),
        "header": None,
        "game_data": None,
        "results": [],
        "deaths": None,   # knockout faces
        "faces": None,
        "inputs": None,
    }
    # 版本布局分发：<=270 (10.11) 是 legacy 布局，>=271 (11.00) 是现代布局。
    # 差异点在 hero 条目内部结构和 owned_taunts 语义（见两个函数的 docstring）。
    legacy = rep["version"] <= 270
    rep["legacy_layout"] = legacy

    while True:
        tag = br.read_bits(4)
        rep.setdefault("order", []).append(tag)
        if tag == OBJ_END:
            break
        elif tag == OBJ_HEADER:
            rep["header"] = {
                "random_seed": br.read_u32(),
                "playlist_id": br.read_u32(),
            }
            rep["header"]["playlist_name"] = (
                br.read_string() if rep["header"]["playlist_id"] != 0 else None
            )
            rep["header"]["online_game"] = br.read_bool()
        elif tag == OBJ_GAMEDATA:
            rep["game_data"] = _read_game_data(br, legacy)
        elif tag == OBJ_RESULTS:
            rep["results"].append(_read_result(br))
        elif tag == OBJ_KNOCKOUT_FACES:
            rep["deaths"] = _read_faces(br)
        elif tag == OBJ_FACES:
            rep["faces"] = _read_faces(br)
        elif tag == OBJ_INPUTS:
            rep["inputs"] = _read_inputs(br)
        elif tag == OBJ_INVALID:
            raise ReplayParseError("object type 8: replay marked invalid")
        else:
            raise ReplayParseError(f"unknown object type {tag}")

    return rep


def parse_replay_file(path: str) -> dict:
    with open(path, "rb") as f:
        return parse_replay_bytes(f.read())


def flag_names(flags: int):
    names = []
    for bit, nm in (
        (F_UP, "UP"), (F_DROP, "DOWN"), (F_LEFT, "LEFT"), (F_RIGHT, "RIGHT"),
        (F_JUMP, "JUMP"), (F_NEUTRAL_PRIORITY, "NSIG"), (F_HEAVY, "HEAVY"),
        (F_LIGHT, "LIGHT"), (F_DODGE, "DODGE"), (F_PICKUP_THROW, "THROW"),
        (F_TAUNT_UP, "TAUNT"), (F_TAUNT_RIGHT, "TAUNT"),
        (F_TAUNT_DOWN, "TAUNT"), (F_TAUNT_LEFT, "TAUNT"),
    ):
        if flags & bit:
            names.append(nm)
    return names


if __name__ == "__main__":
    import sys, json, os
    p = sys.argv[1]
    rep = parse_replay_file(p)
    print("file:", os.path.basename(p))
    print("version:", rep["version"])
    print("header:", rep["header"])
    gd = rep["game_data"]
    print("level:", gd["level_id"], "settings:", gd["settings"])
    for e in gd["entities"]:
        print("  ent", e["ent_id"], repr(e["name"]), "team", e["player_data"]["team"],
              "heroes", [h["hero_id"] for h in e["player_data"]["heroes"]],
              "bot", e["player_data"]["is_bot"])
    print("results:", rep["results"])
    print("deaths:", rep["deaths"])
    for ent, seq in (rep["inputs"] or {}).items():
        print(f"  inputs ent{ent}: {len(seq)} changes, first10={seq[:10]}")
