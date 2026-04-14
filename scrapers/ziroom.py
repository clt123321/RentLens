import requests
import time
import random
import re
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from utils import safe_get, random_delay, log
from image_utils import upgrade_image_url

load_dotenv()

USE_PLAYWRIGHT = os.getenv("ZIROOM_USE_PLAYWRIGHT", "0") == "1"


def _try_playwright_scrape(pages=2, price_min=None, price_max=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("  [自如] playwright 未安装，回退到 requests 模式")
        return None

    log("  [自如] 使用 Playwright 浏览器模式抓取...")
    all_listings = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            for pg in range(1, pages + 1):
                url = "https://www.ziroom.com/z/z2-d23008618/"
                if price_min or price_max:
                    price_part = ""
                    if price_min:
                        price_part += f"r{price_min}"
                    if price_max:
                        price_part += f"r{price_max}"
                    url = f"https://www.ziroom.com/z/z2-d23008618-{price_part}/"
                if pg > 1:
                    base = url.rstrip("/")
                    url = f"{base}-p{pg}/"

                log(f"  [自如-Playwright] 正在加载第 {pg} 页...")
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)
                except Exception as e:
                    log(f"  [自如-Playwright] 页面加载超时: {e}")
                    continue

                html = page.content()
                listings = _parse_ziroom_html(html, price_min or 8000, price_max or 15000)
                log(f"  [自如-Playwright] 第 {pg} 页获取到 {len(listings)} 条房源")

                all_listings.extend(listings)
                time.sleep(random.uniform(3, 6))

            browser.close()
    except Exception as e:
        log(f"  [自如-Playwright] 异常: {e}")

    return all_listings


def _parse_ziroom_html(html, price_min=8000, price_max=15000):
    soup, mapping = _decode_page_prices(html, price_min, price_max)
    items = soup.select("div.Z_list-box div.item")
    listings = []

    for item in items:
        try:
            inv_no = item.get("data-inv-no", "")
            if not inv_no:
                continue

            title_elem = item.select_one("h5.title a")
            title = title_elem.text.strip() if title_elem else ""

            link = title_elem.get("href", "") if title_elem else ""
            if link and not link.startswith("http"):
                link = "https:" + link

            img_url = _extract_image_url(item)

            price_div = item.select_one("div.price-content")
            price = _decode_price_with_mapping(price_div, mapping)

            desc_elem = item.select_one("div.desc")
            desc = desc_elem.get_text(separator=" ", strip=True) if desc_elem else ""

            size = ""
            size_match = re.search(r"([\d.]+)㎡", desc)
            if size_match:
                size = size_match.group(1)

            layout = ""
            bedroom = item.get("data-bedroom", "")
            if bedroom:
                layout = f"{bedroom}居"

            location_elem = item.select_one("div.location")
            location = location_elem.text.strip() if location_elem else ""

            subway_info = ""
            subway_match = re.search(r"距(.+?线.+?站)", location)
            if subway_match:
                subway_info = subway_match.group(1)

            district = "海淀区"
            area_name = ""

            tag_elems = item.select("div.tag span")
            tags = []
            for t in tag_elems:
                text = t.text.strip()
                if text and "签约" not in text:
                    tags.append(text)

            listings.append({
                "id": inv_no,
                "source": "自如",
                "title": title,
                "price": price,
                "url": link,
                "desc": f"{desc} {location}",
                "district": district,
                "area_name": area_name,
                "community": title.split("·")[0] if "·" in title else title,
                "size": size,
                "layout": layout,
                "tags": tags,
                "images": [img_url] if img_url != "No Image" else [],
                "image_url": img_url,
                "subway_info": subway_info,
            })
        except Exception as e:
            log(f"  [自如] 解析房源条目失败: {e}")
            continue

    return listings


def _extract_image_url(item):
    img_elem = item.select_one("img.lazy")
    img_url = ""
    if img_elem:
        img_url = img_elem.get("data-original", "")
        if not img_url:
            img_url = img_elem.get("src", "")
        if img_url and not img_url.startswith("http"):
            img_url = "https:" + img_url

    if not img_url:
        img_elem = item.select_one("img")
        if img_elem:
            img_url = img_elem.get("data-original", "")
            if not img_url:
                img_url = img_elem.get("src", "")
            if img_url and not img_url.startswith("http"):
                img_url = "https:" + img_url

    if img_url:
        img_url = upgrade_image_url(img_url, "自如")
    else:
        img_url = "No Image"

    return img_url


def _decode_price(price_div):
    if not price_div:
        return "0"
    num_spans = price_div.select("span.num")
    digits = []
    for span in num_spans:
        style = span.get("style", "")
        pos_match = re.search(r'background-position:\s*-?([\d.]+)px', style)
        if pos_match:
            pos = float(pos_match.group(1))
            digit = round(pos / 21.4)
            if 0 <= digit <= 9:
                digits.append(str(digit))
    return "".join(digits) if digits else "0"


def _extract_sprite_positions(soup):
    items = soup.select("div.Z_list-box div.item")
    position_data = []
    all_positions = set()
    first_digit_positions = set()
    item_positions = []

    for item in items:
        price_div = item.select_one("div.price-content")
        if not price_div:
            item_positions.append(None)
            continue

        positions = []
        for span in price_div.select("span.num"):
            style = span.get("style", "")
            pos_match = re.search(r'background-position:\s*-?([\d.]+)px', style)
            if pos_match:
                pos = round(float(pos_match.group(1)), 1)
                positions.append(pos)
                all_positions.add(pos)

        if positions:
            first_digit_positions.add(positions[0])
            position_data.append(positions)
            item_positions.append(positions)
        else:
            item_positions.append(None)

    return item_positions, position_data, all_positions, first_digit_positions


def _solve_sprite_mapping(position_data, first_digit_positions, price_min=8000, price_max=15000):
    all_positions = sorted(set(p for positions in position_data for p in positions))

    pos_candidates = {}
    for pos in all_positions:
        if pos in first_digit_positions:
            cands = set(range(10))
            for positions in position_data:
                if positions[0] == pos:
                    for d in range(10):
                        test_price = int(str(d) + "0" * (len(positions) - 1))
                        max_price = int(str(d) + "9" * (len(positions) - 1))
                        if max_price < price_min or test_price > price_max:
                            cands.discard(d)
            pos_candidates[pos] = sorted(cands)
        else:
            pos_candidates[pos] = list(range(10))

    positions_to_solve = sorted(all_positions)
    candidate_lists = [pos_candidates[p] for p in positions_to_solve]

    mapping = {}

    def check_partial():
        for positions in position_data:
            mapped = [mapping.get(p) for p in positions]
            if all(m is not None for m in mapped):
                price_str = ''.join(str(m) for m in mapped)
                try:
                    price = int(price_str)
                    if not (price_min <= price <= price_max):
                        return False
                except:
                    return False
            else:
                partial = ''
                for m in mapped:
                    if m is not None:
                        partial += str(m)
                    else:
                        break
                if partial:
                    remaining = len(positions) - len(partial)
                    min_price = int(partial + '0' * remaining)
                    max_price = int(partial + '9' * remaining)
                    if max_price < price_min or min_price > price_max:
                        return False
        return True

    def solve(idx):
        if idx == len(positions_to_solve):
            return True
        pos = positions_to_solve[idx]
        for digit in candidate_lists[idx]:
            mapping[pos] = digit
            if check_partial():
                if solve(idx + 1):
                    return True
            del mapping[pos]
        return False

    if solve(0):
        return dict(mapping)
    return None


def _decode_page_prices(html, price_min=8000, price_max=15000):
    soup = BeautifulSoup(html, "lxml")
    item_positions, position_data, all_positions, first_digit_positions = _extract_sprite_positions(soup)

    if not position_data:
        return soup, {}

    mapping = _solve_sprite_mapping(position_data, first_digit_positions, price_min, price_max)

    if mapping:
        log(f"  [自如] 精灵图映射已求解: {len(mapping)} 个位置")
        for pos in sorted(mapping.keys()):
            log(f"    {pos}px -> {mapping[pos]}")
    else:
        log("  [自如] 精灵图映射求解失败，使用朴素解码")

    return soup, mapping


def _decode_price_with_mapping(price_div, mapping=None):
    if not price_div:
        return "0"

    if mapping:
        positions = []
        for span in price_div.select("span.num"):
            style = span.get("style", "")
            pos_match = re.search(r'background-position:\s*-?([\d.]+)px', style)
            if pos_match:
                pos = round(float(pos_match.group(1)), 1)
                positions.append(pos)

        if positions and all(p in mapping for p in positions):
            digits = [str(mapping[p]) for p in positions]
            return "".join(digits)

    return _decode_price(price_div)


class ZiroomScraper:
    BASE_URL = "https://www.ziroom.com/z/z2-d23008618/"

    APP_UA = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 "
        "Ziroom/8.65.0 (android;13)"
    )

    WEB_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.WEB_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.ziroom.com/",
        })

        cookie = os.getenv("ZIROOM_COOKIE", "")
        if cookie:
            for part in cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    self.session.cookies.set(k.strip(), v.strip())
        else:
            log("  [!] 未设置 ZIROOM_COOKIE，自如数据可能无法获取")

    def _solve_js_challenge(self, html):
        if "__tst_status" not in html or "EO_Bot_Ssid" not in html:
            return False

        nums = re.findall(r'\b(\d{8,10})\b', html)
        if len(nums) < 4:
            return False

        bot_ssid = int(nums[0])
        tst = int(nums[1]) + int(nums[2]) + int(nums[3])
        self.session.cookies.set("__tst_status", str(tst))
        self.session.cookies.set("EO_Bot_Ssid", str(bot_ssid))
        log(f"  [自如] JS反爬盾已破解: __tst_status={tst}, EO_Bot_Ssid={bot_ssid}")
        return True

    def fetch_list_page(self, page=1, price_min=None, price_max=None):
        url = "https://www.ziroom.com/z/z2-d23008618/"
        if price_min or price_max:
            price_part = ""
            if price_min:
                price_part += f"r{price_min}"
            if price_max:
                price_part += f"r{price_max}"
            url = f"https://www.ziroom.com/z/z2-d23008618-{price_part}/"
        if page > 1:
            base = url.rstrip("/")
            url = f"{base}-p{page}/"

        resp = safe_get(self.session, url)
        if not resp:
            return None

        if "__tst_status" in resp.text and "EO_Bot_Ssid" in resp.text and len(resp.text) < 2000:
            log("  [自如] 触发JS反爬盾，正在破解...")
            if self._solve_js_challenge(resp.text):
                time.sleep(1.5)
                resp = safe_get(self.session, url)
                if not resp:
                    return None
            else:
                log("  [自如] JS反爬盾破解失败，尝试切换UA...")
                self.session.headers.update({"User-Agent": self.WEB_UA})
                resp = safe_get(self.session, url)
                if not resp:
                    return None

        if len(resp.text) < 500 or "验证" in resp.text[:500]:
            log("  [自如] 页面异常，尝试切换UA...")
            self.session.headers.update({"User-Agent": self.WEB_UA})
            resp = safe_get(self.session, url)
            if not resp:
                return None

        return resp.text

    def parse_list_page(self, html, price_min=8000, price_max=15000):
        return _parse_ziroom_html(html, price_min, price_max)

    def scrape(self, pages=2, price_min=None, price_max=None, **kwargs):
        if USE_PLAYWRIGHT:
            result = _try_playwright_scrape(pages, price_min, price_max)
            if result is not None:
                return result
            log("  [自如] Playwright 失败，回退到 requests 模式")

        all_listings = []
        for page in range(1, pages + 1):
            log(f"  [自如] 正在抓取海淀整租第 {page} 页...")
            html = self.fetch_list_page(page, price_min, price_max)
            if not html:
                continue

            listings = self.parse_list_page(html, price_min or 8000, price_max or 15000)
            log(f"  [自如] 第 {page} 页获取到 {len(listings)} 条房源")

            all_listings.extend(listings)
            random_delay(4, 7)

        return all_listings
