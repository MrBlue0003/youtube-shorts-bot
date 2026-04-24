"""analytics.py — Fetch video stats and update per-animal weights.

Reads logs/uploaded.json, fetches viewCount for each video via YouTube Data API,
computes average views per animal type, writes data/weights.json.

pick_prompt() in runway_generate.py uses weights.json to favour animals that
perform better, creating a self-tuning feedback loop over time.

Run automatically every Sunday via the analytics workflow, or manually:
    python scripts/analytics.py
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from scripts.upload import get_youtube_client

logger = logging.getLogger(__name__)

DATA_DIR     = config.BASE_DIR / "data"
WEIGHTS_FILE = DATA_DIR / "weights.json"
DATA_DIR.mkdir(exist_ok=True)

MIN_VIDEOS   = 3      # need at least this many per animal before adjusting weight
WEIGHT_MIN   = 0.5
WEIGHT_MAX   = 2.0


def _fetch_stats(youtube, video_ids: list[str]) -> dict[str, int]:
    """Return {video_id: view_count} batched 50 at a time."""
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = ",".join(video_ids[i:i + 50])
        try:
            resp = youtube.videos().list(part="statistics", id=batch).execute()
            for item in resp.get("items", []):
                result[item["id"]] = int(item["statistics"].get("viewCount", 0))
        except Exception as e:
            logger.warning(f"Stats fetch failed: {e}")
    return result


def compute_weights(youtube) -> dict[str, float]:
    """Return {animal: weight} based on avg views. Empty if not enough data."""
    uploaded_file = config.LOGS_DIR / "uploaded.json"
    if not uploaded_file.exists():
        return {}

    with open(uploaded_file, encoding="utf-8") as f:
        uploads = json.load(f).get("uploads", [])

    if not uploads:
        return {}

    video_ids = [u["video_id"] for u in uploads if u.get("video_id")]
    if not video_ids:
        return {}

    logger.info(f"Fetching stats for {len(video_ids)} videos…")
    stats = _fetch_stats(youtube, video_ids)

    # Group views by animal
    animal_views: dict[str, list[int]] = {}
    for u in uploads:
        vid    = u.get("video_id")
        animal = u.get("animal", "").lower()
        if vid and animal and vid in stats:
            animal_views.setdefault(animal, []).append(stats[vid])

    # Only animals with enough data
    qualified = {a: v for a, v in animal_views.items() if len(v) >= MIN_VIDEOS}
    if not qualified:
        logger.info("Not enough data per animal yet — keeping equal weights")
        return {}

    animal_avg  = {a: sum(v) / len(v) for a, v in qualified.items()}
    overall_avg = sum(animal_avg.values()) / len(animal_avg)

    weights = {}
    for animal, avg in animal_avg.items():
        raw = avg / overall_avg if overall_avg > 0 else 1.0
        weights[animal] = round(max(WEIGHT_MIN, min(WEIGHT_MAX, raw)), 3)
        logger.info(f"  {animal:12s}  avg_views={avg:,.0f}  n={len(qualified[animal])}  weight={weights[animal]}")

    return weights


def update_weights(youtube) -> None:
    """Compute new weights, merge with existing, save."""
    new_weights = compute_weights(youtube)

    existing: dict[str, float] = {}
    if WEIGHTS_FILE.exists():
        with open(WEIGHTS_FILE, encoding="utf-8") as f:
            existing = json.load(f).get("animals", {})

    merged = {**existing, **new_weights}

    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(timezone.utc).date().isoformat(),
            "animals": merged,
        }, f, indent=2)

    logger.info(f"Weights saved → {WEIGHTS_FILE}")


def analyze_title_ab(youtube) -> None:
    """Log A/B performance: action-specific titles vs generic templates.

    Informational only — does not modify weights.json.
    Helps decide whether action-specific titles are worth keeping.
    """
    uploaded_file = config.LOGS_DIR / "uploaded.json"
    if not uploaded_file.exists():
        return

    with open(uploaded_file, encoding="utf-8") as f:
        uploads = json.load(f).get("uploads", [])

    # Only uploads that have title_source field
    tagged = [u for u in uploads if u.get("title_source") and u.get("video_id")]
    if len(tagged) < 5:
        logger.info("Not enough tagged uploads for A/B analysis yet (need ≥5)")
        return

    video_ids = [u["video_id"] for u in tagged]
    stats = _fetch_stats(youtube, video_ids)

    groups: dict[str, list[int]] = {}
    for u in tagged:
        src = u.get("title_source", "unknown")
        vid = u.get("video_id")
        if vid and vid in stats:
            groups.setdefault(src, []).append(stats[vid])

    logger.info("── A/B Title Source Analysis ──")
    for src, views in sorted(groups.items()):
        avg = sum(views) / len(views) if views else 0
        logger.info(f"  {src:20s}  n={len(views):3d}  avg_views={avg:,.0f}")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        youtube = get_youtube_client()
        update_weights(youtube)
        analyze_title_ab(youtube)
        return 0
    except Exception as e:
        logger.error(f"Analytics failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
