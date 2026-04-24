"""
main.py — Daily orchestrator for the YouTube Shorts bot.

Steps:
  1. Validate config
  2. Check Runway credit threshold
  3. Ensure music library (download_music.py)
  4. Generate clips (runway_generate.py)
  5. Assemble Short (assemble.py)
  6. Upload to YouTube (upload.py)
  7. Log result and print terminal notification
"""

import json
import logging
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import config
from scripts.runway_generate import generate_clips, get_total_credits_used, pick_prompt
from scripts.assemble import assemble
from scripts.upload import upload_short
from scripts.download_music import download_music
import scripts.monthly_compilation as monthly_compilation

# ── Logging setup ──────────────────────────────────────────────────────────────
LOG_FILE = config.LOGS_DIR / "daily_log.txt"

def setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )

logger = logging.getLogger("main")


# ── Banner ─────────────────────────────────────────────────────────────────────

def _animal_posted_recently(animal: str, hours: int = 20) -> bool:
    """Return True if this animal was uploaded in the last N hours.
    Prevents duplicate posts from double workflow_dispatch triggers.
    """
    uploaded_file = config.LOGS_DIR / "uploaded.json"
    if not uploaded_file.exists():
        return False
    from datetime import timedelta
    with open(uploaded_file, encoding="utf-8") as f:
        log = json.load(f)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for u in log.get("uploads", []):
        if u.get("animal", "").lower() == animal.lower():
            try:
                if datetime.fromisoformat(u["timestamp"]) > cutoff:
                    return True
            except Exception:
                pass
    return False


def print_banner(msg: str, char: str = "─", width: int = 60) -> None:
    line = char * width
    logger.info(line)
    logger.info(msg.center(width))
    logger.info(line)


# ── Steps ─────────────────────────────────────────────────────────────────────

def step_validate() -> None:
    print_banner("STEP 1 — Validate Config")
    missing = config.validate()
    if missing:
        raise EnvironmentError(
            f"Missing required config values: {', '.join(missing)}\n"
            f"Check your .env file."
        )
    logger.info("Config OK")


def step_check_credits() -> None:
    print_banner("STEP 2 — Check Credits")
    used = get_total_credits_used()
    logger.info(f"Credits used so far: {used}")
    # Note: we track credits used, not remaining. Warn if usage seems high.
    # Adjust MIN_CREDITS logic based on your Runway plan.
    logger.info(
        f"Runway minimum threshold set to {config.RUNWAY_MIN_CREDITS} credits. "
        "Runway does not expose remaining balance via API — monitor in dashboard."
    )


def step_ensure_music() -> None:
    print_banner("STEP 3 — Ensure Music Library")
    tracks = download_music(count=15)
    if tracks:
        logger.info(f"Music library OK: {len(tracks)} track(s) available")
    else:
        logger.warning("No music tracks available — Short will have no audio")


def step_generate(prompt_entry: dict) -> list[Path]:
    print_banner("STEP 4 — Generate Clips")
    clips = generate_clips(
        prompt_entry=prompt_entry,
        clip_count=config.RUNWAY_CLIPS_PER_SHORT,
        duration=config.RUNWAY_CLIP_DURATION,
    )
    logger.info(f"Generated {len(clips)} clips")
    return clips


def step_assemble(clip_paths: list[Path], output_name: str, prompt_entry: dict) -> Path:
    print_banner("STEP 5 — Assemble Short")
    short_path = assemble(clip_paths=clip_paths, output_name=output_name,
                          prompt_entry=prompt_entry)
    size_mb = short_path.stat().st_size / (1024 * 1024)
    logger.info(f"Short assembled: {short_path.name} ({size_mb:.1f} MB)")
    return short_path


def step_upload(short_path: Path, prompt_entry: dict) -> str:
    print_banner("STEP 6 — Upload to YouTube")
    video_id = upload_short(
        video_path=short_path,
        prompt_entry=prompt_entry,
        wait_for_schedule=False,  # cron schedule controls timing (3x/day)
    )
    logger.info(f"Uploaded! https://www.youtube.com/watch?v={video_id}")
    return video_id


# ── Notification ───────────────────────────────────────────────────────────────

def notify_terminal(success: bool, video_id: str = "", error: str = "") -> None:
    """Print a clear terminal notification at the end of the run."""
    sep = "=" * 60
    print(sep)
    if success:
        print("  [OK]  SHORT PUBLISHED SUCCESSFULLY")
        print(f"      https://www.youtube.com/watch?v={video_id}")
    else:
        print("  [FAIL]  PIPELINE FAILED")
        print(f"      {error[:200]}")
    print(f"      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(sep)


def _save_logs_to_repo() -> None:
    """Delegate to save_log.py for conflict-safe push."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/save_log.py"],
            cwd=str(Path(__file__).parent),
            capture_output=True, text=True,
        )
        logger.info(result.stdout.strip())
        if result.returncode != 0:
            logger.warning(f"save_log.py exited {result.returncode}: {result.stderr[:300]}")
    except Exception as e:
        logger.warning(f"Could not save logs to repo: {e}")


def notify_telegram(msg: str) -> None:
    """Optional: send a Telegram notification."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    setup_logging()
    start_ts = datetime.now(timezone.utc)

    print_banner("YouTube Shorts Bot - Daily Run", "=")
    logger.info(f"Started at {start_ts.isoformat()}")

    try:
        step_validate()
        step_check_credits()
        step_ensure_music()

        # Pick a prompt once and reuse across steps for consistent metadata
        prompt_entry = pick_prompt()
        logger.info(
            f"Today's prompt: #{prompt_entry['id']} — "
            f"{prompt_entry['animal']} / {prompt_entry['action']}"
        )

        output_name = (
            f"short_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
            f"{prompt_entry['animal']}_{prompt_entry['action']}"
        )

        # Guard: skip if this animal was already posted in last 20h
        animal = prompt_entry.get("animal", "")
        if _animal_posted_recently(animal):
            logger.warning(f"Animal '{animal}' posted recently — skipping to avoid duplicate.")
            return 0

        clip_paths = step_generate(prompt_entry)
        short_path = step_assemble(clip_paths, output_name, prompt_entry)
        video_id = step_upload(short_path, prompt_entry)

        # Cleanup raw Runway clips — they can be large and aren't needed after assembly
        for clip in clip_paths:
            try:
                clip.unlink(missing_ok=True)
            except Exception:
                pass
        logger.info(f"Cleaned up {len(clip_paths)} raw clip(s) from videos/")

        elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
        logger.info(f"Pipeline completed in {elapsed:.0f}s")

        notify_terminal(success=True, video_id=video_id)
        notify_telegram(
            f"✅ <b>Short published!</b>\n"
            f"<a href='https://www.youtube.com/watch?v={video_id}'>Watch it</a>\n"
            f"Animal: {prompt_entry['animal']} | Action: {prompt_entry['action']}"
        )

        # Monthly Best Of compilation — runs automatically on day 1-3 of each month
        uploaded_file = config.LOGS_DIR / "uploaded.json"
        if monthly_compilation.should_run(uploaded_file):
            logger.info("Monthly Best Of compilation triggered")
            try:
                comp_id = monthly_compilation.run(
                    uploaded_file,
                    config.SHORTS_DIR,
                )
                if comp_id:
                    logger.info(f"Compilation: https://www.youtube.com/watch?v={comp_id}")
                    notify_telegram(
                        f"🎬 <b>Monthly compilation uploaded!</b>\n"
                        f"<a href='https://www.youtube.com/watch?v={comp_id}'>Watch it</a>"
                    )
            except Exception as comp_err:
                logger.error(f"Monthly compilation failed (non-fatal): {comp_err}")

        _save_logs_to_repo()
        return 0

    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Pipeline failed: {e}\n{tb}")
        notify_terminal(success=False, error=str(e))
        notify_telegram(f"❌ <b>Short bot failed!</b>\n<code>{str(e)[:500]}</code>")
        return 1


if __name__ == "__main__":
    sys.exit(main())
