"""
download_music.py — Descarcă melodii royalty-free în music/.

Surse (în ordine):
  1. Pixabay API  — dacă PIXABAY_API_KEY e setat în .env
  2. ccMixter API — gratuit, fără cheie, CC-BY license
  3. Pixabay CDN  — URL-uri directe testate (fallback final)

Refresh săptămânal: la fiecare 7 zile șterge melodiile vechi și descarcă altele noi.
"""

import json
import logging
import sys
import time
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

# ── Surse ──────────────────────────────────────────────────────────────────────

PIXABAY_QUERIES = [
    "cute ukulele",
    "kawaii lofi",
    "upbeat cheerful",
    "acoustic happy",
    "playful piano",
    "fun children music",
    "whimsical background",
    "lighthearted instrumental",
    "positive uplifting",
    "cute animals music",
]

CCMIXTER_QUERIES = [
    "ukulele",
    "lofi",
    "cheerful",
    "acoustic+guitar",
    "playful",
    "upbeat",
    "happy",
    "whimsical",
    "fun",
    "instrumental",
    "light",
    "carefree",
]

# Pixabay CDN URLs testate manual (CC0) — fallback diversificat
PIXABAY_FALLBACK_URLS = [
    ("pb_cute_01", "https://cdn.pixabay.com/audio/2022/03/10/audio_270f49d21d.mp3"),
    ("pb_lofi_02", "https://cdn.pixabay.com/audio/2022/01/18/audio_d0a13f69d2.mp3"),
    ("pb_ukulele_03", "https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3"),
    ("pb_happy_04", "https://cdn.pixabay.com/audio/2021/11/25/audio_91b32e02de.mp3"),
    ("pb_playful_05", "https://cdn.pixabay.com/audio/2022/10/25/audio_946b365bec.mp3"),
    ("pb_whimsical_06", "https://cdn.pixabay.com/audio/2022/08/23/audio_d16737dc28.mp3"),
    ("pb_acoustic_07", "https://cdn.pixabay.com/audio/2022/03/15/audio_8cb749b05d.mp3"),
    ("pb_cheerful_08", "https://cdn.pixabay.com/audio/2022/06/08/audio_c8b8e83d76.mp3"),
]


# ── Weekly refresh ─────────────────────────────────────────────────────────────

def _should_refresh() -> bool:
    if not REFRESH_FILE.exists():
        return True
    try:
        with open(REFRESH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        last = datetime.fromisoformat(data.get("last_refresh", "2000-01-01T00:00:00+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days_since = (datetime.now(timezone.utc) - last).days
        logger.info(f"Music last refreshed {days_since} day(s) ago.")
        return days_since >= REFRESH_DAYS
    except Exception:
        return True


def _mark_refreshed() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REFRESH_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_refresh": datetime.now(timezone.utc).isoformat()}, f)


def _clear_music_library() -> None:
    logger.info("Sterg melodiile vechi pentru refresh saptamanal...")
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        for f in MUSIC_DIR.glob(ext):
            f.unlink(missing_ok=True)
    USED_MUSIC_FILE.unlink(missing_ok=True)
    logger.info("Biblioteca muzicala curatata.")


# ── Pixabay API ────────────────────────────────────────────────────────────────

def _from_pixabay(count: int) -> list[Path]:
    import os
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        logger.info("PIXABAY_API_KEY nu e setat — skip Pixabay API.")
        return []

    tracks: list[Path] = []
    for q in PIXABAY_QUERIES:
        if len(tracks) >= count:
            break
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={"key": api_key, "q": q, "media_type": "music", "per_page": 5},
                headers=HEADERS, timeout=15,
            )
            hits = r.json().get("hits", [])
            for hit in hits:
                if len(tracks) >= count:
                    break
                url = hit.get("audio", hit.get("previewURL", ""))
                if not url:
                    continue
                name = q.replace(" ", "_") + f"_{len(tracks)+1:02d}.mp3"
                dest = MUSIC_DIR / name
                if not dest.exists() and _download_file(url, dest):
                    tracks.append(dest)
        except Exception as e:
            logger.warning(f"Pixabay API eroare pentru '{q}': {e}")

    return tracks


# ── ccMixter API ───────────────────────────────────────────────────────────────

def _from_ccmixter(count: int) -> list[Path]:
    tracks: list[Path] = []
    seen_urls: set[str] = set()

    for tag in CCMIXTER_QUERIES:
        if len(tracks) >= count:
            break
        try:
            r = requests.get(
                "https://ccmixter.org/api/query",
                params={"tags": tag, "limit": 8, "f": "json"},
                headers=HEADERS, timeout=12, verify=False,
            )
            if r.status_code != 200:
                continue

            import json as _json
            try:
                items = _json.loads(r.text)
            except Exception:
                continue

            for item in items:
                if len(tracks) >= count:
                    break

                license_name = item.get("license_name", "")
                if "Noncommercial" in license_name or "NonCommercial" in license_name:
                    continue

                files = item.get("files", [])
                dl_url = ""
                for f in files:
                    u = f.get("download_url", "")
                    if u.lower().endswith(".mp3"):
                        dl_url = u
                        break

                if not dl_url or dl_url in seen_urls:
                    continue
                seen_urls.add(dl_url)

                track_name = item.get("upload_name", f"track_{len(tracks)+1}")
                user = item.get("user_name", "unknown")
                safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in track_name)
                dest = MUSIC_DIR / f"ccm_{safe_name[:30].strip()}_{len(tracks)+1:02d}.mp3"

                logger.info(f"  ccMixter: '{track_name}' by {user} [{license_name}]")
                if not dest.exists() and _download_file(dl_url, dest, verify_ssl=False):
                    tracks.append(dest)
                    time.sleep(0.5)

        except Exception as e:
            logger.warning(f"ccMixter eroare pentru tag '{tag}': {e}")

    return tracks


# ── Pixabay CDN fallback ───────────────────────────────────────────────────────

def _from_pixabay_cdn(count: int) -> list[Path]:
    tracks: list[Path] = []
    for name, url in PIXABAY_FALLBACK_URLS:
        if len(tracks) >= count:
            break
        dest = MUSIC_DIR / f"{name}.mp3"
        if dest.exists():
            logger.info(f"  Deja existent: {dest.name}")
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
        if size_kb < 10:
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
    La fiecare 7 zile șterge melodiile vechi și descarcă altele noi.
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    if force_refresh or _should_refresh():
        _clear_music_library()
        _mark_refreshed()

    already = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if len(already) >= count:
        logger.info(f"music/ are deja {len(already)} melodii — skip download.")
        return already

    needed = count - len(already)
    tracks: list[Path] = list(already)
    logger.info(f"Descarc {needed} melodii noi (din {count} necesare)...")

    if needed > 0:
        new = _from_pixabay(needed)
        tracks.extend(new)
        needed -= len(new)

    if needed > 0:
        logger.info("Sursa: ccMixter API...")
        new = _from_ccmixter(needed)
        tracks.extend(new)
        needed -= len(new)

    if needed > 0:
        logger.info("Sursa: Pixabay CDN (fallback)...")
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
