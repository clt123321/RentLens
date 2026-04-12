import sys
import os
import argparse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers import BeikeScraper, AnjukeScraper, ZiroomScraper, WuBaScraper
from filters import apply_filters, get_funnel_logger, reset_funnel_logger
from exporter import export_to_excel
from commute import CommuteCalculator
from config import BUDGET_MIN, BUDGET_MAX, SIZE_MIN, SIZE_MAX, MAX_RESULTS, COMMUTE_DESTINATIONS
from vlm_scorer import batch_evaluate
from checkpoint import save_checkpoint, load_latest_checkpoint, get_resume_stage, STAGES
from utils import log


SCRAPER_REGISTRY = {
    "beike": ("贝壳", BeikeScraper),
    "anjuke": ("安居客", AnjukeScraper),
    "ziroom": ("自如", ZiroomScraper),
    "wuba": ("58同城", WuBaScraper),
}

DEFAULT_SOURCES = ["beike", "anjuke", "ziroom", "wuba"]

COMMUTE_MAX_MINUTES = 45


def scrape_all(sources=None, pages=3):
    if sources is None:
        sources = DEFAULT_SOURCES

    all_by_source = {}

    for key in sources:
        if key not in SCRAPER_REGISTRY:
            log(f"  [!] 未知数据源: {key}")
            continue

        name, scraper_cls = SCRAPER_REGISTRY[key]
        log(f"--- 开始抓取 {name} ---")

        try:
            scraper = scraper_cls()
            listings = scraper.scrape(pages=pages, district="haidian",
                                      price_min=BUDGET_MIN, price_max=BUDGET_MAX)

            log(f"  [{name}] 获取到 {len(listings)} 条房源")
            all_by_source[name] = listings
        except Exception as e:
            log(f"  [!] {name} 抓取失败: {e}")
            all_by_source[name] = []

        time.sleep(2)

    return all_by_source


def filter_by_source(all_by_source):
    filtered_by_source = {}
    total = 0

    log("=" * 60)
    log("  信息源数量统计日志")
    log("=" * 60)

    for source_name, listings in all_by_source.items():
        raw_count = len(listings)
        if raw_count == 0:
            filtered_by_source[source_name] = []
            log(f"  {source_name}: 原始 0 条 → 保留 0 条 (无数据)")
            continue

        log(f"--- 筛选 {source_name} ({raw_count} 条) ---")
        filtered = apply_filters(listings, max_results=9999)
        filtered_count = len(filtered)

        filtered_by_source[source_name] = filtered
        log(f"  {source_name}: 筛选后 {filtered_count} 条")

        total += filtered_count

    log("=" * 60)
    log(f"  各信息源统计汇总:")
    for source_name, listings in all_by_source.items():
        raw = len(listings)
        final = len(filtered_by_source.get(source_name, []))
        rule = "完整保留(<10条)" if final < 10 else "正常筛选(≥10条)"
        log(f"    {source_name}: 原始 {raw} 条 → 最终 {final} 条 [{rule}]")
    log("=" * 60)

    return filtered_by_source, total


def add_commute_info(filtered_by_source):
    calculator = CommuteCalculator()

    if not calculator.api_key:
        log("  [!] 未配置 AMAP_API_KEY，跳过通勤计算")
        log("  [!] 请在 .env 文件中设置 AMAP_API_KEY（高德地图 Web服务 API Key）")
        return

    log("--- 计算通勤距离 ---")
    count = 0

    for source_name, listings in filtered_by_source.items():
        for i, item in enumerate(listings):
            try:
                community = item.get("community", "?")[:15]
                log(f"  计算通勤 [{i+1}/{len(listings)}]: {community}...")
                commute = calculator.calc_commute_for_listing(item, COMMUTE_DESTINATIONS)
                item["commute"] = commute
                count += 1
                for dest_name, info in commute.items():
                    mode = info.get("transit_mode", "")
                    log(f"    -> {dest_name}: {info.get('duration', 'N/A')} ({mode})")
            except Exception as e:
                log(f"  [!] 通勤计算失败: {e}")
                item["commute"] = {}

    log(f"  已计算 {count} 条房源的通勤信息")


def filter_by_commute(filtered_by_source, max_minutes=COMMUTE_MAX_MINUTES):
    from filters import get_funnel_logger
    logger = get_funnel_logger()

    log(f"--- 通勤时间筛选 (所有目的地 <= {max_minutes}分钟) ---")

    result_by_source = {}
    total_before = 0
    total_after = 0

    for source_name, listings in filtered_by_source.items():
        before = len(listings)
        total_before += before

        kept = []
        over_limit = []
        for item in listings:
            commute = item.get("commute", {})
            if not commute:
                item["commute_note"] = "无通勤数据-保留"
                kept.append(item)
                continue

            all_within = True
            has_valid_data = False
            for dest_name, info in commute.items():
                dur = info.get("duration_min")
                if dur is None:
                    all_within = False
                    continue
                if isinstance(dur, int) and dur > max_minutes:
                    all_within = False
                    has_valid_data = True
                    logger.record_loss(item, "通勤筛选", f"到{dest_name}通勤{dur}分钟 > 上限{max_minutes}分钟")
                    break
                if isinstance(dur, int):
                    has_valid_data = True

            if not has_valid_data and not all_within:
                item["commute_note"] = "通勤数据缺失-需人工核实"
                kept.append(item)
            elif all_within:
                kept.append(item)
            else:
                item["commute_note"] = f"通勤超标-仅供参考"
                over_limit.append(item)

        if len(kept) < 10 and over_limit:
            kept.extend(over_limit)
            log(f"  {source_name}: 通勤达标 {len(kept)-len(over_limit)} 条 < 10，补充超标 {len(over_limit)} 条作为参考")

        result_by_source[source_name] = kept
        total_after += len(kept)
        log(f"  {source_name}: {before} -> {len(kept)} 条 (通勤 <= {max_minutes}分钟)")

    log(f"  通勤筛选: {total_before} -> {total_after} 条")
    return result_by_source, total_after


def build_search_context(args, all_by_source, filtered_by_source, total_raw, total_filtered):
    source_details = {}
    for source_name, listings in all_by_source.items():
        filtered_list = filtered_by_source.get(source_name, [])
        source_details[source_name] = {
            "raw": len(listings),
            "final": len(filtered_list),
        }

    return {
        "conditions": {
            "district": "海淀区",
            "sources": [SCRAPER_REGISTRY.get(k, (k,))[0] for k in (args.sources or DEFAULT_SOURCES)],
            "budget_min": args.budget_min,
            "budget_max": args.budget_max,
            "size_min": args.size_min,
            "size_max": args.size_max,
            "destinations": [d["name"] for d in COMMUTE_DESTINATIONS],
            "commute_max": args.commute_max,
            "ebike_speed": CommuteCalculator.EBIKE_SPEED_KMH,
            "road_factor": CommuteCalculator.ROAD_FACTOR,
            "ebike_max_km": CommuteCalculator.EBIKE_MAX_STRAIGHT_KM,
            "pages": args.pages,
        },
        "results": {
            "total_raw": total_raw,
            "total_dedup": "见日志",
            "total_budget": "见日志",
            "total_size": "见日志",
            "total_district": "见日志",
            "total_commute": total_filtered,
            "total_final": total_filtered,
            "by_source": source_details,
        },
    }


def analyze_top_communities(filtered_by_source):
    from config import COMMUTE_DESTINATIONS
    dest_names = [d["name"] for d in COMMUTE_DESTINATIONS]

    community_stats = {}

    for source_name, listings in filtered_by_source.items():
        for item in listings:
            community = item.get("community", "") or item.get("area_name", "") or item.get("title", "未知")[:15]
            if community not in community_stats:
                min_commute = {name: 999 for name in dest_names}
                commute_modes = {name: "" for name in dest_names}
                community_stats[community] = {
                    "name": community,
                    "count": 0,
                    "prices": [],
                    "sizes": [],
                    "subways": set(),
                    "min_commute": min_commute,
                    "commute_modes": commute_modes,
                }

            stat = community_stats[community]
            stat["count"] += 1

            try:
                price = int("".join(c for c in str(item.get("price", "0")) if c.isdigit()))
                stat["prices"].append(price)
            except (ValueError, TypeError):
                pass

            try:
                size = float(item.get("size", "0"))
                stat["sizes"].append(size)
            except (ValueError, TypeError):
                pass

            for s in item.get("matched_subway", []):
                stat["subways"].add(s)

            commute = item.get("commute", {})
            for name in dest_names:
                info = commute.get(name, {})
                m = info.get("duration_min")
                if m is not None and isinstance(m, (int, float)) and m > 0 and m < stat["min_commute"][name]:
                    stat["min_commute"][name] = m
                    stat["commute_modes"][name] = info.get("transit_mode", "")

    def total_min_commute(stat):
        return sum(stat["min_commute"][name] for name in dest_names)

    ranked = sorted(community_stats.values(), key=lambda x: (
        -x["count"],
        total_min_commute(x),
        min(x["prices"]) if x["prices"] else 99999,
    ))

    return ranked


def print_community_analysis(ranked_communities):
    from config import COMMUTE_DESTINATIONS
    dest_names = [d["name"] for d in COMMUTE_DESTINATIONS]

    log("=" * 60)
    log("  重点小区推荐分析")
    log("=" * 60)

    top_n = min(5, len(ranked_communities))
    if top_n == 0:
        log("  无满足条件的小区")
        return

    for i, stat in enumerate(ranked_communities[:top_n], 1):
        avg_price = sum(stat["prices"]) / len(stat["prices"]) if stat["prices"] else 0
        avg_size = sum(stat["sizes"]) / len(stat["sizes"]) if stat["sizes"] else 0
        min_price = min(stat["prices"]) if stat["prices"] else 0
        max_price = max(stat["prices"]) if stat["prices"] else 0
        price_per_sqm = avg_price / avg_size if avg_size > 0 else 0
        subways = "、".join(sorted(stat["subways"])) if stat["subways"] else "无"

        log(f"")
        log(f"  [{i}] {stat['name']}")
        log(f"      房源数量: {stat['count']}套")
        log(f"      租金范围: {min_price}-{max_price}元/月 (均价{avg_price:.0f}元/月)")
        log(f"      面积均价: {price_per_sqm:.0f}元/㎡/月")
        log(f"      最近地铁: {subways}")
        for name in dest_names:
            m = stat["min_commute"][name]
            display = f"{m}分钟" if m < 900 else "需人工核实"
            mode = stat["commute_modes"][name]
            log(f"      通勤{name}: {display}({mode})")

        advantages = []
        if len(dest_names) >= 2:
            all_under_20 = all(stat["min_commute"][n] <= 20 for n in dest_names)
            all_under_30 = all(stat["min_commute"][n] <= 30 for n in dest_names)
            if all_under_20:
                advantages.append("双通勤极优(均≤20分钟)")
            elif all_under_30:
                advantages.append("双通勤优良(均≤30分钟)")

        for name in dest_names:
            if stat["min_commute"][name] <= 10:
                advantages.append(f"紧邻{name}")

        if len(stat["subways"]) >= 2:
            advantages.append(f"多地铁覆盖({len(stat['subways'])}站)")
        elif stat["subways"]:
            advantages.append("近地铁站")

        if price_per_sqm > 0 and price_per_sqm <= 130:
            advantages.append(f"高性价比({price_per_sqm:.0f}元/㎡)")
        elif price_per_sqm > 0 and price_per_sqm <= 160:
            advantages.append(f"性价比良好({price_per_sqm:.0f}元/㎡)")

        if stat["count"] >= 3:
            advantages.append(f"房源充足({stat['count']}套可选)")

        if advantages:
            log(f"      核心优势: {'; '.join(advantages)}")


def main():
    parser = argparse.ArgumentParser(description="高端公寓租房筛选器 - 海淀区 V2.1")
    parser.add_argument("--sources", nargs="+", default=None,
                        choices=list(SCRAPER_REGISTRY.keys()),
                        help="指定数据源 (默认全部)")
    parser.add_argument("--pages", type=int, default=3,
                        help="每个数据源抓取页数 (默认3)")
    parser.add_argument("--no-commute", action="store_true",
                        help="跳过通勤距离计算")
    parser.add_argument("--commute-max", type=int, default=COMMUTE_MAX_MINUTES,
                        help=f"通勤时间上限(分钟) (默认{COMMUTE_MAX_MINUTES})")
    parser.add_argument("--budget-min", type=int, default=BUDGET_MIN,
                        help=f"最低月租 (默认{BUDGET_MIN})")
    parser.add_argument("--budget-max", type=int, default=BUDGET_MAX,
                        help=f"最高月租 (默认{BUDGET_MAX})")
    parser.add_argument("--size-min", type=int, default=SIZE_MIN,
                        help=f"最小面积 (默认{SIZE_MIN})")
    parser.add_argument("--size-max", type=int, default=SIZE_MAX,
                        help=f"最大面积 (默认{SIZE_MAX})")
    parser.add_argument("--no-vlm", action="store_true",
                        help="跳过VLM视觉评分 (默认跳过)")
    parser.add_argument("--enable-vlm", action="store_true",
                        help="启用VLM视觉评分 (默认关闭)")
    parser.add_argument("--resume", action="store_true",
                        help="从最近的 checkpoint 恢复运行")
    args = parser.parse_args()

    reset_funnel_logger()

    log("=" * 60)
    log("  高端公寓租房筛选器 - 海淀区 V2.1 (Overnight Run)")
    log(f"  预算: {args.budget_min}-{args.budget_max}元/月")
    log(f"  面积: {args.size_min}-{args.size_max}㎡")
    log(f"  通勤目标: {', '.join(d['name'] for d in COMMUTE_DESTINATIONS)}")
    log(f"  通勤上限: {args.commute_max}分钟")
    if args.resume:
        log(f"  恢复模式: 已启用 (--resume)")
    log("=" * 60)

    all_by_source = {}
    filtered_by_source = {}
    total_raw = 0
    total_filtered = 0

    resume_stage = None
    if args.resume:
        resume_stage = get_resume_stage()
        if resume_stage == "done":
            log(f"  [恢复模式] 所有阶段已完成，将加载最新数据并生成 Excel")
        else:
            log(f"  [恢复模式] 将从 '{resume_stage}' 阶段继续")

    def should_skip(stage_name):
        if not resume_stage:
            return False
        if resume_stage == "done":
            return True
        return STAGES.index(stage_name) < STAGES.index(resume_stage)

    def should_run(stage_name):
        if not resume_stage:
            return True
        if resume_stage == "done":
            return False
        return STAGES.index(stage_name) >= STAGES.index(resume_stage)

    # --- Stage 1: 抓取 ---
    if should_skip("scrape"):
        log("[1/6] 抓取房源数据... [恢复模式-跳过]")
        ckpt = load_latest_checkpoint("scrape")
        if ckpt:
            all_by_source = ckpt["data"]
            total_raw = sum(len(v) for v in all_by_source.values())
            log(f"  [恢复] 加载抓取数据: {total_raw} 条")
        else:
            log("  [恢复] 未找到抓取 checkpoint，从头开始")
            resume_stage = None

    if should_run("scrape"):
        log("[1/6] 抓取房源数据...")
        all_by_source = scrape_all(sources=args.sources, pages=args.pages)

        total_raw = sum(len(v) for v in all_by_source.values())
        if total_raw == 0:
            log("[!] 未能获取到任何房源数据，请检查网络连接和 Cookie 配置")
            return

        log(f"共获取到 {total_raw} 条原始房源")
        save_checkpoint("scrape", all_by_source)

    # --- Stage 2: 基础筛选 ---
    if should_skip("filter"):
        log("[2/6] 基础筛选(预算+区域)... [恢复模式-跳过]")
        ckpt = load_latest_checkpoint("filter")
        if ckpt:
            filtered_by_source = ckpt["data"]
            total_filtered = sum(len(v) for v in filtered_by_source.values())
            all_by_source = ckpt.get("extra", {}).get("all_by_source", all_by_source)
            total_raw = ckpt.get("extra", {}).get("total_raw", total_raw)
            log(f"  [恢复] 加载筛选数据: {total_filtered} 条")
        else:
            log("  [恢复] 未找到筛选 checkpoint，从头执行")
            resume_stage = None

    if should_run("filter"):
        log("[2/6] 基础筛选(预算+区域)...")
        filtered_by_source, total_filtered = filter_by_source(all_by_source)

        if total_filtered == 0:
            log("[!] 筛选后无房源，建议调整筛选条件")
            return

        log(f"基础筛选后 {total_filtered} 条房源")
        save_checkpoint("filter", filtered_by_source,
                        extra={"all_by_source": all_by_source, "total_raw": total_raw})

    # --- Stage 3 & 4: 通勤 ---
    if not args.no_commute:
        if should_skip("commute"):
            log("[3/6] 计算通勤距离... [恢复模式-跳过]")
            log("[4/6] 通勤时间筛选... [恢复模式-跳过]")
            ckpt = load_latest_checkpoint("commute")
            if ckpt:
                filtered_by_source = ckpt["data"]
                total_filtered = sum(len(v) for v in filtered_by_source.values())
                log(f"  [恢复] 加载通勤数据: {total_filtered} 条")
            else:
                log("  [恢复] 未找到通勤 checkpoint，从头执行")
                resume_stage = None

        if should_run("commute"):
            log("[3/6] 计算通勤距离...")
            add_commute_info(filtered_by_source)

            log("[4/6] 通勤时间筛选...")
            filtered_by_source, total_filtered = filter_by_commute(
                filtered_by_source, max_minutes=args.commute_max)

            if total_filtered == 0:
                log(f"[!] 无房源满足通勤 <= {args.commute_max}分钟的条件，建议放宽通勤时间")
                return

            save_checkpoint("commute", filtered_by_source,
                            extra={"total_raw": total_raw})
    else:
        log("[3/6] 跳过通勤距离计算")
        log("[4/6] 跳过通勤时间筛选")

    log(f"最终保留 {total_filtered} 条房源")

    # --- Stage 5: VLM 视觉评分 ---
    vlm_enabled = args.enable_vlm
    if not vlm_enabled:
        log("[5/6] VLM 视觉评分... [默认关闭，使用 --enable-vlm 启用]")
    elif should_skip("vlm"):
        log("[5/6] VLM 视觉评分... [恢复模式-跳过]")
        ckpt = load_latest_checkpoint("vlm")
        if ckpt:
            filtered_by_source = ckpt["data"]
            total_filtered = sum(len(v) for v in filtered_by_source.values())
            log(f"  [恢复] 加载VLM评分数据: {total_filtered} 条")
        else:
            log("  [恢复] 未找到VLM checkpoint，从头执行")
            resume_stage = None

    if vlm_enabled and should_run("vlm"):
        log("[5/6] VLM 视觉评分...")
        batch_evaluate(filtered_by_source)
        save_checkpoint("vlm", filtered_by_source,
                        extra={"total_raw": total_raw})

    search_context = build_search_context(args, all_by_source, filtered_by_source, total_raw, total_filtered)

    funnel_logger = get_funnel_logger()
    funnel_logger.print_summary()

    log("[6/6] 生成 Excel (含图片+通勤+流失归因)...")
    ts = time.strftime("%Y%m%d_%H%M")
    district_label = "海淀"
    filename = f"房源_{district_label}_{args.budget_min}-{args.budget_max}元_{args.size_min}-{args.size_max}㎡_通勤≤{args.commute_max}min_{ts}.xlsx"
    export_to_excel(filtered_by_source, search_context=search_context, filename=filename,
                    funnel_logger=funnel_logger)

    log("=" * 60)
    log("  筛选结果预览")
    log("=" * 60)

    for source_name, listings in filtered_by_source.items():
        if not listings:
            continue
        log(f"--- {source_name} ({len(listings)} 条) ---")
        for i, item in enumerate(listings, 1):
            stations = "、".join(item.get("matched_subway", []))
            commute = item.get("commute", {})
            commute_str = ""
            for dest_name, info in commute.items():
                if info:
                    mode = info.get("transit_mode", "")
                    commute_str += f" | {dest_name}: {info.get('duration', 'N/A')}({mode})"
            ai_score = item.get("ai_score", 0)
            roast = item.get("roast_comment", "")
            roast_short = roast[:20] + "..." if len(roast) > 20 else roast
            log(f"  {i}. [{item.get('price', '?')}元][{item.get('size', '?')}㎡] "
                f"{item.get('title', '')[:30]}... | 地铁: {stations}{commute_str}"
                f" | AI:{ai_score:.1f}分 {roast_short}")

    ranked = analyze_top_communities(filtered_by_source)
    print_community_analysis(ranked)


if __name__ == "__main__":
    main()
