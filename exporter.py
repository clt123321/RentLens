import os
import requests
import time
import datetime
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
import xlsxwriter
from PIL import Image
from utils import log

IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

IMG_WIDTH = 200
IMG_HEIGHT = 150


def ensure_dirs():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def download_image(url, filepath, timeout=5):
    if not url or not url.startswith("http"):
        return False
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": url,
        })
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        img = img.convert("RGB")
        img = img.resize((IMG_WIDTH, IMG_HEIGHT), Image.LANCZOS)
        img.save(filepath, "JPEG", quality=85)
        return True
    except Exception:
        return False


SHEET_HEADERS_TEMPLATE = [
    "序号", "首图", "推荐理由", "房源链接",
    "房源标题", "租金(元/月)", "面积(㎡)", "户型", "匹配地铁站",
]

COL_WIDTHS_TEMPLATE = [6, 29, 40, 35, 40, 14, 10, 10, 20]


def _build_sheet_headers():
    from config import COMMUTE_DESTINATIONS
    headers = list(SHEET_HEADERS_TEMPLATE)
    widths = list(COL_WIDTHS_TEMPLATE)
    for dest in COMMUTE_DESTINATIONS:
        headers.append(f"通勤-{dest['name']}")
        widths.append(14)
        headers.append("交通方式")
        widths.append(12)
    headers.extend(["来源", "置信度"])
    widths.extend([10, 8])
    return headers, widths


def _compute_recommendation(item):
    from config import COMMUTE_DESTINATIONS
    score = 0
    reasons = []

    commute = item.get("commute", {})

    dest_names = [d["name"] for d in COMMUTE_DESTINATIONS]
    dest_mins = {}
    for name in dest_names:
        info = commute.get(name, {})
        m = info.get("duration_min")
        if m is None or not isinstance(m, int):
            m = 999
        dest_mins[name] = m

    for name in dest_names:
        m = dest_mins[name]
        if m <= 15:
            reasons.append(f"距{name}仅{m}分钟(极近)")
            score += 2
        elif m <= 25:
            reasons.append(f"距{name}{m}分钟(较近)")
            score += 1

    if len(dest_names) >= 2:
        all_under_20 = all(dest_mins[n] <= 20 for n in dest_names)
        all_under_30 = all(dest_mins[n] <= 30 for n in dest_names)
        if all_under_20:
            reasons.append("双通勤极优(均≤20分钟)")
            score += 1
        elif all_under_30:
            reasons.append("双通勤优良(均≤30分钟)")

    try:
        price = int("".join(c for c in str(item.get("price", "0")) if c.isdigit()))
    except (ValueError, TypeError):
        price = 0

    try:
        size = float(item.get("size", "0"))
    except (ValueError, TypeError):
        size = 0

    if price > 0:
        if price <= 9000:
            reasons.append(f"价格实惠({price}元/月)")
            score += 1
        elif price <= 12000:
            reasons.append(f"价格适中({price}元/月)")

    if price > 0 and size > 0:
        price_per_sqm = price / size
        if price_per_sqm <= 120:
            reasons.append(f"高性价比({price_per_sqm:.0f}元/㎡)")
            score += 2
        elif price_per_sqm <= 150:
            reasons.append(f"性价比良({price_per_sqm:.0f}元/㎡)")
            score += 1

    if size > 0:
        if 40 <= size <= 60:
            reasons.append(f"面积适合两人({size}㎡)")
            score += 1
        elif 60 < size <= 80:
            reasons.append(f"面积宽敞({size}㎡)")
            score += 1
        elif size > 80:
            reasons.append(f"面积充裕({size}㎡)")

    layout = item.get("layout", "")
    if layout:
        if "2室" in layout or "2居" in layout:
            reasons.append("两居适合双人")
            score += 1
        elif "1室" in layout or "1居" in layout:
            reasons.append("一居紧凑")

    subway_count = len(item.get("matched_subway", []))
    if subway_count >= 2:
        reasons.append(f"双地铁覆盖({subway_count}站)")
        score += 1
    elif subway_count == 1:
        reasons.append("近地铁站")

    if score >= 5:
        return "重点推荐", score, reasons
    elif score >= 3:
        return "推荐", score, reasons
    else:
        return "", score, reasons


def _write_metadata_sheet(wb, search_context):
    ws = wb.add_worksheet("筛选报告")

    title_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 14, "font_color": "#1F4E79",
    })
    section_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 12, "font_color": "#1F4E79",
    })
    label_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 11, "font_color": "#2F5496",
    })
    value_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 11,
    })

    ws.set_column("A:A", 22)
    ws.set_column("B:B", 60)

    row = 0
    ws.write(row, 0, "房源筛选报告", title_fmt)
    row += 1
    ws.write(row, 0, f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", value_fmt)
    row += 2

    ws.write(row, 0, "【搜索条件】", section_fmt)
    row += 1

    conditions = search_context.get("conditions", {})
    condition_items = [
        ("地理区域", conditions.get("district", "海淀区")),
        ("数据来源", "、".join(conditions.get("sources", []))),
        ("预算范围", f"{conditions.get('budget_min', '?')}-{conditions.get('budget_max', '?')}元/月"),
        ("面积范围", f"{conditions.get('size_min', '?')}-{conditions.get('size_max', '?')}㎡"),
        ("通勤目标", "、".join(conditions.get("destinations", []))),
        ("通勤上限", f"{conditions.get('commute_max', '?')}分钟"),
        ("通勤方式", "公交/地铁(高德API) + 骑行(高德API) + 电动车(≤5km直线距离估算)"),
        ("电动车参数", f"速度{conditions.get('ebike_speed', '?')}km/h, 道路系数{conditions.get('road_factor', '?')}, 直线距离上限{conditions.get('ebike_max_km', '?')}km"),
        ("抓取页数", f"{conditions.get('pages', '?')}页/数据源"),
    ]
    for label, value in condition_items:
        ws.write(row, 0, label, label_fmt)
        ws.write(row, 1, str(value), value_fmt)
        row += 1

    row += 1
    ws.write(row, 0, "【筛选结果】", section_fmt)
    row += 1

    results = search_context.get("results", {})
    result_items = [
        ("原始抓取总数", f"{results.get('total_raw', '?')}条"),
        ("去重后", f"{results.get('total_dedup', '?')}条"),
        ("预算筛选后", f"{results.get('total_budget', '?')}条"),
        ("面积筛选后", f"{results.get('total_size', '?')}条"),
        ("区域筛选后", f"{results.get('total_district', '?')}条"),
        ("通勤筛选后", f"{results.get('total_commute', '?')}条"),
        ("最终保留", f"{results.get('total_final', '?')}条"),
    ]
    for label, value in result_items:
        ws.write(row, 0, label, label_fmt)
        ws.write(row, 1, str(value), value_fmt)
        row += 1

    row += 1
    ws.write(row, 0, "【各来源明细】", section_fmt)
    row += 1

    source_details = results.get("by_source", {})
    for source_name, detail in source_details.items():
        ws.write(row, 0, source_name, label_fmt)
        ws.write(row, 1, f"抓取{detail.get('raw', '?')}条 → 最终{detail.get('final', '?')}条", value_fmt)
        row += 1

    row += 1
    ws.write(row, 0, "【推荐标记说明】", section_fmt)
    row += 1

    legend_items = [
        ("重点推荐(蓝色行)", "综合评分≥5: 通勤极近 + 价格实惠 + 性价比优 + 面积合适 + 双地铁"),
        ("推荐(绿色行)", "综合评分≥3: 多维度综合优势"),
        ("推荐理由列", "逐条列出推荐依据: 交通距离/价格/性价比/面积/户型/地铁覆盖"),
        ("通勤时间条件格式", "绿色≤20分钟 / 黄色≤30分钟 / 红色>30分钟 / 灰色=需人工核实"),
    ]
    for label, desc in legend_items:
        ws.write(row, 0, label, label_fmt)
        ws.write(row, 1, desc, value_fmt)
        row += 1

    row += 1
    ws.write(row, 0, "【筛选逻辑说明】", section_fmt)
    row += 1

    logic_items = [
        ("排序规则", "优先匹配地铁站数量(多→少), 其次价格(低→高)"),
        ("去重规则", "同来源+标题前30字+价格 完全一致视为重复"),
        ("面积回退", "面积筛选结果<5条时自动放宽至120㎡; 仍无结果则回退到预算筛选"),
        ("通勤选择", "公交/地铁、骑行、电动车 三种方式取最短时间"),
        ("电动车限制", "直线距离>5km时不考虑全程电动车"),
        ("通勤缺失处理", "高德API失败时标记为'需人工核实'，不丢弃房源"),
        ("信源置信度", "贝壳=高, 自如=高, 安居客=中, 58同城=低"),
    ]
    for label, desc in logic_items:
        ws.write(row, 0, label, label_fmt)
        ws.write(row, 1, desc, value_fmt)
        row += 1


def _write_loss_analysis_sheet(wb, funnel_logger):
    ws = wb.add_worksheet("流失归因分析")

    title_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 14, "font_color": "#1F4E79",
    })
    header_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 11,
        "font_color": "#FFFFFF", "bg_color": "#C00000",
        "align": "center", "valign": "vcenter", "border": 1,
    })
    data_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "valign": "vcenter", "text_wrap": True, "border": 1,
    })

    ws.set_column("A:A", 12)
    ws.set_column("B:B", 40)
    ws.set_column("C:C", 12)
    ws.set_column("D:D", 16)
    ws.set_column("E:E", 40)
    ws.set_column("F:F", 30)
    ws.set_column("G:G", 35)

    row = 0
    ws.write(row, 0, "房源流失归因分析", title_fmt)
    row += 2

    ws.write(row, 0, "数据源", header_fmt)
    ws.write(row, 1, "房源标题", header_fmt)
    ws.write(row, 2, "价格", header_fmt)
    ws.write(row, 3, "流失阶段", header_fmt)
    ws.write(row, 4, "流失原因", header_fmt)
    ws.write(row, 5, "详情", header_fmt)
    ws.write(row, 6, "房源链接", header_fmt)
    row += 1

    link_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#0563C1", "underline": True,
        "valign": "vcenter", "text_wrap": True, "border": 1,
    })

    records = funnel_logger.get_records()
    for rec in records:
        ws.write(row, 0, rec["source"], data_fmt)
        ws.write(row, 1, rec["title"], data_fmt)
        ws.write(row, 2, rec["price"], data_fmt)
        ws.write(row, 3, rec["stage"], data_fmt)
        ws.write(row, 4, rec["reason"], data_fmt)
        ws.write(row, 5, rec["detail"], data_fmt)
        url = rec.get("url", "")
        if url and str(url).startswith("http"):
            display = str(url)[:50] + "..." if len(str(url)) > 50 else str(url)
            ws.write_url(row, 6, str(url), link_fmt, display)
        else:
            ws.write(row, 6, url or "无链接", data_fmt)
        row += 1

    row += 2
    summary_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 11, "font_color": "#2F5496",
    })
    value_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 11,
    })

    ws.write(row, 0, "【按数据源汇总】", summary_fmt)
    row += 1

    summary = funnel_logger.get_summary()
    for source, stages in summary.items():
        ws.write(row, 0, source, summary_fmt)
        row += 1
        for stage, count in stages.items():
            ws.write(row, 0, "", value_fmt)
            ws.write(row, 1, f"{stage}: {count}条", value_fmt)
            row += 1


def _write_data_sheet(wb, data, source_name=""):
    ws = wb.add_worksheet(source_name)

    header_fmt = wb.add_format({
        "font_name": "微软雅黑", "bold": True, "font_size": 11,
        "font_color": "#FFFFFF", "bg_color": "#2F5496",
        "align": "center", "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    data_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    link_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#0563C1", "underline": True,
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    recommend_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10, "bold": True,
        "font_color": "#1F4E79", "bg_color": "#DAEEF3",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    good_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#006100", "bg_color": "#C6EFCE",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })

    commute_green_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#006100", "bg_color": "#C6EFCE",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    commute_yellow_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#9C5700", "bg_color": "#FFEB9C",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    commute_red_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#9C0006", "bg_color": "#FFC7CE",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    commute_gray_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#666666", "bg_color": "#F2F2F2",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })

    recommend_center_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10, "bold": True,
        "font_color": "#006100", "bg_color": "#C6EFCE",
        "valign": "vcenter", "align": "center",
        "border": 1,
    })
    good_center_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#9C5700", "bg_color": "#FFEB9C",
        "valign": "vcenter", "align": "center",
        "border": 1,
    })
    center_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "valign": "vcenter", "align": "center",
        "border": 1,
    })

    recommend_link_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10, "bold": True,
        "font_color": "#0563C1", "underline": True,
        "bg_color": "#C6EFCE",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })
    good_link_fmt = wb.add_format({
        "font_name": "微软雅黑", "font_size": 10,
        "font_color": "#0563C1", "underline": True,
        "bg_color": "#FFEB9C",
        "valign": "vcenter", "text_wrap": True,
        "border": 1,
    })

    SHEET_HEADERS, COL_WIDTHS = _build_sheet_headers()

    from config import COMMUTE_DESTINATIONS
    dest_names = [d["name"] for d in COMMUTE_DESTINATIONS]
    num_base_cols = len(SHEET_HEADERS_TEMPLATE)
    commute_col_map = {}
    for i, name in enumerate(dest_names):
        commute_col_map[name] = {
            "duration_col": num_base_cols + i * 2,
            "mode_col": num_base_cols + i * 2 + 1,
        }
    source_col = num_base_cols + len(dest_names) * 2
    confidence_col = source_col + 1

    for col_idx, width in enumerate(COL_WIDTHS):
        ws.set_column(col_idx, col_idx, width)

    ws.set_row(0, 30)
    for col_idx, header in enumerate(SHEET_HEADERS):
        ws.write(0, col_idx, header, header_fmt)

    for row_idx, item in enumerate(data, 1):
        commute = item.get("commute", {})

        recommend_label, score, reasons = _compute_recommendation(item)
        reason_text = "; ".join(reasons) if reasons else ""

        row_values = [
            row_idx,
            "",
            reason_text,
            item.get("url", ""),
            item.get("title", ""),
            item.get("price", ""),
            item.get("size", ""),
            item.get("layout", ""),
            "、".join(item.get("matched_subway", [])),
        ]

        commute_infos = {}
        for name in dest_names:
            info = commute.get(name, {})
            dur = info.get("duration", "需人工核实") if info else "需人工核实"
            dist = info.get("distance", "N/A") if info else "N/A"
            mode = info.get("transit_mode", "需人工核实") if info else "需人工核实"
            dur_min = info.get("duration_min") if info else None

            if dur != "需人工核实" and dist != "N/A":
                dur_str = f"{dur} ({dist})"
            else:
                dur_str = dur

            row_values.append(dur_str)
            row_values.append(mode)
            commute_infos[name] = {"duration_min": dur_min}

        row_values.append(item.get("source", ""))
        row_values.append(item.get("source_confidence", ""))

        is_recommend = recommend_label == "重点推荐"
        is_good = recommend_label == "推荐"

        ws.set_row(row_idx, int(IMG_HEIGHT * 0.75) + 6)

        for col_idx, value in enumerate(row_values):
            if col_idx == 1:
                continue

            if is_recommend:
                fmt = recommend_fmt
            elif is_good:
                fmt = good_fmt
            else:
                fmt = data_fmt

            if col_idx == 3 and value and str(value).startswith("http"):
                link_format = recommend_link_fmt if is_recommend else (good_link_fmt if is_good else link_fmt)
                ws.write_url(row_idx, col_idx, str(value), link_format, str(value)[:50] + "..." if len(str(value)) > 50 else str(value))
                continue

            center_cols = {0, 5, 6, 7, source_col, confidence_col}
            if col_idx in center_cols:
                if is_recommend:
                    fmt = recommend_center_fmt
                elif is_good:
                    fmt = good_center_fmt
                else:
                    fmt = center_fmt

            for name in dest_names:
                cmap = commute_col_map[name]
                if col_idx == cmap["duration_col"]:
                    dur_min = commute_infos[name]["duration_min"]
                    if dur_min is None:
                        fmt = commute_gray_fmt
                    elif isinstance(dur_min, int) and dur_min <= 20:
                        fmt = commute_green_fmt
                    elif isinstance(dur_min, int) and dur_min <= 30:
                        fmt = commute_yellow_fmt
                    elif isinstance(dur_min, int):
                        fmt = commute_red_fmt
                    break

            ws.write(row_idx, col_idx, value, fmt)

        images = item.get("images", [])
        if images:
            img_path = item.get("_img_path")
            if not img_path:
                img_filename = f"{item.get('source', 'unknown')}_{item.get('id', row_idx)}.jpg"
                img_path = os.path.join(IMAGE_DIR, img_filename)

            if img_path and os.path.exists(img_path):
                try:
                    ws.insert_image(row_idx, 1, img_path, {
                        "object_position": 1,
                    })
                except Exception:
                    ws.write(row_idx, 1, "[图片加载失败]", data_fmt)
            else:
                if download_image(images[0], img_path):
                    try:
                        ws.insert_image(row_idx, 1, img_path, {
                            "object_position": 1,
                        })
                    except Exception:
                        ws.write(row_idx, 1, "[图片加载失败]", data_fmt)
                else:
                    ws.write(row_idx, 1, "[图片下载失败]", data_fmt)

    ws.autofilter(0, 0, len(data), len(SHEET_HEADERS) - 1)
    ws.freeze_panes(1, 0)


def _preload_images(data_by_source, max_workers=8):
    ensure_dirs()
    tasks = []
    for source_name, listings in data_by_source.items():
        for item in listings:
            images = item.get("images", [])
            if not images:
                continue
            img_filename = f"{item.get('source', 'unknown')}_{item.get('id', 'unknown')}.jpg"
            img_path = os.path.join(IMAGE_DIR, img_filename)
            if os.path.exists(img_path):
                item["_img_path"] = img_path
                continue
            tasks.append((images[0], img_path, item))

    if not tasks:
        log(f"  [图片] 所有图片已缓存，无需下载")
        return

    log(f"  [图片] 预下载 {len(tasks)} 张图片 (并发={max_workers})...")
    downloaded = 0
    failed = 0

    def _download_task(args):
        url, filepath = args
        return filepath, download_image(url, filepath)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_download_task, (url, fp)): item for url, fp, item in tasks}
        for future in as_completed(futures):
            item = futures[future]
            try:
                filepath, success = future.result()
                if success:
                    item["_img_path"] = filepath
                    downloaded += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

            done = downloaded + failed
            if done % 20 == 0 or done == len(tasks):
                log(f"  [图片] 进度: {done}/{len(tasks)} (成功={downloaded}, 失败={failed})")

    log(f"  [图片] 下载完成: 成功={downloaded}, 失败={failed}")


def export_to_excel(data_by_source, search_context=None, filename="精选房源.xlsx",
                    funnel_logger=None):
    ensure_dirs()

    filepath = os.path.join(OUTPUT_DIR, filename)

    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except PermissionError:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(OUTPUT_DIR, f"精选房源_{ts}.xlsx")
            log(f"  [!] 原文件被占用，保存为新文件: {filepath}")

    _preload_images(data_by_source)

    wb = xlsxwriter.Workbook(filepath)

    if search_context:
        _write_metadata_sheet(wb, search_context)

    if funnel_logger:
        _write_loss_analysis_sheet(wb, funnel_logger)

    total = sum(len(v) for v in data_by_source.values())
    log(f"  写入 Excel，共 {total} 条房源...")

    for source_name, listings in data_by_source.items():
        if not listings:
            continue
        log(f"  写入 Sheet: {source_name} ({len(listings)} 条)")
        _write_data_sheet(wb, listings, source_name)

    if not wb.sheetnames:
        ws = wb.add_worksheet("无数据")
        ws.write(0, 0, "未获取到任何房源数据")

    wb.close()
    log(f"[OK] 已生成 {filepath}")
    return filepath
