import requests
import re
import time
import random
from bs4 import BeautifulSoup
from utils import safe_get, random_delay, log
from image_utils import upgrade_image_url


class WuBaScraper:
    BASE_URL = "https://m.58.com/bj/zufang/"
    HAIDIAN_URL = "https://m.58.com/bj/zufang/haidian/"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                          "Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://m.58.com/bj/zufang/",
        })

    def fetch_list_page(self, page=1, district="haidian"):
        if district:
            url = f"{self.HAIDIAN_URL}pn{page}/"
        else:
            url = f"{self.BASE_URL}pn{page}/"
        resp = safe_get(self.session, url)
        if not resp:
            return None
        return resp.text

    def parse_addr(self, addr):
        district = ""
        area_name = ""
        community = ""
        parts = [p.strip() for p in addr.split() if p.strip()]
        if len(parts) >= 1:
            area_name = parts[0]
        if len(parts) >= 2:
            community = parts[1].lstrip("-")
        if area_name:
            district = "海淀区"
        return district, area_name, community

    def _extract_image_url(self, item):
        img_elem = item.select_one("div.list-item-img")
        img_url = ""
        if img_elem:
            img_url = img_elem.get("bg-src", "")
            if not img_url:
                style = img_elem.get("style", "")
                if "url(" in style:
                    img_url = style.split("url(")[1].split(")")[0].strip("'\"")

        if not img_url:
            img_elem = item.select_one("img")
            if img_elem:
                img_url = img_elem.get("data-src", "")
                if not img_url:
                    img_url = img_elem.get("src", "")
                if not img_url:
                    img_url = img_elem.get("lazy_src", "")

        if img_url:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            img_url = upgrade_image_url(img_url, "58同城")
        else:
            img_url = "No Image"

        return img_url

    def parse_list_page(self, html):
        soup = BeautifulSoup(html, "lxml")
        items = soup.select("a.list-item")
        listings = []

        for item in items:
            try:
                title_elem = item.select_one("li.list-item-info-title")
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                link = item.get("href", "")
                if not link:
                    continue

                price_elem = item.select_one("li.list-item-info-price i")
                price = price_elem.text.strip() if price_elem else "0"

                addr_elem = item.select_one("span.list-item-info-addr-text")
                addr = addr_elem.text.strip().replace("\n", " ").replace("  ", " ") if addr_elem else ""

                district, area_name, community = self.parse_addr(addr)

                layout_match = re.search(r'(\d+室\d+厅)', addr)
                layout = layout_match.group(1) if layout_match else ""

                img_url = self._extract_image_url(item)

                house_id = ""
                if "zufang/" in link and ".shtml" in link:
                    house_id = link.split("zufang/")[1].split(".")[0].replace("x", "")

                listings.append({
                    "id": house_id,
                    "source": "58同城",
                    "title": title,
                    "price": price,
                    "url": link,
                    "desc": f"{addr} {price}元/月",
                    "district": district,
                    "area_name": area_name,
                    "community": community,
                    "size": "",
                    "layout": layout,
                    "tags": [],
                    "images": [img_url] if img_url != "No Image" else [],
                    "image_url": img_url,
                })
            except Exception:
                continue

        return listings

    def scrape(self, pages=2, district="haidian", **kwargs):
        all_listings = []
        for page in range(1, pages + 1):
            label = f"海淀" if district else "全部区域"
            print(f"  [58同城] 正在抓取{label}第 {page} 页...")
            html = self.fetch_list_page(page, district=district)
            if not html:
                continue

            listings = self.parse_list_page(html)
            print(f"  [58同城] 第 {page} 页获取到 {len(listings)} 条房源")

            all_listings.extend(listings)
            random_delay(2, 5)

        return all_listings
