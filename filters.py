import csv
import os
from config import SUBWAY_STATIONS_HAIDIAN, BUDGET_MIN, BUDGET_MAX, MAX_RESULTS, SIZE_MIN, SIZE_MAX

HAIDIAN_AREAS = [
    "西北旺", "万柳", "中关村", "海淀黄庄", "知春路", "知春里",
    "五道口", "上地", "清河", "西二旗", "西三旗", "马连洼",
    "永丰", "温泉", "苏家坨", "上庄", "香山", "四季青",
    "紫竹桥", "公主坟", "甘家口", "牡丹园", "小西天",
    "北太平庄", "学院路", "蓟门桥", "魏公村", "人民大学",
    "苏州街", "巴沟", "火器营", "长春桥", "世纪城",
]

SOURCE_CONFIDENCE = {
    "贝壳": "高",
    "自如": "高",
    "安居客": "中",
    "58同城": "低",
}


class LossRecord:
    __slots__ = ("source", "title", "price", "stage", "reason", "detail", "url")

    def __init__(self, source, title, price, stage, reason, detail="", url=""):
        self.source = source
        self.title = title
        self.price = price
        self.stage = stage
        self.reason = reason
        self.detail = detail
        self.url = url

    def to_dict(self):
        return {
            "source": self.source,
            "title": self.title,
            "price": self.price,
            "stage": self.stage,
            "reason": self.reason,
            "detail": self.detail,
            "url": self.url,
        }


class FunnelLogger:
    def __init__(self):
        self._records = []
        self._stage_counts = {}

    def record_loss(self, item, stage, reason, detail=""):
        source = item.get("source", "未知")
        title = item.get("title", "")[:40]
        price = item.get("price", "")
        url = item.get("url", "")
        rec = LossRecord(source, title, price, stage, reason, detail, url)
        self._records.append(rec)

        key = f"{source}|{stage}"
        if key not in self._stage_counts:
            self._stage_counts[key] = 0
        self._stage_counts[key] += 1

    def get_records(self):
        return [r.to_dict() for r in self._records]

    def get_summary(self):
        summary = {}
        for key, count in self._stage_counts.items():
            source, stage = key.split("|", 1)
            if source not in summary:
                summary[source] = {}
            summary[source][stage] = count
        return summary

    def print_summary(self):
        from utils import log
        summary = self.get_summary()
        log("=" * 60)
        log("  【房源流失归因分析】")
        log("=" * 60)
        for source in ["贝壳", "自如", "安居客", "58同城"]:
            if source not in summary:
                continue
            log(f"  {source} (置信度: {SOURCE_CONFIDENCE.get(source, '?')}):")
            for stage, count in summary[source].items():
                log(f"    - {stage}: 流失 {count} 条")
        log("=" * 60)

    def export_csv(self, filepath):
        records = self.get_records()
        if not records:
            return
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["source", "title", "price", "stage", "reason", "detail"])
            writer.writeheader()
            writer.writerows(records)


_funnel_logger = FunnelLogger()


def get_funnel_logger():
    return _funnel_logger


def reset_funnel_logger():
    global _funnel_logger
    _funnel_logger = FunnelLogger()


def parse_price(price_str):
    try:
        cleaned = "".join(c for c in str(price_str) if c.isdigit())
        return int(cleaned) if cleaned else 0
    except (ValueError, TypeError):
        return 0


def filter_by_budget(listings, budget_min=BUDGET_MIN, budget_max=BUDGET_MAX):
    logger = get_funnel_logger()
    filtered = []
    for item in listings:
        price = parse_price(item.get("price", "0"))
        if budget_min <= price <= budget_max:
            item["price_num"] = price
            filtered.append(item)
        else:
            if price < budget_min:
                logger.record_loss(item, "预算筛选", f"价格{price}元 < 下限{budget_min}元")
            else:
                logger.record_loss(item, "预算筛选", f"价格{price}元 > 上限{budget_max}元")
    return filtered


def filter_by_size(listings, size_min=SIZE_MIN, size_max=SIZE_MAX):
    logger = get_funnel_logger()
    filtered = []
    for item in listings:
        size_str = item.get("size", "")
        try:
            size_val = float(size_str) if size_str else 0
        except (ValueError, TypeError):
            size_val = 0

        if size_val == 0:
            filtered.append(item)
            continue

        if size_min <= size_val <= size_max:
            item["size_num"] = size_val
            filtered.append(item)
        else:
            if size_val < size_min:
                logger.record_loss(item, "面积筛选", f"面积{size_val}㎡ < 下限{size_min}㎡")
            else:
                logger.record_loss(item, "面积筛选", f"面积{size_val}㎡ > 上限{size_max}㎡")
    return filtered


def filter_by_district(listings, districts=None):
    if districts is None:
        districts = ["海淀区", "海淀"]

    logger = get_funnel_logger()
    filtered = []
    for item in listings:
        district = item.get("district", "")
        if district in districts:
            filtered.append(item)
        else:
            logger.record_loss(item, "区域筛选", f"区域'{district}'不在海淀区")
    return filtered


def filter_by_area(listings, areas=None):
    if areas is None:
        areas = HAIDIAN_AREAS

    logger = get_funnel_logger()
    filtered = []
    for item in listings:
        area_name = item.get("area_name", "")
        community = item.get("community", "")
        text = f"{area_name} {community} {item.get('title', '')} {item.get('desc', '')}"
        matched_areas = [a for a in areas if a in text]
        if matched_areas:
            item["matched_area"] = matched_areas
            filtered.append(item)
        else:
            logger.record_loss(item, "板块筛选", f"未匹配海淀板块")
    return filtered


def filter_by_subway(listings, stations=None):
    if stations is None:
        stations = SUBWAY_STATIONS_HAIDIAN

    filtered = []
    for item in listings:
        text = f"{item.get('title', '')} {item.get('desc', '')}"
        matched_stations = [s for s in stations if s in text]
        if matched_stations:
            item["matched_subway"] = matched_stations
            filtered.append(item)
    return filtered


def deduplicate(listings):
    logger = get_funnel_logger()
    seen = set()
    result = []
    for item in listings:
        title = item.get("title", "")[:30]
        price = item.get("price", "")
        source = item.get("source", "")
        key = f"{source}_{title}_{price}"
        if key not in seen:
            seen.add(key)
            result.append(item)
        else:
            logger.record_loss(item, "去重", f"与已有房源重复")
    return result


def apply_filters(listings, budget_min=BUDGET_MIN, budget_max=BUDGET_MAX,
                  size_min=SIZE_MIN, size_max=SIZE_MAX, max_results=MAX_RESULTS):
    from utils import log

    logger = get_funnel_logger()

    log(f"筛选前: {len(listings)} 条房源")

    listings = deduplicate(listings)
    log(f"去重后: {len(listings)} 条")

    budget_filtered = filter_by_budget(listings, budget_min, budget_max)
    log(f"预算筛选 ({budget_min}-{budget_max}元): {len(budget_filtered)} 条")

    if size_min > 0 or size_max > 0:
        size_filtered = filter_by_size(budget_filtered, size_min, size_max)
        log(f"面积筛选 ({size_min}-{size_max}㎡): {len(size_filtered)} 条")

        if len(size_filtered) < 5:
            size_filtered = filter_by_size(budget_filtered, size_min, 120)
            log(f"面积放宽 ({size_min}-120㎡): {len(size_filtered)} 条")

        if not size_filtered:
            size_filtered = budget_filtered
            log(f"面积筛选无结果，回退到预算筛选结果")
    else:
        size_filtered = budget_filtered
        log(f"面积筛选: 已跳过（未设置面积限制）")

    district_filtered = filter_by_district(size_filtered)
    log(f"区域筛选 (海淀区): {len(district_filtered)} 条")

    if not district_filtered:
        area_filtered = filter_by_area(size_filtered)
        log(f"板块筛选 (海淀相关): {len(area_filtered)} 条")
        final_filtered = area_filtered
    else:
        final_filtered = district_filtered

    for item in final_filtered:
        text = f"{item.get('title', '')} {item.get('desc', '')} {item.get('area_name', '')} {item.get('community', '')}"
        matched_stations = [s for s in SUBWAY_STATIONS_HAIDIAN if s in text]
        item["matched_subway"] = matched_stations

    for item in final_filtered:
        item["source_confidence"] = SOURCE_CONFIDENCE.get(item.get("source", ""), "低")

    final_filtered.sort(key=lambda x: (-len(x.get("matched_subway", [])), x.get("price_num", 0)))

    if max_results is not None and max_results > 0 and len(final_filtered) > max_results:
        for item in final_filtered[max_results:]:
            logger.record_loss(item, "数量截取", f"超过最大保留数{max_results}条")
        final_filtered = final_filtered[:max_results]
        log(f"截取前 {max_results} 条")

    return final_filtered
