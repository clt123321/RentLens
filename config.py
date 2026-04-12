import os
from dotenv import load_dotenv

load_dotenv()

CITY_NAME = os.getenv("CITY_NAME", "北京")
CITY_NAME_FULL = os.getenv("CITY_NAME_FULL", f"{CITY_NAME}市")

SUBWAY_STATIONS_HAIDIAN = [
    "西北旺", "西二旗", "上地", "中关村", "海淀黄庄", "知春路",
    "知春里", "五道口", "清华东路西口", "北京大学东门", "圆明园",
    "西苑", "北宫门", "安河桥北", "马连洼", "肖家河",
    "永丰", "永丰南", "屯佃", "稻香湖路", "温阳路",
    "北安河", "软件园西", "软件园", "西小口", "永泰庄",
    "清河", "朱房北", "上清桥", "学清路", "六道口",
    "学院桥", "西土城", "蓟门桥", "大钟寺", "国家图书馆",
    "魏公村", "人民大学", "苏州街", "巴沟", "火器营",
    "长春桥", "车道沟", "慈寿寺", "西钓鱼台",
]

BUDGET_MIN = 8000
BUDGET_MAX = 15000
MAX_RESULTS = 0
SIZE_MIN = 0
SIZE_MAX = 90


def _load_commute_destinations():
    env_val = os.getenv("COMMUTE_DESTINATIONS", "")
    if env_val:
        destinations = []
        for pair in env_val.split(";"):
            pair = pair.strip()
            if "|" in pair:
                name, address = pair.split("|", 1)
                destinations.append({"name": name.strip(), "address": address.strip()})
        if destinations:
            return destinations
    return [
        {"name": "目标地点A", "address": f"{CITY_NAME_FULL}海淀区中关村"},
        {"name": "目标地点B", "address": f"{CITY_NAME_FULL}海淀区西二旗"},
    ]


COMMUTE_DESTINATIONS = _load_commute_destinations()
