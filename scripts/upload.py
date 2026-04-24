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

# ── Per-animal pinned comment polls ──────────────────────────────────────────
_ANIMAL_COMMENTS = {
    "cat":
        "🐱 Would you adopt this cat?\n"
        "1️⃣ Yes, immediately\n"
        "2️⃣ Already have 3 cats\n"
        "3️⃣ Just here for the vibes\n"
        "👇 Drop your number!\n\nFollow for daily cute animals 🔔",
    "dog":
        "🐶 Does this dog deserve ALL the treats?\n"
        "1️⃣ Obviously yes\n"
        "2️⃣ Give it the whole bakery\n"
        "3️⃣ I'd share my lunch\n"
        "👇 Comment your answer!\n\nFollow for daily cute animals 🔔",
    "capybara":
        "🦫 Capybaras are...\n"
        "1️⃣ The chillest animals alive\n"
        "2️⃣ My spirit animal\n"
        "3️⃣ Literally me on weekends\n"
        "👇 Which one are you?\n\nFollow for daily cute animals 🔔",
    "panda":
        "🐼 This panda is...\n"
        "1️⃣ Too cute to handle\n"
        "2️⃣ My new favourite animal\n"
        "3️⃣ Literally a stuffed toy come to life\n"
        "👇 Drop your number!\n\nFollow for daily cute animals 🔔",
    "bunny":
        "🐰 On a scale of 1–3, how adorable is this bunny?\n"
        "1️⃣ Very adorable\n"
        "2️⃣ Dangerously adorable\n"
        "3️⃣ Illegal levels of cuteness\n"
        "👇 Comment below!\n\nFollow for daily cute animals 🔔",
    "fox":
        "🦊 This fox is...\n"
        "1️⃣ Too clever and too cute\n"
        "2️⃣ My favourite animated character now\n"
        "3️⃣ Living rent-free in my head\n"
        "👇 Which one?\n\nFollow for daily cute animals 🔔",
    "bear":
        "🐻 If this bear showed up at your door...\n"
        "1️⃣ Instant best friends\n"
        "2️⃣ I'd share my honey\n"
        "3️⃣ We're going on adventures\n"
        "👇 Drop your number!\n\nFollow for daily cute animals 🔔",
    "penguin":
        "🐧 This penguin is waddling into your heart...\n"
        "1️⃣ Already adopted it mentally\n"
        "2️⃣ It's my new screensaver\n"
        "3️⃣ I need 10 of them\n"
        "👇 Comment below!\n\nFollow for daily cute animals 🔔",
    "koala":
        "🐨 This koala is...\n"
        "1️⃣ Living my dream life\n"
        "2️⃣ Too sleepy and too cute\n"
        "3️⃣ My sleep goals honestly\n"
        "👇 Which one are you?\n\nFollow for daily cute animals 🔔",
    "frog":
        "🐸 This frog is...\n"
        "1️⃣ Unexpectedly adorable\n"
        "2️⃣ The hero we needed\n"
        "3️⃣ My new favourite character\n"
        "👇 Drop your number!\n\nFollow for daily cute animals 🔔",
    "duck":
        "🦆 This duck walked into your life and...\n"
        "1️⃣ Immediately became my best friend\n"
        "2️⃣ I'd follow it anywhere\n"
        "3️⃣ It's the cutest thing I've seen today\n"
        "👇 Comment below!\n\nFollow for daily cute animals 🔔",
    "chick":
        "🐣 This tiny chick is...\n"
        "1️⃣ Dangerously cute\n"
        "2️⃣ Too small, too perfect\n"
        "3️⃣ My heart can't handle it\n"
        "👇 Which one?\n\nFollow for daily cute animals 🔔",
    "lamb":
        "🐑 This lamb is...\n"
        "1️⃣ The fluffiest thing I've ever seen\n"
        "2️⃣ Absolutely precious\n"
        "3️⃣ My instant mood booster\n"
        "👇 Drop your number!\n\nFollow for daily cute animals 🔔",
}
_DEFAULT_COMMENT = (
    "🥹 Which cute animal should we animate next?\n"
    "1️⃣ Cat\n2️⃣ Dog\n3️⃣ Capybara\n"
    "👇 Drop your vote!\n\nFollow for daily cute animals 🔔"
)

TAGS = [
    "shorts", "cute animals", "funny animals", "kawaii", "animated animals",
    "animal lovers", "cute pets", "funny video", "trending", "viral",
    "wholesome", "baby animals", "adorable", "cuteness overload",
    "fluffy animals", "animal videos", "cute animation", "heartwarming",
    "cute creature", "cozy",
]

HASHTAGS = (
    "#Shorts #CuteAnimals #FunnyAnimals #Kawaii #AnimatedAnimals #CozyVibes "
    "#Satisfying #AnimalLovers #CutePets #FunnyVideo #Trending #Viral "
    "#Wholesome #BabyAnimals #Adorable #CutenessOverload #FluffyAnimals "
    "#PetVideos #AnimalVideos #FunnyPets #CuteVideo #DailyCute #Cute "
    "#AnimalsOfYouTube #AnimalMemes #CutestAnimals #2DAnimation #Cozy "
    "#Animals #CuteAnimation #FYP #ForYou #ForYouPage #AnimalsOfTikTok "
    "#CuteShorts #AnimalShorts #FunnyShorts #KawaiiAnimals #CozyShorts "
    "#HeartWarmingVideo #FeelGood #SmileMore #HappyAnimals #TinyAnimals "
    "#CuteCreature #AnimatedCute #CuteContent #DailyAnimal #AnimalOfTheDay "
    "#CuteTok #FunnyTok #AnimalTok #CuteViral #ViralAnimals #MustWatch "
    "#CuteAlert #CuteMoment #AnimalLove #PetLovers #FurryFriends "
    "#WholesomeContent #GoodVibesOnly #MoodBooster #InstantSmile "
    "#CutestEver #TooAdorable #AnimationLovers #CartoonAnimals #DigitalArt "
    "#AnimalAnimation #CuteCharacters #FunAnimation #LovableAnimals"
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

    # Animal-specific hashtags (e.g. #Cat #CatShorts #CuteCat #CatLovers)
    animal_tag = animal.replace(" ", "")
    animal_hashtags = (
        f"#{animal_tag} #{animal_tag}Shorts #{animal_tag}Lovers "
        f"#{animal_tag}Video #{animal_tag}Life #{animal_tag}OfTheDay "
        f"#{animal_tag}TikTok #{animal_tag}Cute #{animal_tag}Funny "
        f"#Cute{animal_tag} #Funny{animal_tag} #Baby{animal_tag}"
    )

    description = (
        f"A little {animal.lower()} moment to brighten your day \U0001f338\n\n"
        f"\U0001f50a Turn on sound for the full experience!\n"
        f"\u2728 Subscribe for daily cute animal animations! \U0001f514\n"
        f"\U0001f514 New cute video every single day!\n\n"
        f"{HASHTAGS}\n"
        f"{animal_hashtags}"
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
            "defaultAudioLanguage": "en",
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
            animal = prompt_entry.get("animal", "") if prompt_entry else ""
            _record_upload(video_id, title, str(video_path), animal=animal)

            # ── Post-upload actions (non-fatal) ──────────────────────────
            _auto_like(youtube, video_id)
            _post_comment(youtube, video_id, animal)
            if animal:
                from scripts.playlists import add_video_to_animal_playlist
                add_video_to_animal_playlist(youtube, video_id, animal)

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


def _auto_like(youtube, video_id: str) -> None:
    """Like the video with the channel owner account — small engagement signal."""
    try:
        youtube.videos().rate(id=video_id, rating="like").execute()
        logger.info(f"Auto-liked: {video_id}")
    except HttpError as e:
        logger.warning(f"Could not auto-like: {e}")


def _post_comment(youtube, video_id: str, animal: str) -> None:
    """Post a pinned poll comment to drive replies and watch time."""
    text = _ANIMAL_COMMENTS.get(animal, _DEFAULT_COMMENT)
    try:
        resp = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": text}
                    },
                }
            },
        ).execute()
        logger.info(f"Comment posted: {resp['snippet']['topLevelComment']['id']}")
    except HttpError as e:
        logger.warning(f"Could not post comment: {e}")


def _record_upload(video_id: str, title: str, video_path: str, animal: str = "") -> None:
    if UPLOADED_FILE.exists():
        with open(UPLOADED_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"uploads": []}

    data["uploads"].append({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "video_id": video_id,
        "title": title,
        "animal": animal,
        "file": video_path,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    })

    with open(UPLOADED_FILE, "w", encoding="utf-8") as f:
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
