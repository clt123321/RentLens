import requests
import time
import random
import os
import math
from dotenv import load_dotenv
from utils import log
from config import CITY_NAME, CITY_NAME_FULL

load_dotenv()


class CommuteCalculator:
    GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
    TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
    BICYCLING_URL = "https://restapi.amap.com/v4/direction/bicycling"

    EBIKE_MAX_STRAIGHT_KM = 5.0
    EBIKE_SPEED_KMH = 22
    ROAD_FACTOR = 1.3

    def __init__(self):
        self.api_key = os.getenv("AMAP_API_KEY", "")
        if not self.api_key:
            log("  [!] 未设置 AMAP_API_KEY，通勤距离计算将不可用")
            log("  [!] 请在高德开放平台创建 Web服务 类型的Key，填入 .env 的 AMAP_API_KEY")
        self._coords_cache = {}
        self._route_cache = {}

    def get_coordinates(self, address):
        if address in self._coords_cache:
            return self._coords_cache[address]

        if not self.api_key:
            return None

        try:
            resp = requests.get(self.GEO_URL, params={
                "key": self.api_key,
                "address": address,
                "city": CITY_NAME,
            }, timeout=10)
            data = resp.json()

            if data.get("info") == "USERKEY_PLAT_NOMATCH":
                log("  [!] 高德API Key类型不匹配！请使用 Web服务 类型的Key，而非 JS API 类型的Key")
                return None

            if data.get("status") == "1" and data.get("geocodes"):
                location = data["geocodes"][0]["location"]
                self._coords_cache[address] = location
                time.sleep(random.uniform(0.1, 0.3))
                return location
            else:
                log(f"  [!] 地理编码无结果 ({address}): {data.get('info', 'unknown')}")
        except requests.exceptions.RequestException as e:
            log(f"  [!] 地理编码请求失败 ({address}): {e}")
        except (ValueError, KeyError, IndexError) as e:
            log(f"  [!] 地理编码解析失败 ({address}): {e}")
        return None

    def _haversine_km(self, coord1, coord2):
        lng1, lat1 = [float(x) for x in coord1.split(",")]
        lng2, lat2 = [float(x) for x in coord2.split(",")]
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
        c = 2 * math.asin(math.sqrt(a))
        return R * c

    def _parse_transit_mode(self, segments):
        modes = set()
        for seg in segments:
            bus = seg.get("bus", {})
            walking = seg.get("walking", {})

            if bus and bus.get("buslines"):
                busline = bus["buslines"][0] if bus["buslines"] else {}
                bus_name = busline.get("name", "")
                if "地铁" in bus_name or "号线" in bus_name:
                    modes.add("地铁")
                elif "公交" in bus_name or "路" in bus_name:
                    modes.add("公交")
                else:
                    modes.add("公交")

            if walking and walking.get("distance", "0") != "0":
                walk_dist = int(walking.get("distance", "0"))
                if walk_dist > 100:
                    modes.add("步行")

        if not modes:
            modes.add("公交")

        return "、".join(sorted(modes, key=lambda x: ["地铁", "公交", "步行"].index(x) if x in ["地铁", "公交", "步行"] else 99))

    def get_transit_commute(self, origin_coords, dest_coords):
        if not self.api_key or not origin_coords or not dest_coords:
            return None

        cache_key = f"transit_{origin_coords}-{dest_coords}"
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        try:
            params = {
                "key": self.api_key,
                "origin": origin_coords,
                "destination": dest_coords,
                "city": CITY_NAME,
                "strategy": 0,
                "time": "0900",
            }
            resp = requests.get(self.TRANSIT_URL, params=params, timeout=10)
            data = resp.json()

            if data.get("status") == "1" and data.get("route"):
                transits = data["route"].get("transits", [])
                if transits:
                    best = transits[0]
                    duration_sec = int(best.get("duration", 0))
                    walking_distance = int(best.get("walking_distance", 0))
                    distance = int(data["route"].get("distance", 0))
                    segments = best.get("segments", [])

                    cost_str = best.get("cost", "0")
                    try:
                        cost_yuan = float(cost_str)
                    except (ValueError, TypeError):
                        cost_yuan = 0.0

                    transit_mode = self._parse_transit_mode(segments)

                    result = {
                        "distance": f"{distance / 1000:.1f}km",
                        "distance_m": distance,
                        "duration": f"{duration_sec // 60}分钟",
                        "duration_min": duration_sec // 60,
                        "walking_distance": f"{walking_distance}m",
                        "walking_distance_m": walking_distance,
                        "cost": f"{cost_yuan:.0f}元",
                        "segments": len(segments),
                        "transit_mode": transit_mode,
                    }
                    self._route_cache[cache_key] = result
                    time.sleep(random.uniform(0.2, 0.5))
                    return result

            return None
        except requests.exceptions.RequestException as e:
            log(f"  [!] 公交路径规划请求失败: {e}")
        except (ValueError, KeyError, IndexError) as e:
            log(f"  [!] 公交路径规划解析失败: {e}")
        return None

    def get_bicycling_commute(self, origin_coords, dest_coords):
        if not self.api_key or not origin_coords or not dest_coords:
            return None

        cache_key = f"bike_{origin_coords}-{dest_coords}"
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        try:
            params = {
                "key": self.api_key,
                "origin": origin_coords,
                "destination": dest_coords,
            }
            resp = requests.get(self.BICYCLING_URL, params=params, timeout=10)
            data = resp.json()

            if data.get("status") == "1" and data.get("data"):
                paths = data["data"].get("paths", [])
                if paths:
                    best = paths[0]
                    duration_sec = int(best.get("duration", 0))
                    distance = int(best.get("distance", 0))

                    result = {
                        "distance": f"{distance / 1000:.1f}km",
                        "distance_m": distance,
                        "duration": f"{duration_sec // 60}分钟",
                        "duration_min": duration_sec // 60,
                        "walking_distance": "0m",
                        "walking_distance_m": 0,
                        "cost": "0元",
                        "segments": 0,
                        "transit_mode": "骑行",
                    }
                    self._route_cache[cache_key] = result
                    time.sleep(random.uniform(0.2, 0.5))
                    return result

            return None
        except requests.exceptions.RequestException as e:
            log(f"  [!] 骑行路径规划请求失败: {e}")
        except (ValueError, KeyError, IndexError) as e:
            log(f"  [!] 骑行路径规划解析失败: {e}")
        return None

    def estimate_ebike_commute(self, origin_coords, dest_coords):
        if not origin_coords or not dest_coords:
            return None

        cache_key = f"ebike_{origin_coords}-{dest_coords}"
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        try:
            straight_km = self._haversine_km(origin_coords, dest_coords)
            road_km = straight_km * self.ROAD_FACTOR
            duration_min = int(round(road_km / self.EBIKE_SPEED_KMH * 60))

            result = {
                "distance": f"{road_km:.1f}km",
                "distance_m": int(road_km * 1000),
                "duration": f"{duration_min}分钟",
                "duration_min": duration_min,
                "walking_distance": "0m",
                "walking_distance_m": 0,
                "cost": "0元",
                "segments": 0,
                "transit_mode": "电动车",
            }
            self._route_cache[cache_key] = result
            return result
        except Exception:
            return None

    NEED_VERIFY = {"distance": "需人工核实", "duration": "需人工核实", "segments": 0, "transit_mode": "需人工核实", "duration_min": None}

    def calc_transit_commute(self, origin_address, dest_address):
        origin = self.get_coordinates(origin_address)
        dest = self.get_coordinates(dest_address)

        if not origin or not dest:
            return dict(self.NEED_VERIFY)

        transit_result = self.get_transit_commute(origin, dest)
        bike_result = self.get_bicycling_commute(origin, dest)

        straight_km = self._haversine_km(origin, dest)
        ebike_result = None
        if straight_km <= self.EBIKE_MAX_STRAIGHT_KM:
            ebike_result = self.estimate_ebike_commute(origin, dest)

        candidates = []
        if transit_result:
            candidates.append(transit_result)
        if bike_result:
            candidates.append(bike_result)
        if ebike_result:
            candidates.append(ebike_result)

        if not candidates:
            return dict(self.NEED_VERIFY)

        valid = [c for c in candidates if c.get("duration_min") is not None and c.get("duration_min", 0) > 0]
        if not valid:
            return dict(self.NEED_VERIFY)

        best = min(valid, key=lambda x: x.get("duration_min", 9999))
        return best

    def calc_commute_for_listing(self, listing, destinations):
        community = listing.get("community", "")
        area_name = listing.get("area_name", "")
        district = listing.get("district", "")
        title = listing.get("title", "")

        non_address_patterns = ["室", "厅", "卫", "居", "房间", "户型"]
        if area_name and any(p in area_name for p in non_address_patterns):
            area_name = ""

        address_parts = [p for p in [district, area_name, community] if p]
        origin_address = CITY_NAME_FULL + "".join(address_parts)

        if not community and not area_name:
            if title:
                origin_address = f"{CITY_NAME_FULL}{district}{title[:20]}"
            else:
                for dest in destinations:
                    listing.setdefault("commute", {})[dest["name"]] = {
                        "distance": "N/A",
                        "duration": "N/A",
                        "segments": 0,
                        "transit_mode": "N/A",
                        "duration_min": None,
                    }
                return {dest["name"]: listing["commute"][dest["name"]] for dest in destinations}

        results = {}
        for dest in destinations:
            dest_name = dest["name"]
            dest_address = dest["address"]
            result = self.calc_transit_commute(origin_address, dest_address)

            if result.get("duration_min") == 0 and result.get("transit_mode") == "电动车":
                result = {
                    "distance": "N/A",
                    "duration": "N/A",
                    "segments": 0,
                    "transit_mode": "N/A",
                    "duration_min": None,
                }

            results[dest_name] = result

        return results
