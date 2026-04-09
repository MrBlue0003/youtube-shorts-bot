"""
test_generate.py — Generează clipuri de test FĂRĂ Runway ML.

Surse (în ordine de prioritate):
  1. Pixabay Videos API  — dacă PIXABAY_API_KEY e setat în .env
  2. ffmpeg lavfi        — fallback offline complet, animații pastel 9:16

Rezultatul e identic cu runway_generate.py: o listă de Path-uri în videos/.
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

FFMPEG = config.FFMPEG_BIN

# Animale + culori pastel pentru clipurile ffmpeg
CLIP_THEMES = [
    {"label": "Cute Cat",     "bg": "0xFFB7C5", "accent": "0xFF6B9D"},  # roz
    {"label": "Happy Bunny",  "bg": "0xC8E6C9", "accent": "0x66BB6A"},  # verde mint
    {"label": "Cozy Bear",    "bg": "0xFFE0B2", "accent": "0xFFA726"},  # portocaliu pastel
    {"label": "Tiny Duck",    "bg": "0xFFF9C4", "accent": "0xF9A825"},  # galben
    {"label": "Chill Fox",    "bg": "0xFFCCBC", "accent": "0xFF7043"},  # piersică
    {"label": "Happy Panda",  "bg": "0xE1BEE7", "accent": "0xAB47BC"},  # lavandă
]


# ── Pixabay downloader ─────────────────────────────────────────────────────────

def _download_from_pixabay(count: int, duration: int) -> list[Path]:
    """Descarcă clipuri cu animale de pe Pixabay (necesită PIXABAY_API_KEY)."""
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        raise EnvironmentError("PIXABAY_API_KEY nu e setat.")

    queries = ["cat", "dog", "rabbit", "duck", "bear", "panda"]
    clips: list[Path] = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for i, query in enumerate(queries[:count]):
        url = (
            f"https://pixabay.com/api/videos/"
            f"?key={api_key}&q={query}&video_type=film&per_page=3&safesearch=true"
        )
        logger.info(f"Pixabay search: {query}")
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read())

            hits = data.get("hits", [])
            if not hits:
                logger.warning(f"Niciun rezultat Pixabay pentru: {query}")
                continue

            # Alege calitatea 'medium' sau prima disponibilă
            videos = hits[0]["videos"]
            video_url = (
                videos.get("medium", {}).get("url")
                or videos.get("small", {}).get("url")
                or videos.get("tiny", {}).get("url")
            )
            if not video_url:
                continue

            dest = config.VIDEOS_DIR / f"{ts}_test_{query}_{i+1:02d}.mp4"
            logger.info(f"Descarcam {query} → {dest.name}")
            urllib.request.urlretrieve(video_url, str(dest))
            clips.append(dest)
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"Pixabay error pentru {query}: {e}")

    return clips


# ── ffmpeg generator ───────────────────────────────────────────────────────────

def _generate_with_ffmpeg(count: int, duration: int) -> list[Path]:
    """
    Generează clipuri 1080×1920 cu ffmpeg (lavfi color + drawtext).
    Design simplu: fundal pastel gradient + text animal centrat.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    clips: list[Path] = []

    for i in range(count):
        theme = CLIP_THEMES[i % len(CLIP_THEMES)]
        label = theme["label"]
        bg    = theme["bg"]
        acc   = theme["accent"]
        slug  = label.replace(" ", "_").lower()
        dest  = config.VIDEOS_DIR / f"{ts}_test_{slug}_{i+1:02d}.mp4"

        # Hue shift lent pentru efect de culoare animată
        # drawtext cu label mare + "TEST MODE" jos
        vf = ",".join([
            "hue=h=t*15",                                           # rotatie culoare lenta
            "drawbox=x=0:y=800:w=iw:h=320:color=" + acc + "@0.35:t=fill",  # banner accent
            "drawtext=text='" + label + "'"
            ":fontsize=95"
            ":fontcolor=white"
            ":shadowcolor=black@0.5:shadowx=3:shadowy=3"
            ":x=(w-text_w)/2:y=860",                                # text centrat pe banner
            "drawtext=text='✦ TEST MODE ✦'"
            ":fontsize=42"
            ":fontcolor=white@0.8"
            ":x=(w-text_w)/2:y=h-100",                              # text mic jos
        ])

        cmd = [
            FFMPEG, "-y",
            "-f", "lavfi",
            "-i", f"color=c={bg}:size=1080x1920:rate=30",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-t", str(duration),
            "-movflags", "+faststart",
            str(dest),
        ]

        logger.info(f"Generez clip {i+1}/{count}: {label}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"ffmpeg error:\n{result.stderr[-1500:]}")
            raise RuntimeError(f"Generare clip eșuată: {label}")

        size_kb = dest.stat().st_size // 1024
        logger.info(f"  -> {dest.name} ({size_kb} KB)")
        clips.append(dest)

    return clips


# ── Public interface ───────────────────────────────────────────────────────────

def generate_test_clips(
    count: int = config.RUNWAY_CLIPS_PER_SHORT,
    duration: int = config.RUNWAY_CLIP_DURATION,
) -> list[Path]:
    """
    Generează `count` clipuri de test, returnează lista de Path-uri.
    Încearcă Pixabay dacă PIXABAY_API_KEY e setat, altfel ffmpeg.
    """
    config.VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

    if os.getenv("PIXABAY_API_KEY"):
        logger.info("Mod test: Pixabay API")
        try:
            clips = _download_from_pixabay(count, duration)
            if len(clips) >= 2:
                return clips
            logger.warning("Pixabay a returnat prea puțin — cad pe ffmpeg.")
        except Exception as e:
            logger.warning(f"Pixabay eșuat: {e} — cad pe ffmpeg.")

    logger.info("Mod test: generare locala cu ffmpeg")
    return _generate_with_ffmpeg(count, duration)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    clips = generate_test_clips()
    print(f"\n{len(clips)} clipuri generate:")
    for c in clips:
        print(f"  {c}")
