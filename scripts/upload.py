"""
upload.py — Upload a finished Short to YouTube via Data API v3.

Auth flow:
  - Prefers YOUTUBE_REFRESH_TOKEN from .env (non-interactive, for servers).
  - Falls back to youtube_token.json (written by get_youtube_token.py).
  - Last resort: OAuth2 browser flow (only works locally).

Upload is scheduled for UPLOAD_HOUR:UPLOAD_MINUTE in UPLOAD_TIMEZONE.
After upload, video_id is saved to logs/uploaded.json.
"""

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth.transport.requests
import google.oauth2.credentials
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",   # upload
    "https://www.googleapis.com/auth/youtube.force-ssl", # delete + channel read
]
UPLOADED_FILE = config.LOGS_DIR / "uploaded.json"
PROMPTS_FILE = config.PROMPTS_DIR / "animal_prompts.json"

# YouTube category ID 15 = Pets & Animals
CATEGORY_ID = "15"

TAGS = [
    "shorts", "cute", "animals", "cuteanimals", "funnyanimals",
    "kawaii", "2danimation", "animatedanimals", "cuteanimals2024",
    "cozy", "satisfying", "funnyanimals2024", "animalshorts",
]

HASHTAGS = (
    "#shorts #cute #animals #cuteanimals #funnyanimals "
    "#kawaii #2danimation #animatedanimals #cozy #satisfying "
    "#animallovers #cutepets #funnyvideo #trending"
)


# ── Auth ───────────────────────────────────────────────────────────────────────

def _credentials_from_refresh_token() -> Credentials | None:
    """Build credentials from YOUTUBE_REFRESH_TOKEN in .env."""
    if not config.YOUTUBE_REFRESH_TOKEN:
        return None

    # Try client_secrets.json first, then fall back to env vars
    secrets_path = Path(config.YOUTUBE_CLIENT_SECRETS)
    if secrets_path.exists():
        with open(secrets_path) as f:
            secrets = json.load(f)
        web_or_installed = secrets.get("web") or secrets.get("installed") or {}
        client_id = web_or_installed.get("client_id", "")
        client_secret = web_or_installed.get("client_secret", "")
        token_uri = web_or_installed.get("token_uri", "https://oauth2.googleapis.com/token")
    else:
        # Railway / server mode: read from env vars
        import os
        client_id = os.getenv("YOUTUBE_CLIENT_ID", "")
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
        token_uri = "https://oauth2.googleapis.com/token"
        if not client_id or not client_secret:
            logger.warning("client_secrets.json not found and YOUTUBE_CLIENT_ID/SECRET not set")
            return None

    creds = Credentials(
        token=None,
        refresh_token=config.YOUTUBE_REFRESH_TOKEN,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return creds


def _credentials_from_token_file() -> Credentials | None:
    """Load saved credentials from youtube_token.json."""
    token_path = Path(config.YOUTUBE_TOKEN_FILE)
    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds if creds and creds.valid else None


def _credentials_from_oauth_flow() -> Credentials:
    """Run browser-based OAuth2 flow. Only works interactively."""
    secrets_path = Path(config.YOUTUBE_CLIENT_SECRETS)
    if not secrets_path.exists():
        raise FileNotFoundError(f"client_secrets.json not found: {secrets_path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path = Path(config.YOUTUBE_TOKEN_FILE)
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    logger.info(f"Token saved to {token_path}")
    return creds


def get_youtube_client():
    """Return an authenticated YouTube API client."""
    creds = None

    # Priority 1: refresh token from env (best for CI/server)
    try:
        creds = _credentials_from_refresh_token()
        if creds:
            logger.info("Authenticated via YOUTUBE_REFRESH_TOKEN")
    except Exception as e:
        logger.warning(f"Refresh token auth failed: {e}")

    # Priority 2: saved token file
    if not creds:
        try:
            creds = _credentials_from_token_file()
            if creds:
                logger.info("Authenticated via youtube_token.json")
        except Exception as e:
            logger.warning(f"Token file auth failed: {e}")

    # Priority 3: interactive OAuth flow
    if not creds:
        logger.info("Starting interactive OAuth2 flow…")
        creds = _credentials_from_oauth_flow()

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


# ── Title / description generation ────────────────────────────────────────────

def _load_prompt_data() -> dict:
    with open(PROMPTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def generate_metadata(prompt_entry: dict | None = None) -> tuple[str, str]:
    """Return (title, description) for the upload."""
    import random
    data = _load_prompt_data()

    if prompt_entry is None:
        prompt_entry = random.choice(data["prompts"])

    animal = prompt_entry["animal"].title()
    action = prompt_entry["action"].replace("_", " ")
    templates = data.get("title_templates", [])

    if templates:
        template = random.choice(templates)
        title = template.replace("{animal}", animal).replace("{action}", action)
    else:
        title = f"This cute {animal} {action} will make your day 🥹 #shorts"

    # Ensure title ≤ 100 chars (YouTube limit)
    if len(title) > 97:
        title = title[:97] + "…"

    description = (
        f"A little {animal.lower()} moment to brighten your day 🌸\n\n"
        f"{HASHTAGS}\n\n"
        "✨ Subscribe for daily cute animal animations!\n"
        "🔔 Turn on notifications so you never miss one."
    )

    return title, description


# ── Upload ─────────────────────────────────────────────────────────────────────

def wait_until_upload_time() -> None:
    """Block until the configured upload time (if it's still in the future today)."""
    tz = ZoneInfo(config.UPLOAD_TIMEZONE)
    now = datetime.now(tz)
    target = now.replace(
        hour=config.UPLOAD_HOUR,
        minute=config.UPLOAD_MINUTE,
        second=0,
        microsecond=0,
    )
    if target > now:
        wait_secs = (target - now).total_seconds()
        logger.info(
            f"Waiting {wait_secs / 60:.1f} min until "
            f"{config.UPLOAD_HOUR:02d}:{config.UPLOAD_MINUTE:02d} "
            f"{config.UPLOAD_TIMEZONE}…"
        )
        time.sleep(wait_secs)
    else:
        logger.info("Upload time already passed — uploading now.")


def verify_channel(youtube) -> tuple[str, str]:
    """
    Verifică canalul activ și îl compară cu YOUTUBE_CHANNEL_ID din config.
    Returnează (channel_id, channel_name).
    Oprește execuția dacă canalul nu coincide cu cel configurat.
    """
    resp = youtube.channels().list(part="snippet,id", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError("Nu s-a găsit niciun canal YouTube pe acest cont.")

    channel_id   = items[0]["id"]
    channel_name = items[0]["snippet"]["title"]
    logger.info(f"Canal activ: {channel_name} (ID: {channel_id})")

    expected = config.YOUTUBE_CHANNEL_ID
    if expected and expected != channel_id:
        raise RuntimeError(
            f"CANAL GRESIT! Autentificat pe '{channel_name}' ({channel_id}), "
            f"dar se asteapta canalul cu ID '{expected}'. "
            f"Re-ruleaza get_youtube_token.py si selecteaza canalul corect."
        )

    return channel_id, channel_name


def upload_short(
    video_path: Path,
    prompt_entry: dict | None = None,
    wait_for_schedule: bool = True,
) -> str:
    """
    Upload video_path as a YouTube Short.

    Returns the YouTube video ID.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    title, description = generate_metadata(prompt_entry)
    logger.info(f"Title: {title}")

    if wait_for_schedule:
        wait_until_upload_time()

    youtube = get_youtube_client()

    # Verificare canal INAINTE de upload — previne postarea pe canalul gresit
    channel_id, channel_name = verify_channel(youtube)
    logger.info(f"Upload pe: {channel_name}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": TAGS,
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=256 * 1024,
    )

    logger.info(f"Uploading {video_path.name}…")

    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            request = youtube.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"Upload progress: {pct}%")

            video_id = response["id"]
            logger.info(f"Upload complete! Video ID: {video_id}")
            _record_upload(video_id, title, str(video_path))
            return video_id

        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"HTTP {e.resp.status} — retrying in {wait}s…")
                time.sleep(wait)
            else:
                raise


def upload_compilation(video_path: Path, month_str: str, youtube=None) -> str:
    """Upload a monthly Best Of compilation. Returns video_id."""
    if not video_path.exists():
        raise FileNotFoundError(f"Compilation video not found: {video_path}")

    if youtube is None:
        youtube = get_youtube_client()

    title = f"Best of {month_str} \U0001f43e | CuteDaily Compilation"
    if len(title) > 100:
        title = title[:97] + "\u2026"

    description = (
        f"\U0001f43e Best of {month_str} \u2014 cutest animals of the month!\n\n"
        "Our most-watched cute animal animations all in one place \U0001f495\n\n"
        "#cuteanimals #compilation #shorts #kawaii #animatedanimals "
        "#cuteanimals2024 #bestof #funny\n\n"
        "\u2728 Subscribe for daily cute animal animations!\n"
        "\U0001f514 Turn on notifications so you never miss one."
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["best of", "compilation", "cute animals", "shorts",
                     "kawaii", "animated animals", month_str.lower()],
            "categoryId": "15",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
            "madeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path), mimetype="video/mp4", resumable=True, chunksize=256 * 1024
    )

    logger.info(f"Uploading compilation: {title}")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"Compilation upload: {int(status.progress() * 100)}%")

    video_id = response["id"]
    logger.info(f"Compilation uploaded: https://www.youtube.com/watch?v={video_id}")
    return video_id


def _record_upload(video_id: str, title: str, video_path: str) -> None:
    if UPLOADED_FILE.exists():
        with open(UPLOADED_FILE) as f:
            data = json.load(f)
    else:
        data = {"uploads": []}

    data["uploads"].append({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "video_id": video_id,
        "title": title,
        "file": video_path,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    })

    with open(UPLOADED_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Upload recorded in {UPLOADED_FILE}")


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Find the most recent Short
    shorts = sorted(config.SHORTS_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not shorts:
        print("No Short found in shorts/ — run assemble.py first.")
        sys.exit(1)

    latest = shorts[-1]
    print(f"Uploading: {latest.name}")
    vid_id = upload_short(latest, wait_for_schedule=False)
    print(f"Done! https://www.youtube.com/watch?v={vid_id}")
