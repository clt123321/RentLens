import os
import base64
import requests
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import log

SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "Pro/moonshotai/Kimi-K2.5"

MOCK_MODE = os.getenv("VLM_MOCK", "1") == "1"
MAX_WORKERS = int(os.getenv("VLM_MAX_WORKERS", "5"))
MAX_RETRIES = 3

SYSTEM_PROMPT = (
    "你是一位品味极其刁钻、见多识广的顶级豪宅设计师。"
    "你的客户是一对预算有限的年轻职场情侣，希望租到品质不错的房子。"
    "请通过房源首图，为这套房子打分并给出你的『毒舌』点评。\n"
    "**打分规则**：\n"
    "- 0-4分（直接开喷）：如果出现猪肝红地板、老旧发黄空调、城中村风格、廉价拼装家具。"
    "点评示例：『这种散发着上世纪90年代老干部气息的红木家具，是打算让你们在下班后提前体验退休生活吗？直接抬走。』\n"
    "- 5-7分（中规中矩）：精装修但缺乏特色，或者是毫无灵魂的中介风/自如流水线风。\n"
    "- 8-10分（极力推荐）：有大落地窗、现代极简/原木风、高品质卫浴、全屋新风地暖。"
    "点评示例：『大落地窗配上极简的原木风，加上那套西门子洗烘套装，总算保住了你们身为都市精英的最后尊严，值得周末去实地看一眼。』"
)

USER_PROMPT_TEMPLATE = (
    "房源标题: {title}\n"
    "租金: {price}元/月\n\n"
    "请严格按以下JSON格式回复，不要添加任何其他文字：\n"
    '{{"ai_score": 8.5, "roast_comment": "你的毒舌点评", "style": "装修风格", "lighting": "采光", "furniture": "家具新旧", "summary": "一句话总结"}}'
)

_eval_lock = threading.Lock()
_eval_counter = {"done": 0, "total": 0}


def _encode_image_url(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        b64 = base64.b64encode(resp.content).decode("utf-8")
        return f"data:{content_type};base64,{b64}"
    except Exception as e:
        log(f"  [VLM] 图片下载失败: {url[:60]}... -> {e}")
        return None


def _call_api_with_retry(payload, headers, max_retries=MAX_RETRIES):
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(SILICONFLOW_API_URL, json=payload, headers=headers, timeout=90)

            if resp.status_code == 429:
                wait = min(2 ** attempt + 1, 30)
                log(f"  [VLM] 429限流，退避{wait}s (尝试{attempt}/{max_retries})")
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp

            if resp.status_code >= 500:
                wait = min(2 ** attempt, 20)
                log(f"  [VLM] 服务端错误{resp.status_code}，退避{wait}s")
                time.sleep(wait)
                continue

            return resp

        except requests.exceptions.Timeout:
            wait = min(2 ** attempt, 20)
            log(f"  [VLM] 超时，退避{wait}s (尝试{attempt}/{max_retries})")
            time.sleep(wait)
        except requests.exceptions.ConnectionError:
            wait = min(2 ** attempt + 2, 30)
            log(f"  [VLM] 连接错误，退避{wait}s (尝试{attempt}/{max_retries})")
            time.sleep(wait)
        except Exception as e:
            log(f"  [VLM] 异常: {e} (尝试{attempt}/{max_retries})")
            time.sleep(2)

    return None


def evaluate_image_vibe(image_url, title="", price=""):
    if not image_url or image_url == "No Image":
        return {"ai_score": 0.0, "vibe_desc": "无图片", "roast_comment": ""}

    if MOCK_MODE:
        return {
            "ai_score": 0.0,
            "vibe_desc": "Mock模式-待AI接入",
            "roast_comment": "Mock模式-等待真实AI接入",
            "raw_response": "",
        }

    api_key = os.getenv("SILICONFLOW_API_KEY", "")
    if not api_key:
        return {"ai_score": 0.0, "vibe_desc": "未配置API Key", "roast_comment": "", "raw_response": ""}

    b64_image = _encode_image_url(image_url)
    if not b64_image:
        return {"ai_score": 0.0, "vibe_desc": "图片下载失败", "roast_comment": "", "raw_response": ""}

    user_prompt = USER_PROMPT_TEMPLATE.format(title=title, price=price)

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": b64_image}},
                    {"type": "text", "text": user_prompt},
                ],
            }
        ],
        "max_tokens": 400,
        "temperature": 0.4,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start = time.time()
    resp = _call_api_with_retry(payload, headers)
    elapsed = time.time() - start

    if resp is None:
        log(f"  [VLM] {title[:20]}... -> 全部重试失败 ({elapsed:.1f}s)")
        return {"ai_score": 0.0, "vibe_desc": "重试耗尽", "roast_comment": "", "raw_response": ""}

    if resp.status_code != 200:
        log(f"  [VLM] {title[:20]}... -> API错误{resp.status_code} ({elapsed:.1f}s)")
        return {"ai_score": 0.0, "vibe_desc": f"API错误({resp.status_code})", "roast_comment": "", "raw_response": resp.text[:200]}

    try:
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

        json_start = content.index("{")
        json_end = content.rindex("}") + 1
        result = json.loads(content[json_start:json_end])

        score = float(result.get("ai_score", result.get("score", 0)))
        roast = result.get("roast_comment", "")
        style = result.get("style", "")
        lighting = result.get("lighting", "")
        furniture = result.get("furniture", "")
        summary = result.get("summary", "")

        vibe_desc = f"{style}|{lighting}|{furniture}|{summary}"

        log(f"  [VLM] {title[:20]}... -> {score}分 ({elapsed:.1f}s)")

        return {
            "ai_score": score,
            "vibe_desc": vibe_desc,
            "roast_comment": roast,
            "style": style,
            "lighting": lighting,
            "furniture": furniture,
            "summary": summary,
            "raw_response": content,
        }
    except (ValueError, json.JSONDecodeError) as e:
        log(f"  [VLM] {title[:20]}... -> JSON解析失败 ({elapsed:.1f}s)")
        return {"ai_score": 0.0, "vibe_desc": "解析失败", "roast_comment": "", "raw_response": content[:200] if 'content' in locals() else ""}
    except Exception as e:
        log(f"  [VLM] {title[:20]}... -> 异常: {e} ({elapsed:.1f}s)")
        return {"ai_score": 0.0, "vibe_desc": f"异常:{e}", "roast_comment": "", "raw_response": ""}


def _evaluate_single(item):
    image_url = item.get("image_url", "")
    if not image_url:
        image_url = item.get("images", [""])[0] if item.get("images") else ""

    if not image_url or image_url == "No Image":
        result = {"ai_score": 0.0, "vibe_desc": "无图片", "roast_comment": ""}
    else:
        result = evaluate_image_vibe(
            image_url,
            title=item.get("title", ""),
            price=item.get("price", ""),
        )

    item["ai_score"] = result.get("ai_score", 0.0)
    item["vibe_desc"] = result.get("vibe_desc", "")
    item["roast_comment"] = result.get("roast_comment", "")

    with _eval_lock:
        _eval_counter["done"] += 1
        done = _eval_counter["done"]
        total = _eval_counter["total"]
        if done % 5 == 0 or done == total:
            log(f"  [VLM] 进度: {done}/{total}")

    return item


def batch_evaluate(listings_by_source, max_items=0):
    log(f"--- VLM 视觉打分 (模型: {MODEL_NAME}, Mock: {MOCK_MODE}, 并发: {MAX_WORKERS}) ---")

    all_items = []
    for source_name, listings in listings_by_source.items():
        for item in listings:
            if item.get("ai_score") is not None and item.get("ai_score", 0) > 0:
                continue
            all_items.append(item)

    if max_items > 0:
        all_items = all_items[:max_items]

    total = len(all_items)
    with _eval_lock:
        _eval_counter["done"] = 0
        _eval_counter["total"] = total

    log(f"  [VLM] 待评估: {total} 条房源")

    if MOCK_MODE:
        for item in all_items:
            item["ai_score"] = 0.0
            item["vibe_desc"] = "Mock模式-待AI接入"
            item["roast_comment"] = "Mock模式-等待真实AI接入"
        log(f"  [VLM] Mock模式，跳过评估 ({total}条)")
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_evaluate_single, item): item for item in all_items}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log(f"  [VLM] 线程异常: {e}")

    log(f"  [VLM] 评估完成: {_eval_counter['done']}/{total} 条房源")
