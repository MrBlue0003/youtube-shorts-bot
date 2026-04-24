"""playlists.py — Auto-manage YouTube playlists for CuteDaily.

Creates one playlist per animal type (lazily) and adds every uploaded Short to it.
Playlist IDs are cached in data/playlists.json so we never create duplicates.
"""
import json
import logging
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

DATA_DIR       = config.BASE_DIR / "data"
PLAYLISTS_FILE = DATA_DIR / "playlists.json"
DATA_DIR.mkdir(exist_ok=True)

# ── Per-animal playlist metadata ──────────────────────────────────────────────
ANIMAL_PLAYLIST_META = {
    "cat":      {"title": "🐱 Cute Cat Videos | CuteDaily",      "description": "Daily cute 2D animated cat videos — kawaii, cozy, and wholesome."},
    "dog":      {"title": "🐶 Cute Dog Videos | CuteDaily",       "description": "Daily cute 2D animated dog videos — funny, adorable, and heartwarming."},
    "capybara": {"title": "🦫 Cute Capybara Videos | CuteDaily",  "description": "The most relaxed animal on the internet, animated in kawaii style."},
    "panda":    {"title": "🐼 Cute Panda Videos | CuteDaily",     "description": "Daily cute 2D animated panda videos — fluffy, cozy, and adorable."},
    "bunny":    {"title": "🐰 Cute Bunny Videos | CuteDaily",     "description": "Daily cute 2D animated bunny videos — soft, sweet, and kawaii."},
    "fox":      {"title": "🦊 Cute Fox Videos | CuteDaily",       "description": "Daily cute 2D animated fox videos — clever, cozy, and charming."},
    "bear":     {"title": "🐻 Cute Bear Videos | CuteDaily",      "description": "Daily cute 2D animated bear videos — big, fluffy, and lovable."},
    "penguin":  {"title": "🐧 Cute Penguin Videos | CuteDaily",   "description": "Daily cute 2D animated penguin videos — waddling into your heart."},
    "koala":    {"title": "🐨 Cute Koala Videos | CuteDaily",     "description": "Daily cute 2D animated koala videos — sleepy, soft, and precious."},
    "frog":     {"title": "🐸 Cute Frog Videos | CuteDaily",      "description": "Daily cute 2D animated frog videos — tiny, funny, and delightful."},
    "duck":     {"title": "🦆 Cute Duck Videos | CuteDaily",      "description": "Daily cute 2D animated duck videos — quacking their way into your day."},
    "chick":    {"title": "🐣 Cute Chick Videos | CuteDaily",     "description": "Daily cute 2D animated chick videos — tiny, fluffy, and heartwarming."},
    "lamb":     {"title": "🐑 Cute Lamb Videos | CuteDaily",      "description": "Daily cute 2D animated lamb videos — soft, sweet, and utterly adorable."},
}


def _load_cache() -> dict:
    if PLAYLISTS_FILE.exists():
        with open(PLAYLISTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"playlists": {}}


def _save_cache(data: dict) -> None:
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_or_create_playlist(youtube, animal: str) -> str | None:
    """Return YouTube playlist ID for the animal. Creates it if it doesn't exist."""
    cache     = _load_cache()
    playlists = cache.get("playlists", {})

    if animal in playlists:
        return playlists[animal]

    meta = ANIMAL_PLAYLIST_META.get(animal)
    if not meta:
        logger.warning(f"No playlist metadata for animal '{animal}'")
        return None

    try:
        resp = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title":           meta["title"],
                    "description":     meta["description"],
                    "defaultLanguage": "en",
                },
                "status": {"privacyStatus": "public"},
            },
        ).execute()
        playlist_id = resp["id"]
        logger.info(f"Created playlist '{meta['title']}' → {playlist_id}")

        playlists[animal] = playlist_id
        cache["playlists"] = playlists
        _save_cache(cache)
        return playlist_id

    except HttpError as e:
        logger.error(f"Failed to create playlist for '{animal}': {e}")
        return None


def add_to_playlist(youtube, video_id: str, playlist_id: str) -> bool:
    """Add a video to a playlist. Returns True on success."""
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        logger.info(f"Added {video_id} to playlist {playlist_id}")
        return True
    except HttpError as e:
        logger.error(f"Failed to add {video_id} to playlist {playlist_id}: {e}")
        return False


def add_video_to_animal_playlist(youtube, video_id: str, animal: str) -> None:
    """High-level: get/create the animal playlist and add the video."""
    playlist_id = get_or_create_playlist(youtube, animal)
    if playlist_id:
        add_to_playlist(youtube, video_id, playlist_id)
