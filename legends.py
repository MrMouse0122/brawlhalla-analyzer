"""Brawlhalla 英雄 ID → 名字映射。

hero_id 采用游戏名册顺序（与官方 API api.brawlhalla.com/static/legends 及
各统计站一致；alpha 时代名册与发布顺序有差异：Sir Roland=8、Thatch=9、
Ada=10、Scarlet=11、Sentinel=12）。来源：Brawlhalla 官方网站英雄列表 +
brawlhalla.wiki.gg/wiki/Legends + 社区 API 对照。
未知 ID 一律回退为 "Legend N"（游戏更新出新英雄后在此表追加即可）。
"""

LEGENDS = {
    1: "Bödvar", 2: "Cassidy", 3: "Orion", 4: "Lord Vraxx", 5: "Gnash",
    6: "Queen Nai", 7: "Hattori", 8: "Sir Roland", 9: "Thatch", 10: "Ada",
    11: "Scarlet", 12: "Sentinel", 13: "Lucien", 14: "Teros", 15: "Brynn",
    16: "Asuri", 17: "Barraza", 18: "Ember", 19: "Azoth", 20: "Koji",
    21: "Ulgrim", 22: "Diana", 23: "Jhala", 24: "Kor", 25: "Wu Shang",
    26: "Val", 27: "Ragnir", 28: "Cross", 29: "Mirage", 30: "Nix",
    31: "Mordex", 32: "Yumiko", 33: "Artemis", 34: "Caspian", 35: "Sidra",
    36: "Xull", 37: "Kaya", 38: "Isaiah", 39: "Jiro", 40: "Lin Fei",
    41: "Zariel", 42: "Rayman", 43: "Dusk", 44: "Fait", 45: "Thor",
    46: "Petra", 47: "Vector", 48: "Volkov", 49: "Onyx", 50: "Jaeyun",
    51: "Mako", 52: "Magyar", 53: "Reno", 54: "Munin", 55: "Arcadia",
    56: "Ezio", 57: "Tezca", 58: "Thea", 59: "Red Raptor", 60: "Loki",
    61: "Seven", 62: "Vivi", 63: "Imugi", 64: "King Zuva", 65: "Priya",
    66: "Ransom", 67: "Lady Vera", 68: "Rupture", 69: "Aurus",
}


def legend_name(hero_id):
    """返回英雄名；未知 ID 回退为 Legend N。"""
    try:
        return LEGENDS.get(int(hero_id), f"Legend {hero_id}")
    except (TypeError, ValueError):
        return str(hero_id)
