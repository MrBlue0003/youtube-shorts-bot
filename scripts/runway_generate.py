"""
runway_generate.py — Generate video clips via Runway ML Gen-3 Alpha Turbo API.

Reads a random prompt from animal_prompts.json, submits generation tasks,
polls until complete, downloads clips into videos/, and tracks credit usage.
"""

import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add parent to path so config is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CREDITS_FILE = config.LOGS_DIR / "credits.json"
PROMPTS_FILE = config.PROMPTS_DIR / "animal_prompts.json"

POLL_INTERVAL = 10   # seconds between status checks
POLL_TIMEOUT  = 600  # Gen-4.5 can take 8-10 min — give it room
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 30]  # seconds


# ── Credit tracker ─────────────────────────────────────────────────────────────

def load_credits() -> dict:
    if CREDITS_FILE.exists():
        with open(CREDITS_FILE) as f:
            return json.load(f)
    return {"total_used": 0, "sessions": []}


def save_credits(data: dict) -> None:
    with open(CREDITS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_credits(used: int, prompt_id: int, clip_count: int) -> None:
    data = load_credits()
    data["total_used"] += used
    data["sessions"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_id": prompt_id,
        "clips_generated": clip_count,
        "credits_used": used,
        "total_used_cumulative": data["total_used"],
    })
    save_credits(data)
    logger.info(f"Credits used this session: {used} | Total: {data['total_used']}")


def get_total_credits_used() -> int:
    return load_credits().get("total_used", 0)


# ── Runway API client ──────────────────────────────────────────────────────────

class RunwayClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        })

    def submit_generation(self, prompt: str, duration: int = 5) -> str:
        """Submit a text-to-video task and return the task ID."""
        payload = {
            "model": "gen4.5",       # Gen-4.5 (disponibil pe Runway Pro)
            "promptText": prompt,    # camelCase — cerinta API
            "duration": duration,
            "ratio": "720:1280",     # 9:16 vertical (singurul portrait suportat)
        }
        url = f"{self.base_url}/text_to_video"
        logger.debug(f"Submitting generation: {prompt[:80]}…")

        for attempt, wait in enumerate(RETRY_BACKOFF, 1):
            try:
                resp = self.session.post(url, json=payload, timeout=30)
                resp.raise_for_status()
                task_id = resp.json()["id"]
                logger.info(f"Task submitted: {task_id}")
                return task_id
            except requests.HTTPError as e:
                logger.warning(f"HTTP error on attempt {attempt}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                else:
                    raise
            except requests.RequestException as e:
                logger.warning(f"Request error on attempt {attempt}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
                else:
                    raise

    def poll_task(self, task_id: str) -> dict:
        """Poll until the task succeeds or fails. Returns the completed task dict."""
        url = f"{self.base_url}/tasks/{task_id}"
        deadline = time.time() + POLL_TIMEOUT

        while time.time() < deadline:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            task = resp.json()
            status = task.get("status", "")
            logger.debug(f"Task {task_id} status: {status}")

            if status == "SUCCEEDED":
                return task
            elif status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"Task {task_id} ended with status: {status}")

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(f"Task {task_id} did not complete within {POLL_TIMEOUT}s")

    def download_video(self, task: dict, dest_path: Path) -> Path:
        """Download the generated video to dest_path."""
        video_url = task.get("output", [None])[0]
        if not video_url:
            raise ValueError(f"No output URL in task: {task.get('id')}")

        logger.info(f"Downloading clip → {dest_path.name}")
        with self.session.get(video_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return dest_path


# ── Main generation logic ──────────────────────────────────────────────────────

def _current_season() -> str:
    """Return the current meteorological season based on the current month."""
    month = datetime.now().month
    if month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    elif month in (9, 10, 11):
        return "autumn"
    else:
        return "winter"


def _get_recent_animals(n: int = 10) -> set[str]:
    """Return the set of animals used in the last N uploads."""
    uploaded_file = config.LOGS_DIR / "uploaded.json"
    if not uploaded_file.exists():
        return set()
    try:
        with open(uploaded_file, encoding="utf-8") as f:
            data = json.load(f)
        recent = data.get("uploads", [])[-n:]
        return {u.get("animal", "").lower() for u in recent if u.get("animal")}
    except Exception:
        return set()


def _load_animal_weights() -> dict[str, float]:
    """Load per-animal performance weights from data/weights.json."""
    weights_file = Path(__file__).parent.parent / "data" / "weights.json"
    if not weights_file.exists():
        return {}
    try:
        with open(weights_file, encoding="utf-8") as f:
            return json.load(f).get("animals", {})
    except Exception:
        return {}


def pick_prompt() -> dict:
    """Pick a seasonal prompt, avoiding animals used in the last 10 uploads.
    Uses performance weights to favour animals that get more views.
    """
    with open(PROMPTS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    season = _current_season()
    all_prompts = data["prompts"]

    # Prefer prompts that explicitly match this season
    seasonal = [p for p in all_prompts if season in p.get("seasons", ["any"])]
    generic  = [p for p in all_prompts if p.get("seasons", ["any"]) == ["any"]]

    # 70% chance to pick a seasonal prompt, 30% generic
    pool = seasonal if seasonal and random.random() < 0.7 else (seasonal + generic)
    if not pool:
        pool = all_prompts

    # Filter out recently used animals
    recent_animals = _get_recent_animals(10)
    fresh_pool = [p for p in pool if p.get("animal", "").lower() not in recent_animals]
    active_pool = fresh_pool if fresh_pool else pool

    if not fresh_pool:
        logger.warning("All animals recently used — picking from full pool")

    logger.info(f"Season: {season} | Pool: {len(active_pool)} prompts")

    # Weighted random choice — animals with more views get picked more often
    weights = _load_animal_weights()
    if weights:
        prompt_weights = [weights.get(p.get("animal", "").lower(), 1.0) for p in active_pool]
        chosen = random.choices(active_pool, weights=prompt_weights, k=1)[0]
    else:
        chosen = random.choice(active_pool)

    logger.info(f"Chosen: {chosen.get('animal')} / {chosen.get('action')}")
    return chosen


def generate_clips(
    prompt_entry: dict | None = None,
    clip_count: int = config.RUNWAY_CLIPS_PER_SHORT,
    duration: int = config.RUNWAY_CLIP_DURATION,
) -> list[Path]:
    """
    Generate `clip_count` clips for the given prompt (or a random one).
    Returns list of downloaded video Paths.
    """
    if not config.RUNWAY_API_KEY:
        raise RuntimeError("RUNWAY_API_KEY is not set in environment.")

    if prompt_entry is None:
        prompt_entry = pick_prompt()

    prompt_text = prompt_entry["prompt"]
    prompt_id = prompt_entry["id"]
    animal = prompt_entry["animal"]
    action = prompt_entry["action"]

    logger.info(f"Using prompt #{prompt_id}: {animal} / {action}")
    logger.info(f"Generating {clip_count} × {duration}s clips…")

    client = RunwayClient(config.RUNWAY_API_KEY, config.RUNWAY_API_BASE)

    # Submit all tasks in sequence (Runway free-tier is single-task)
    task_ids: list[str] = []
    for i in range(clip_count):
        logger.info(f"Submitting clip {i + 1}/{clip_count}")
        task_id = client.submit_generation(prompt_text, duration)
        task_ids.append(task_id)
        if i < clip_count - 1:
            time.sleep(2)  # polite gap between submissions

    # Poll and download
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clip_paths: list[Path] = []

    for i, task_id in enumerate(task_ids):
        logger.info(f"Waiting for clip {i + 1}/{clip_count} (task {task_id})…")
        try:
            task = client.poll_task(task_id)
            dest = config.VIDEOS_DIR / f"{timestamp}_{animal}_{action}_{i + 1:02d}.mp4"
            client.download_video(task, dest)
            clip_paths.append(dest)
            logger.info(f"Clip {i + 1} saved: {dest.name}")
        except Exception as e:
            logger.error(f"Failed to get clip {i + 1}: {e}")
            # Continue with remaining clips

    if not clip_paths:
        raise RuntimeError("No clips were successfully generated.")

    # Each 5-second clip on Gen-3 Alpha Turbo costs ~25 credits
    credits_per_clip = 25 * duration
    record_credits(
        used=credits_per_clip * len(clip_paths),
        prompt_id=prompt_id,
        clip_count=len(clip_paths),
    )

    logger.info(f"Generation complete: {len(clip_paths)}/{clip_count} clips ready.")
    return clip_paths


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    paths = generate_clips()
    print("\nGenerated clips:")
    for p in paths:
        print(f"  {p}")
