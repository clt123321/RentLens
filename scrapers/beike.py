import requests
import time
import random
import re
import os
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from utils import safe_get, random_delay
from image_utils import upgrade_image_url

load_dotenv()


class BeikeScraper:
    BASE_URL = "https://bj.zu.ke.com/zufang/"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "Referer": "https://bj.ke.com/",
        })

        cookie = os.getenv("BEIKE_COOKIE", "")
        if cookie:
            for part in cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    self.session.cookies.set(k.strip(), v.strip())
        else:
            print("  [!] 未设置 BEIKE_COOKIE，贝壳数据可能无法获取")

    def fetch_list_page(self, page=1, district="haidian", price_min=None, price_max=None):
        url = f"{self.BASE_URL}{district}/pg{page}/"
        if price_min or price_max:
            price_part = ""
            if price_min:
                price_part += f"brp{price_min}"
            if price_max:
                price_part += f"erp{price_max}"
            url = f"{self.BASE_URL}{district}/{price_part}pg{page}/"
        resp = safe_get(self.session, url)
        if not resp:
            return None
        if "CAPTCHA" in resp.text[:500] or "captcha" in resp.text[:500].lower():
            print(f"  [!] 贝壳第{page}页触发验证码(CAPTCHA)，Cookie 可能已过期或被风控")
            return None
        if "登录" in resp.text[:500]:
            print(f"  [!] 贝壳第{page}页需要登录，Cookie 可能已过期")
            return None
        return resp.text

    def _extract_image_url(self, item):
        img_elem = item.select_one("img.lazyload")
        img_url = ""
        if img_elem:
            img_url = img_elem.get("data-src", "")
            if not img_url:
                img_url = img_elem.get("src", "")

        if not img_url:
            img_elem = item.select_one("img")
            if img_elem:
                img_url = img_elem.get("data-src", "")
                if not img_url:
                    img_url = img_elem.get("src", "")

        if img_url:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            img_url = upgrade_image_url(img_url, "贝壳")
        else:
            img_url = "No Image"

        return img_url

    def parse_list_page(self, html):
        soup = BeautifulSoup(html, "lxml")
        items = soup.select("div.content__list--item")
        listings = []

        for item in items:
            try:
                title_elem = item.select_one("p.content__list--item--title a")
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                link = title_elem.get("href", "")
                if not link:
                    continue

                house_code = ""
                match = re.search(r"/zufang/(\w+)\.html", link)
                if match:
                    house_code = match.group(1)

                price_elem = item.select_one("span.content__list--item-price em")
                price = price_elem.text.strip() if price_elem else "0"

                img_url = self._extract_image_url(item)

                des_links = item.select("p.content__list--item--des a")
                district = ""
                area_name = ""
                community = ""

                if des_links:
                    for dl in des_links:
                        dl_text = dl.text.strip()
                        if not district and ("海淀" in dl_text or "朝阳" in dl_text or "西城" in dl_text or "东城" in dl_text):
                            district = dl_text + ("区" if not dl_text.endswith("区") else "")
                        elif not area_name:
                            area_name = dl_text
                        elif not community:
                            community = dl_text

                if not district:
                    district = "海淀区"

                des_text = item.select_one("p.content__list--item--des")
                desc_parts = []
                if des_text:
                    for child in des_text.children:
                        text = child.string
                        if text and text.strip() and text.strip() not in ("/",):
                            desc_parts.append(text.strip())
                desc = " ".join(desc_parts)

                size = ""
                size_match = re.search(r"(\d+\.?\d*)㎡", desc)
                if size_match:
                    size = size_match.group(1)

                layout = ""
                layout_match = re.search(r"(\d+室\d+厅)", desc)
                if layout_match:
                    layout = layout_match.group(1)

                tag_elems = item.select("p.content__list--item--bottom i")
                tags = [t.text.strip() for t in tag_elems if t.text.strip()]

                geo_coords = ""
                coord_elem = item.select_one("[data-coordinate]")
                if coord_elem:
                    coord_val = coord_elem.get("data-coordinate", "")
                    if coord_val:
                        geo_coords = coord_val

                listings.append({
                    "id": house_code,
                    "source": "贝壳",
                    "title": title,
                    "price": price,
                    "url": f"https://bj.zu.ke.com{link}",
                    "desc": f"{district} {area_name} {community} {size}㎡ {layout} {price}元/月",
                    "district": district,
                    "area_name": area_name,
                    "community": community,
                    "size": size,
                    "layout": layout,
                    "tags": tags,
                    "images": [img_url] if img_url != "No Image" else [],
                    "image_url": img_url,
                    "geo_coords": geo_coords,
                })
            except Exception:
                continue

        return listings

    def scrape(self, pages=3, district="haidian", price_min=None, price_max=None):
        all_listings = []
        for page in range(1, pages + 1):
            print(f"  [贝壳] 正在抓取 {district} 第 {page} 页...")
            html = self.fetch_list_page(page, district, price_min, price_max)
            if not html:
                continue

            listings = self.parse_list_page(html)
            print(f"  [贝壳] 第 {page} 页获取到 {len(listings)} 条房源")

            all_listings.extend(listings)
            random_delay(4, 7)

        return all_listings
