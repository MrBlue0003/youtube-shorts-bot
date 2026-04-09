"""
test_run.py — Rulează pipeline-ul COMPLET în modul TEST (fără Runway).

Pași:
  1. Generează clipuri demo cu ffmpeg (animații pastel 9:16)
  2. Asamblează Short-ul cu assemble.py
  3. Upload pe YouTube cu upload.py

Utilizare:
    python test_run.py              # asamblează + uploadează
    python test_run.py --no-upload  # doar generare + asamblare
    python test_run.py --no-gen     # skip generare, folosește clipuri existente
"""

import argparse
import io
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Forteaza UTF-8 pe stdout/stderr (Windows cp1252 fix)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import config
from scripts.test_generate import generate_test_clips
from scripts.assemble import assemble
from scripts.upload import upload_short

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOGS_DIR / "test_run.log", encoding="utf-8"),
    ],
)
# Forteaza UTF-8 si pe file handler-ul de log
for h in logging.root.handlers:
    if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
        h.setStream(io.TextIOWrapper(h.stream.buffer, encoding="utf-8", errors="replace") if hasattr(h.stream, "buffer") else h.stream)
logger = logging.getLogger("test_run")


def banner(msg: str) -> None:
    sep = "-" * 55
    logger.info(sep)
    logger.info(f"  {msg}")
    logger.info(sep)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test pipeline fără Runway ML")
    parser.add_argument("--no-upload", action="store_true", help="Nu uploada pe YouTube")
    parser.add_argument("--no-gen",    action="store_true", help="Folosește clipuri deja existente în videos/")
    args = parser.parse_args()

    start = datetime.now(timezone.utc)
    print()
    print("=" * 55)
    print("  TEST MODE - YouTube Shorts Bot")
    print("  (fara Runway ML)")
    print("=" * 55)
    print()

    clip_paths: list[Path] = []

    # ── PASUL 1: Generare clipuri ──────────────────────────────────────────────
    if args.no_gen:
        banner("PASUL 1 — Skip generare (--no-gen)")
        clip_paths = sorted(config.VIDEOS_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if not clip_paths:
            logger.error("Nu există clipuri în videos/ — rulează fără --no-gen.")
            return 1
        logger.info(f"Folosesc {len(clip_paths)} clipuri existente.")
    else:
        banner("PASUL 1 — Generare clipuri demo cu ffmpeg")
        try:
            clip_paths = generate_test_clips(
                count=config.RUNWAY_CLIPS_PER_SHORT,
                duration=config.RUNWAY_CLIP_DURATION,
            )
            logger.info(f"✓ {len(clip_paths)} clipuri generate.")
        except Exception as e:
            logger.error(f"Generare eșuată: {e}\n{traceback.format_exc()}")
            return 1

    # ── PASUL 2: Asamblare ─────────────────────────────────────────────────────
    banner("PASUL 2 — Asamblare Short (ffmpeg)")
    try:
        output_name = f"test_short_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        short_path = assemble(clip_paths=clip_paths, output_name=output_name)
        size_mb = short_path.stat().st_size / (1024 * 1024)
        logger.info(f"✓ Short: {short_path.name} ({size_mb:.1f} MB)")
    except Exception as e:
        logger.error(f"Asamblare eșuată: {e}\n{traceback.format_exc()}")
        return 1

    # ── PASUL 3: Upload ────────────────────────────────────────────────────────
    if args.no_upload:
        banner("PASUL 3 — Skip upload (--no-upload)")
        video_id = None
    else:
        banner("PASUL 3 — Upload YouTube")
        # Prompt fals pentru metadata
        fake_prompt = {
            "id": 0,
            "animal": "cat",
            "action": "test",
        }
        try:
            video_id = upload_short(
                video_path=short_path,
                prompt_entry=fake_prompt,
                wait_for_schedule=False,   # upload imediat în modul test
            )
            logger.info(f"✓ Uploaded: https://www.youtube.com/watch?v={video_id}")
        except Exception as e:
            logger.error(f"Upload eșuat: {e}\n{traceback.format_exc()}")
            return 1

    # ── Final ──────────────────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    print()
    print("=" * 55)
    print("  [OK]  TEST COMPLET!")
    print(f"  Durata: {elapsed:.0f}s")
    print(f"  Short:  {short_path}")
    if video_id:
        print(f"  Video:  https://www.youtube.com/watch?v={video_id}")
    print("=" * 55)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
