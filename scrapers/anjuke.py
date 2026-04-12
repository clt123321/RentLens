import requests
import time
import random
import re
from bs4 import BeautifulSoup
from utils import safe_get, random_delay, log
from image_utils import upgrade_image_url


class AnjukeScraper:
    BASE_URL = "https://bj.zu.anjuke.com/fangyuan/"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://bj.zu.anjuke.com/",
        })

    def fetch_list_page(self, page=1, district="haidian", price_min=None, price_max=None):
        url = f"{self.BASE_URL}{district}/p{page}/"
        params = {}
        if price_min:
            params["from_price"] = price_min
        if price_max:
            params["to_price"] = price_max
        resp = safe_get(self.session, url, params=params)
        if not resp:
            return None
        return resp.text

    def _extract_image_url(self, item):
        img_elem = item.select_one("img.thumbnail")
        img_url = ""
        if img_elem:
            img_url = img_elem.get("lazy_src", "")
            if not img_url:
                img_url = img_elem.get("src", "")
            if not img_url:
                img_url = img_elem.get("data-original", "")

        if not img_url:
            img_elem = item.select_one("img")
            if img_elem:
                img_url = img_elem.get("lazy_src", "")
                if not img_url:
                    img_url = img_elem.get("src", "")
                if not img_url:
                    img_url = img_elem.get("data-original", "")

        if img_url:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            img_url = upgrade_image_url(img_url, "安居客")
        else:
            img_url = "No Image"

        return img_url

    def parse_list_page(self, html):
        soup = BeautifulSoup(html, "lxml")
        items = soup.select("div.zu-itemmod")
        listings = []

        for item in items:
            try:
                title_elem = item.select_one("div.zu-info h3 a")
                if not title_elem:
                    continue

                title_b = title_elem.select_one("b.strongbox")
                title = title_b.text.strip() if title_b else title_elem.text.strip()

                link = title_elem.get("href", "")
                if not link:
                    continue

                house_id = ""
                match = re.search(r"fangyuan/(\d+)", link)
                if match:
                    house_id = match.group(1)

                price_elem = item.select_one("div.zu-side strong.price")
                price = price_elem.text.strip() if price_elem else "0"

                img_url = self._extract_image_url(item)

                community_elem = item.select_one("div.zu-info address a")
                community = community_elem.text.strip() if community_elem else ""

                addr_elem = item.select_one("div.zu-info address")
                district = ""
                area_name = ""
                addr_text = ""
                if addr_elem:
                    addr_text = addr_elem.get_text(separator=" ", strip=True)
                    addr_parts = [p.strip() for p in addr_text.split("-")]
                    district_keywords = ["海淀", "朝阳", "丰台", "东城", "西城",
                                         "石景山", "通州", "大兴", "昌平", "顺义",
                                         "房山", "门头沟", "密云", "怀柔", "延庆", "平谷"]
                    for part in addr_parts:
                        if not district:
                            for kw in district_keywords:
                                if kw in part:
                                    district = kw + ("区" if not kw.endswith("区") else "")
                                    break
                    if len(addr_parts) >= 2:
                        area_name = addr_parts[1]

                detail_elem = item.select_one("p.details-item.tag")
                layout = ""
                size = ""
                if detail_elem:
                    detail_text = detail_elem.get_text(separator="", strip=True)
                    detail_text = re.sub(r"\s+", "", detail_text)
                    layout_match = re.search(r"(\d+)室(\d+)厅", detail_text)
                    if layout_match:
                        layout = f"{layout_match.group(1)}室{layout_match.group(2)}厅"
                    size_match = re.search(r"([\d.]+)平米", detail_text)
                    if size_match:
                        size = size_match.group(1)

                tag_elems = item.select("p.details-item.bot-tag span")
                tags = [t.text.strip() for t in tag_elems if t.text.strip()]

                listings.append({
                    "id": house_id,
                    "source": "安居客",
                    "title": title,
                    "price": price,
                    "url": link.split("?")[0] if link else "",
                    "desc": f"{district} {area_name} {community} {size}㎡ {layout} {price}元/月",
                    "district": district,
                    "area_name": area_name,
                    "community": community,
                    "size": size,
                    "layout": layout,
                    "tags": tags,
                    "images": [img_url] if img_url != "No Image" else [],
                    "image_url": img_url,
                })
            except Exception as e:
                log(f"  [安居客] 解析房源条目失败: {e}")
                continue

        return listings

    def scrape(self, pages=3, district="haidian", price_min=None, price_max=None):
        all_listings = []
        for page in range(1, pages + 1):
            log(f"  [安居客] 正在抓取 {district} 第 {page} 页...")
            html = self.fetch_list_page(page, district, price_min, price_max)
            if not html:
                continue

            listings = self.parse_list_page(html)
            log(f"  [安居客] 第 {page} 页获取到 {len(listings)} 条房源")

            all_listings.extend(listings)
            random_delay(3, 6)

        return all_listings
