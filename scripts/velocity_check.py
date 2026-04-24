"""
velocity_check.py — 24-hour view velocity check for recently uploaded Shorts.

Run daily ~24h after the main post (via velocity_check.yml at 15:00 UTC).
Checks videos uploaded 18-30 hours ago (the "first-day window") and:

  - If views ≥ VIRAL_THRESHOLD  → boost that animal's weight by 1.3× (capped at WEIGHT_MAX)
  - If views ≤ DUD_THRESHOLD    → gently reduce weight by 0.9× (floored at WEIGHT_MIN)
  - Otherwise                   → no change

This creates a fast feedback loop instead of waiting for the weekly analytics run.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from scripts.upload import get_youtube_client

logger = logging.getLogger(__name__)

DATA_DIR     = config.BASE_DIR / "data"
WEIGHTS_FILE = DATA_DIR / "weights.json"

# Thresholds for the 18-30h view window
VIRAL_THRESHOLD = 300   # views in first 24h → boost
DUD_THRESHOLD   = 30    # views in first 24h → slight penalty

BOOST_FACTOR    = 1.30
PENALTY_FACTOR  = 0.90
WEIGHT_MIN      = 0.50
WEIGHT_MAX      = 2.00

CHECK_WINDOW_MIN_HOURS = 18
CHECK_WINDOW_MAX_HOURS = 30


def _load_weights() -> dict[str, float]:
    if not WEIGHTS_FILE.exists():
        return {}
    with open(WEIGHTS_FILE, encoding="utf-8") as f:
        return json.load(f).get("animals", {})


def _save_weights(animals: dict[str, float]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    existing = {}
    if WEIGHTS_FILE.exists():
        with open(WEIGHTS_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    existing["animals"] = animals
    existing["updated"] = datetime.now(timezone.utc).date().isoformat()
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
    logger.info(f"Weights updated → {WEIGHTS_FILE}")


def _fetch_views(youtube, video_ids: list[str]) -> dict[str, int]:
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = ",".join(video_ids[i:i + 50])
        try:
            resp = youtube.videos().list(part="statistics", id=batch).execute()
            for item in resp.get("items", []):
                result[item["id"]] = int(item["statistics"].get("viewCount", 0))
        except Exception as e:
            logger.warning(f"Stats fetch error: {e}")
    return result


def run_velocity_check(youtube) -> None:
    uploaded_file = config.LOGS_DIR / "uploaded.json"
    if not uploaded_file.exists():
        logger.info("No uploads yet — nothing to check.")
        return

    with open(uploaded_file, encoding="utf-8") as f:
        uploads = json.load(f).get("uploads", [])

    now = datetime.now(timezone.utc)
    window_min = now - timedelta(hours=CHECK_WINDOW_MAX_HOURS)
    window_max = now - timedelta(hours=CHECK_WINDOW_MIN_HOURS)

    # Find videos in the 18-30h window
    in_window = []
    for u in uploads:
        vid = u.get("video_id")
        ts_str = u.get("timestamp", "")
        if not vid or not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if window_min <= ts <= window_max:
            in_window.append(u)

    if not in_window:
        logger.info(
            f"No videos in the {CHECK_WINDOW_MIN_HOURS}-{CHECK_WINDOW_MAX_HOURS}h window — nothing to check."
        )
        return

    logger.info(f"Checking {len(in_window)} video(s) in the 24h velocity window…")
    video_ids = [u["video_id"] for u in in_window]
    stats = _fetch_views(youtube, video_ids)

    weights = _load_weights()
    changed = False

    for u in in_window:
        vid    = u["video_id"]
        animal = u.get("animal", "").lower()
        views  = stats.get(vid, 0)
        title  = u.get("title", vid)[:60]

        logger.info(f"  {animal:12s}  {views:>6,} views  ({title}…)")

        if not animal:
            continue

        current = weights.get(animal, 1.0)
        if views >= VIRAL_THRESHOLD:
            new_w = round(min(WEIGHT_MAX, current * BOOST_FACTOR), 3)
            logger.info(f"  ↑ VIRAL: {animal} weight {current} → {new_w} (+{BOOST_FACTOR:.0%})")
            weights[animal] = new_w
            changed = True
        elif views <= DUD_THRESHOLD:
            new_w = round(max(WEIGHT_MIN, current * PENALTY_FACTOR), 3)
            logger.info(f"  ↓ slow: {animal} weight {current} → {new_w} ({PENALTY_FACTOR:.0%})")
            weights[animal] = new_w
            changed = True
        else:
            logger.info(f"  = OK: {animal} weight unchanged ({current})")

    if changed:
        _save_weights(weights)
    else:
        logger.info("No weight changes needed.")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        youtube = get_youtube_client()
        run_velocity_check(youtube)
        return 0
    except Exception as e:
        logger.error(f"Velocity check failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
