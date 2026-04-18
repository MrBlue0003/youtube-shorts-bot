"""
monthly_compilation.py — Best Of monthly compilation for CuteDaily Bot.

Called automatically from main.py on day 1-3 of each month.

Flow:
  1. Check uploaded.json for last month's videos
  2. Fetch view counts via YouTube Data API
  3. Sort by views, pick top clips to fill ~30 min
  4. Download each clip with yt-dlp
  5. Concatenate with ffmpeg
  6. Upload as "Best of [Month Year] | CuteDaily"
  7. Record in uploaded.json so it won't re-run
"""

import json
import logging
import shutil
import subprocess
import traceback
from calendar import month_name
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("monthly_compilation")

# No clip limit — take everything posted last month
MAX_CLIPS = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _last_month() -> tuple[int, int]:
    """Return (month, year) for the previous calendar month."""
    now = datetime.now(timezone.utc)
    if now.month == 1:
        return 12, now.year - 1
    return now.month - 1, now.year


def should_run(uploaded_file: Path) -> bool:
    """Return True if it's day 1-3 and this month's compilation hasn't run yet."""
    now = datetime.now(timezone.utc)
    if now.day > 3:
        return False

    month, year = _last_month()
    key = f"{year}-{month:02d}"

    if not uploaded_file.exists():
        return False

    with open(uploaded_file, encoding="utf-8") as f:
        log = json.load(f)

    for entry in log.get("uploads", []):
        if entry.get("is_compilation") and entry.get("compilation_month") == key:
            logger.info(f"Compilation for {key} already done — skipping")
            return False

    return True


def _get_last_month_videos(uploaded_file: Path) -> list[dict]:
    month, year = _last_month()
    with open(uploaded_file, encoding="utf-8") as f:
        log = json.load(f)

    videos = []
    for entry in log.get("uploads", []):
        if entry.get("is_compilation"):
            continue
        ts = entry.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.month == month and dt.year == year:
                videos.append(entry)
        except (ValueError, AttributeError):
            continue
    return videos


def _get_view_counts(video_ids: list[str], youtube) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(part="statistics", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            counts[item["id"]] = int(item["statistics"].get("viewCount", 0))
    return counts


def _download_video(video_id: str, output_path: Path) -> bool:
    url = f"https://www.youtube.com/watch?v={video_id}"
    result = subprocess.run(
        [
            "yt-dlp",
            "-f", "bestvideo[height<=1920][ext=mp4]+bestaudio[ext=m4a]/mp4",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "-o", str(output_path),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        logger.warning(f"yt-dlp failed [{video_id}]: {result.stderr[-400:]}")
    return output_path.exists()


def _build_compilation(clip_paths: list[Path], output_path: Path) -> bool:
    concat_file = output_path.parent / "concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.as_posix()}'\n")

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    concat_file.unlink(missing_ok=True)
    if result.returncode != 0:
        logger.error(f"ffmpeg concat failed: {result.stderr[-800:]}")
    return result.returncode == 0


# ── Main entry point ───────────────────────────────────────────────────────────

def run(uploaded_file: Path, output_base: Path) -> str | None:
    """
    Run the monthly Best Of compilation.
    Returns YouTube video_id on success, None on failure/skip.
    """
    month, year = _last_month()
    month_str = f"{month_name[month]} {year}"

    logger.info("=" * 60)
    logger.info(f"  Monthly Compilation: Best of {month_str}")
    logger.info("=" * 60)

    videos = _get_last_month_videos(uploaded_file)
    if not videos:
        logger.warning("No regular uploads found for last month — skipping")
        return None

    logger.info(f"Last month: {len(videos)} video(s) found")

    # Get view counts and sort
    video_ids = [v["video_id"] for v in videos if v.get("video_id")]
    youtube_client = None
    try:
        from scripts.upload import get_youtube_client
        youtube_client = get_youtube_client()
        view_counts = _get_view_counts(video_ids, youtube_client)
    except Exception as e:
        logger.warning(f"Could not fetch view counts: {e} — using upload order")
        view_counts = {}

    for v in videos:
        v["_views"] = view_counts.get(v.get("video_id", ""), 0)

    videos.sort(key=lambda v: v["_views"], reverse=True)
    selected = videos  # take all — no cap

    logger.info(f"Selected all {len(selected)} clip(s) from last month")
    for v in selected:
        logger.info(f"  {v['video_id']}  {v['_views']:>6} views  {v.get('title', '')}")

    # Work directory
    work_dir = output_base / f"compilation_{year}_{month:02d}"
    work_dir.mkdir(parents=True, exist_ok=True)

    # Download clips
    clip_paths: list[Path] = []
    for i, v in enumerate(selected):
        vid_id = v.get("video_id", "")
        if not vid_id:
            continue
        out = work_dir / f"{i:02d}_{vid_id}.mp4"
        logger.info(f"Downloading [{i + 1}/{len(selected)}] {vid_id}…")
        if _download_video(vid_id, out):
            clip_paths.append(out)
        else:
            logger.warning(f"  Skipped {vid_id} (download failed)")

    if not clip_paths:
        logger.error("No clips downloaded — aborting compilation")
        shutil.rmtree(work_dir, ignore_errors=True)
        return None

    # Concatenate
    final_path = work_dir / f"bestof_{year}_{month:02d}.mp4"
    logger.info(f"Building compilation from {len(clip_paths)} clip(s)…")
    if not _build_compilation(clip_paths, final_path):
        shutil.rmtree(work_dir, ignore_errors=True)
        return None

    # Upload
    try:
        from scripts.upload import upload_compilation
        if youtube_client is None:
            from scripts.upload import get_youtube_client
            youtube_client = get_youtube_client()
        video_id = upload_compilation(final_path, month_str, youtube_client)
    except Exception as e:
        logger.error(f"Compilation upload failed: {e}\n{traceback.format_exc()}")
        shutil.rmtree(work_dir, ignore_errors=True)
        return None

    # Record in uploaded.json
    uploaded_log = Path(__file__).parent.parent / "logs" / "uploaded.json"
    with open(uploaded_log, encoding="utf-8") as f:
        log_data = json.load(f)

    log_data["uploads"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "video_id": video_id,
        "title": f"Best of {month_str} | CuteDaily",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "is_compilation": True,
        "compilation_month": f"{year}-{month:02d}",
        "clips_count": len(clip_paths),
    })

    with open(uploaded_log, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)

    shutil.rmtree(work_dir, ignore_errors=True)

    logger.info("=" * 60)
    logger.info(f"  Compilation done! https://www.youtube.com/watch?v={video_id}")
    logger.info("=" * 60)
    return video_id
