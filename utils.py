import requests
import time
import random
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 3
MAX_DELAY = 15


def log(msg, level="info"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def safe_get(session, url, max_retries=MAX_RETRIES, **kwargs):
    kwargs.setdefault("timeout", 15)

    for attempt in range(1, max_retries + 1):
        try:
            log(f"  >> 请求: {url[:80]}... (尝试 {attempt}/{max_retries})")
            start = time.time()
            resp = session.get(url, **kwargs)
            elapsed = time.time() - start
            log(f"  << 响应: {resp.status_code} ({elapsed:.1f}s)")

            if resp.status_code == 429:
                wait = random.uniform(10, 20)
                log(f"  [429] 请求频率限制，等待 {wait:.1f}s...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        except requests.exceptions.ConnectionError as e:
            wait = BASE_DELAY * attempt + random.uniform(1, 3)
            log(f"  [连接错误] 第{attempt}次重试，等待 {wait:.1f}s: {e}")
            time.sleep(wait)

        except requests.exceptions.Timeout as e:
            wait = BASE_DELAY * attempt + random.uniform(1, 3)
            log(f"  [超时] 第{attempt}次重试，等待 {wait:.1f}s: {e}")
            time.sleep(wait)

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 401):
                log(f"  [HTTP {e.response.status_code}] 访问被拒绝，Cookie 可能已过期")
                return None
            wait = BASE_DELAY * attempt + random.uniform(1, 3)
            log(f"  [HTTP错误] 第{attempt}次重试，等待 {wait:.1f}s: {e}")
            time.sleep(wait)

        except Exception as e:
            wait = BASE_DELAY * attempt + random.uniform(1, 3)
            log(f"  [未知错误] 第{attempt}次重试，等待 {wait:.1f}s: {e}")
            time.sleep(wait)

    log(f"  [失败] {url[:80]} 重试{max_retries}次后仍失败")
    return None


def random_delay(min_sec=3, max_sec=7):
    delay = random.uniform(min_sec, max_sec)
    log(f"  ... 等待 {delay:.1f}s ...")
    time.sleep(delay)
    return delay
