"""
download_music.py — Descarcă melodii royalty-free în music/.

Surse (în ordine):
  1. Pixabay API  — dacă PIXABAY_API_KEY e setat în .env
  2. ccMixter API — gratuit, fără cheie, CC-BY license
  3. Pixabay CDN  — URL-uri directe testate (fallback final)
"""

import logging
import sys
import time
import warnings
from pathlib import Path

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

MUSIC_DIR = config.MUSIC_DIR
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

# ── Surse ──────────────────────────────────────────────────────────────────────

# ccMixter tags care se potrivesc pentru cute animal content
CCMIXTER_QUERIES = [
    "ukulele",
    "lofi",
    "cute",
    "cheerful",
    "acoustic+guitar",
    "playful",
    "upbeat",
    "happy",
    "kawaii",
]

# Pixabay CDN URLs testate manual (fallback, CC0)
PIXABAY_FALLBACK_URLS = [
    ("pixabay_kawaii_01", "https://cdn.pixabay.com/audio/2021/08/04/audio_0625c1539c.mp3"),
    ("pixabay_upbeat_02", "https://cdn.pixabay.com/audio/2022/08/02/audio_884fe92c21.mp3"),
]


# ── Pixabay API ────────────────────────────────────────────────────────────────

def _from_pixabay(count: int) -> list[Path]:
    import os
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        logger.info("PIXABAY_API_KEY nu e setat — skip Pixabay API.")
        return []

    tracks: list[Path] = []
    queries = ["ukulele cute", "kawaii lofi", "upbeat cheerful", "acoustic happy"]

    for q in queries:
        if len(tracks) >= count:
            break
        try:
            r = requests.get(
                "https://pixabay.com/api/",
                params={
                    "key": api_key, "q": q,
                    "media_type": "music", "per_page": 5,
                },
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
                if _download_file(url, dest):
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

            # ccMixter trimite text/plain — trebuie json.loads fortat
            import json as _json
            try:
                items = _json.loads(r.text)
            except Exception:
                continue

            for item in items:
                if len(tracks) >= count:
                    break

                # Filtram licente non-comerciale (NC) — nu sunt permise pe YouTube monetizat
                license_name = item.get("license_name", "")
                if "Noncommercial" in license_name or "NonCommercial" in license_name:
                    logger.debug(f"Skip NC track: {item.get('upload_name')} ({license_name})")
                    continue

                # Gasim URL-ul de download din files[]
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
                dest = MUSIC_DIR / f"ccmixter_{safe_name[:35].strip()}_{len(tracks)+1:02d}.mp3"

                logger.info(f"  ccMixter: '{track_name}' by {user} [{license_name}]")
                if _download_file(dl_url, dest, verify_ssl=False):
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

        total = int(r.headers.get("content-length", 0))
        with open(dest, "wb") as f:
            downloaded = 0
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)

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

def download_music(count: int = 5) -> list[Path]:
    """
    Descarcă `count` melodii royalty-free în music/.
    Returnează lista de Path-uri descărcate.
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    already = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if len(already) >= count:
        logger.info(f"music/ are deja {len(already)} melodii — skip download.")
        return already[:count]

    needed = count - len(already)
    tracks: list[Path] = list(already)

    logger.info(f"Descarc {needed} melodii noi (din {count} necesare)...")

    # 1. Pixabay API (dacă key disponibil)
    if needed > 0:
        new = _from_pixabay(needed)
        tracks.extend(new)
        needed -= len(new)

    # 2. ccMixter
    if needed > 0:
        logger.info("Sursa: ccMixter API (CC-BY, fara cheie)...")
        new = _from_ccmixter(needed)
        tracks.extend(new)
        needed -= len(new)

    # 3. Pixabay CDN fallback
    if needed > 0:
        logger.info("Sursa: Pixabay CDN (fallback)...")
        new = _from_pixabay_cdn(needed)
        tracks.extend(new)

    logger.info(f"Total melodii in music/: {len(tracks)}")
    return tracks


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    tracks = download_music(count=5)
    print(f"\n{len(tracks)} melodii in music/:")
    for t in tracks:
        print(f"  {t.name} ({t.stat().st_size // 1024} KB)")
