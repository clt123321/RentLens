import json
import os
import glob
import time
from utils import log

CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")

STAGES = ["scrape", "filter", "commute", "vlm"]


def _ensure_dir():
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)


def save_checkpoint(stage, data_by_source, extra=None):
    _ensure_dir()
    ts = int(time.time())
    filepath = os.path.join(CHECKPOINT_DIR, f"checkpoint_{stage}_{ts}.json")

    serializable = {}
    for source_name, listings in data_by_source.items():
        serializable[source_name] = []
        for item in listings:
            clean = {}
            for k, v in item.items():
                if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    clean[k] = v
                elif isinstance(v, set):
                    clean[k] = list(v)
                else:
                    clean[k] = str(v)
            serializable[source_name].append(clean)

    payload = {
        "stage": stage,
        "timestamp": ts,
        "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": list(serializable.keys()),
        "counts": {k: len(v) for k, v in serializable.items()},
        "data": serializable,
    }
    if extra:
        payload["extra"] = extra

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"  [Checkpoint] 已保存 {stage} 阶段 ({sum(payload['counts'].values())}条) -> {filepath}")
    except Exception as e:
        log(f"  [Checkpoint] 保存失败: {e}")

    _cleanup_old(stage, keep=3)
    return filepath


def load_latest_checkpoint(stage=None):
    _ensure_dir()

    if stage:
        pattern = os.path.join(CHECKPOINT_DIR, f"checkpoint_{stage}_*.json")
    else:
        pattern = os.path.join(CHECKPOINT_DIR, "checkpoint_*.json")

    files = sorted(glob.glob(pattern))
    if not files:
        return None

    latest = files[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            payload = json.load(f)
        log(f"  [Checkpoint] 加载 {latest}")
        log(f"  [Checkpoint] 阶段={payload['stage']}, 时间={payload['time_str']}, "
            f"数量={payload['counts']}")
        return payload
    except Exception as e:
        log(f"  [Checkpoint] 加载失败: {e}")
        return None


def find_completed_stages():
    _ensure_dir()
    completed = set()
    for stage in STAGES:
        pattern = os.path.join(CHECKPOINT_DIR, f"checkpoint_{stage}_*.json")
        if glob.glob(pattern):
            completed.add(stage)
    return completed


def get_resume_stage():
    completed = find_completed_stages()
    for stage in STAGES:
        if stage not in completed:
            return stage
    return "done"


def _cleanup_old(stage, keep=3):
    pattern = os.path.join(CHECKPOINT_DIR, f"checkpoint_{stage}_*.json")
    files = sorted(glob.glob(pattern))
    if len(files) > keep:
        for f in files[:-keep]:
            try:
                os.remove(f)
            except Exception:
                pass
