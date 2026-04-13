"""
config.py — Global configuration loaded from .env
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
PROMPTS_DIR = BASE_DIR / "prompts"
MUSIC_DIR = BASE_DIR / "music"
VIDEOS_DIR = BASE_DIR / "videos"
SHORTS_DIR = BASE_DIR / "shorts"
LOGS_DIR = BASE_DIR / "logs"

# Ensure all directories exist
for d in (VIDEOS_DIR, SHORTS_DIR, LOGS_DIR, MUSIC_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv(BASE_DIR / ".env")

RUNWAY_API_KEY: str = os.getenv("RUNWAY_API_KEY", "")

# ── ffmpeg path (auto-detectat dacă nu e setat) ────────────────────────────────
_DEFAULT_FFMPEG_PATHS = [
    r"C:\Users\Valentin\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",   # dacă e în PATH
]

def _find_ffmpeg() -> str:
    import shutil
    custom = os.getenv("FFMPEG_BIN", "")
    if custom:
        return custom
    for p in _DEFAULT_FFMPEG_PATHS:
        if shutil.which(p) or Path(p).exists():
            return p
    return "ffmpeg"

FFMPEG_BIN: str = _find_ffmpeg()

def _find_ffprobe() -> str:
    """Derivă calea ffprobe din cea a ffmpeg (sunt în același director)."""
    from pathlib import Path as _Path
    p = _Path(FFMPEG_BIN)
    candidate = p.parent / p.name.replace("ffmpeg", "ffprobe")
    return str(candidate) if candidate.exists() else "ffprobe"

FFPROBE_BIN: str = _find_ffprobe()
RUNWAY_API_BASE: str = os.getenv("RUNWAY_API_BASE", "https://api.dev.runwayml.com/v1")

YOUTUBE_CLIENT_SECRETS: str = os.getenv(
    "YOUTUBE_CLIENT_SECRETS",
    str(BASE_DIR / "client_secrets.json"),
)
YOUTUBE_TOKEN_FILE: str = os.getenv(
    "YOUTUBE_TOKEN_FILE",
    str(BASE_DIR / "youtube_token.json"),
)
YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

# ID-ul canalului YouTube tinta (optional dar recomandat ca protectie)
# Gasit automat la primul upload daca nu e setat
YOUTUBE_CHANNEL_ID: str = os.getenv("YOUTUBE_CHANNEL_ID", "")

# ── Video settings ─────────────────────────────────────────────────────────────
VIDEO_WIDTH: int = int(os.getenv("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT: int = int(os.getenv("VIDEO_HEIGHT", "1920"))
VIDEO_FPS: int = int(os.getenv("VIDEO_FPS", "30"))
SHORT_MIN_DURATION: int = int(os.getenv("SHORT_MIN_DURATION", "5"))
SHORT_MAX_DURATION: int = int(os.getenv("SHORT_MAX_DURATION", "60"))

# ── Runway settings ────────────────────────────────────────────────────────────
RUNWAY_CLIP_DURATION: int = 5   # 5s — single clip per short (hardcoded to prevent Railway env override)
RUNWAY_CLIPS_PER_SHORT: int = 1  # 1 clip per run, posted 1x/day
RUNWAY_MIN_CREDITS: int = int(os.getenv("RUNWAY_MIN_CREDITS", "75"))

# ── Notifications ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Validation ─────────────────────────────────────────────────────────────────
def validate() -> list[str]:
    """Return a list of missing critical config values."""
    missing = []
    if not RUNWAY_API_KEY:
        missing.append("RUNWAY_API_KEY")
    if not (Path(YOUTUBE_CLIENT_SECRETS).exists() or YOUTUBE_REFRESH_TOKEN):
        missing.append("YOUTUBE_CLIENT_SECRETS or YOUTUBE_REFRESH_TOKEN")
    return missing
