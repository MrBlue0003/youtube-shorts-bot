"""
download_music.py — Descarcă melodii royalty-free în music/.

Surse:
  1. Incompetech (Kevin MacLeod) — CC-BY 4.0, 21 melodii verificate
  2. Pixabay CDN                 — CC0, URL-uri directe testate (fallback)

Refresh săptămânal: la fiecare 7 zile șterge melodiile vechi și descarcă altele noi.
Rotație: săptămâna N folosește un subset diferit de melodii, evitând repetiția.
"""

import json
import logging
import random
import sys
import urllib.parse
import warnings
from datetime import datetime, timezone
from pathlib import Path

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

MUSIC_DIR = config.MUSIC_DIR
REFRESH_FILE = config.LOGS_DIR / "music_refresh.json"
USED_MUSIC_FILE = config.LOGS_DIR / "used_music.json"
REFRESH_DAYS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

# ── Catalog Incompetech (CC-BY 4.0 — Kevin MacLeod, incompetech.com) ──────────
# Toate URL-urile sunt verificate și funcționale.
INCOMPETECH_BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"

INCOMPETECH_TRACKS = [
    # Playful / Funny
    "Fluffing a Duck",
    "Monkeys Spinning Monkeys",
    "Sneaky Snitch",
    "Chipper Doodle v2",
    "Happy Bee",
    "Pixel Peeker Polka - faster",
    "Bushwick Tarantella Loop",
    "Merry Go",
    "Hackbeat",
    # Upbeat / Cheerful
    "Carefree",
    "Opportunity Walks",
    "Easy Lemon",
    "Local Forecast",
    "Wallpaper",
    "Bossa Antigua",
    # Calm / Background
    "Gymnopedie No 1",
    "Crinoline Dreams",
    "Pamgaea",
    "Perspectives",
    "Airport Lounge",
    "Lobby Time",
]

# ── Mood mapping for music selection ──────────────────────────────────────────
# Maps lowercase track name → mood tag ("playful" | "upbeat" | "calm")
# Used by assemble.py to pick mood-matched music for each action.
TRACK_MOODS: dict[str, str] = {
    "fluffing a duck":              "playful",
    "monkeys spinning monkeys":     "playful",
    "sneaky snitch":                "playful",
    "chipper doodle v2":            "playful",
    "happy bee":                    "playful",
    "pixel peeker polka - faster":  "playful",
    "bushwick tarantella loop":     "playful",
    "merry go":                     "playful",
    "hackbeat":                     "playful",
    "carefree":                     "upbeat",
    "opportunity walks":            "upbeat",
    "easy lemon":                   "upbeat",
    "local forecast":               "upbeat",
    "wallpaper":                    "upbeat",
    "bossa antigua":                "upbeat",
    "gymnopedie no 1":              "calm",
    "crinoline dreams":             "calm",
    "pamgaea":                      "calm",
    "perspectives":                 "calm",
    "airport lounge":               "calm",
    "lobby time":                   "calm",
}


def get_mood_for_track(filename: str) -> str:
    """Return the mood tag for a music file based on its filename.

    Handles filenames like ``incompetech_fluffing_a_duck.mp3`` or
    ``pb_lofi_02.mp3`` (Pixabay fallbacks → default "upbeat").
    """
    name = (
        filename
        .replace("incompetech_", "")
        .replace(".mp3", "")
        .replace(".wav", "")
        .replace(".m4a", "")
        .replace("_", " ")
        .strip()
        .lower()
    )
    return TRACK_MOODS.get(name, "upbeat")

# ── Pixabay CDN (CC0) — fallback ───────────────────────────────────────────────
PIXABAY_FALLBACK_URLS = [
    ("pb_lofi_02",     "https://cdn.pixabay.com/audio/2022/01/18/audio_d0a13f69d2.mp3"),
    ("pb_ukulele_03",  "https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3"),
    ("pb_whimsical_06","https://cdn.pixabay.com/audio/2022/08/23/audio_d16737dc28.mp3"),
]


# ── Weekly refresh ─────────────────────────────────────────────────────────────

def _get_refresh_data() -> dict:
    if not REFRESH_FILE.exists():
        return {"last_refresh": "2000-01-01T00:00:00+00:00", "week_number": 0}
    try:
        with open(REFRESH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_refresh": "2000-01-01T00:00:00+00:00", "week_number": 0}


def _should_refresh() -> bool:
    data = _get_refresh_data()
    try:
        last = datetime.fromisoformat(data["last_refresh"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days_since = (datetime.now(timezone.utc) - last).days
        logger.info(f"Muzica last refreshed {days_since} day(s) ago (refresh la {REFRESH_DAYS} zile).")
        return days_since >= REFRESH_DAYS
    except Exception:
        return True


def _mark_refreshed(week_number: int) -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REFRESH_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "last_refresh": datetime.now(timezone.utc).isoformat(),
            "week_number": week_number,
        }, f)


def _clear_music_library() -> None:
    logger.info("Sterg melodiile vechi pentru refresh saptamanal...")
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        for f in MUSIC_DIR.glob(ext):
            f.unlink(missing_ok=True)
    USED_MUSIC_FILE.unlink(missing_ok=True)
    logger.info("Biblioteca muzicala curatata.")


# ── Incompetech downloader ─────────────────────────────────────────────────────

def _from_incompetech(count: int, week_number: int) -> list[Path]:
    """
    Descarcă melodii de pe Incompetech.
    Săptămâna N → shufflat cu seed N → primele `count` melodii diferite față de săptămâna N-1.
    """
    tracks_list = list(INCOMPETECH_TRACKS)
    rng = random.Random(week_number)
    rng.shuffle(tracks_list)

    downloaded: list[Path] = []
    for name in tracks_list:
        if len(downloaded) >= count:
            break
        url = INCOMPETECH_BASE + urllib.parse.quote(name) + ".mp3"
        safe = name.lower().replace(" ", "_").replace("-", "_")
        dest = MUSIC_DIR / f"incompetech_{safe}.mp3"
        if dest.exists():
            logger.info(f"  Deja existent: {dest.name}")
            downloaded.append(dest)
            continue
        logger.info(f"  Incompetech: '{name}'")
        if _download_file(url, dest):
            downloaded.append(dest)

    return downloaded


# ── Pixabay CDN fallback ───────────────────────────────────────────────────────

def _from_pixabay_cdn(count: int) -> list[Path]:
    tracks: list[Path] = []
    for name, url in PIXABAY_FALLBACK_URLS:
        if len(tracks) >= count:
            break
        dest = MUSIC_DIR / f"{name}.mp3"
        if dest.exists():
            tracks.append(dest)
            continue
        logger.info(f"  Pixabay CDN: {name}")
        if _download_file(url, dest):
            tracks.append(dest)
    return tracks


# ── Downloader ─────────────────────────────────────────────────────────────────

def _download_file(url: str, dest: Path, verify_ssl: bool = True) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30,
                         stream=True, verify=verify_ssl, allow_redirects=True)
        if r.status_code != 200:
            logger.debug(f"HTTP {r.status_code} pentru {url}")
            return False
        ct = r.headers.get("content-type", "")
        if "text/html" in ct or "xml" in ct:
            logger.debug(f"Raspuns HTML/XML in loc de audio: {url}")
            return False

        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

        size_kb = dest.stat().st_size // 1024
        if size_kb < 50:
            dest.unlink(missing_ok=True)
            logger.debug(f"Fisier prea mic ({size_kb}KB), ignorat: {url}")
            return False

        logger.info(f"  -> Descarcat: {dest.name} ({size_kb} KB)")
        return True

    except Exception as e:
        logger.warning(f"Download esuat pentru {url}: {e}")
        dest.unlink(missing_ok=True)
        return False


# ── Public interface ───────────────────────────────────────────────────────────

def download_music(count: int = 15, force_refresh: bool = False) -> list[Path]:
    """
    Descarcă `count` melodii royalty-free în music/.
    La fiecare 7 zile șterge melodiile vechi și descarcă altele noi (rotație săptămânală).
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    data = _get_refresh_data()
    week_number = data.get("week_number", 0)

    if force_refresh or _should_refresh():
        _clear_music_library()
        week_number += 1
        _mark_refreshed(week_number)
        logger.info(f"Refresh saptamanal — saptamana #{week_number}, set nou de melodii.")

    already = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if len(already) >= count:
        logger.info(f"music/ are deja {len(already)} melodii — skip download.")
        return already

    needed = count - len(already)
    tracks: list[Path] = list(already)
    logger.info(f"Descarc {needed} melodii noi (saptamana #{week_number})...")

    if needed > 0:
        new = _from_incompetech(needed, week_number)
        tracks.extend(new)
        needed -= len(new)

    if needed > 0:
        logger.info("Fallback: Pixabay CDN...")
        new = _from_pixabay_cdn(needed)
        tracks.extend(new)

    logger.info(f"Total melodii in music/: {len(tracks)}")
    return tracks


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    tracks = download_music(count=15, force_refresh=True)
    print(f"\n{len(tracks)} melodii in music/:")
    for t in tracks:
        print(f"  {t.name} ({t.stat().st_size // 1024} KB)")
