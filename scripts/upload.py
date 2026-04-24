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

# ── Per-action viral title pool ───────────────────────────────────────────────
# Takes priority over generic title_templates for better clickability.
# {animal} is replaced with the capitalised animal name.
_ACTION_TITLES: dict[str, list[str]] = {
    "cooking": [
        "This {animal} cooking is the most wholesome thing today 🍳",
        "POV: Your {animal} decided to become a chef 👨‍🍳",
        "Nobody talks about how cute {animal}s look while cooking 😭",
        "This {animal} chef will make you smile instantly 🌸",
    ],
    "dancing": [
        "This {animal} dancing will fix your mood instantly 💃",
        "POV: This {animal} hears its favourite song 🎵",
        "The way this {animal} dances though... 😭✨",
        "I need everyone to see this dancing {animal} 🕺",
    ],
    "cozy_sleep": [
        "The most cozy {animal} nap you'll see today 😴",
        "POV: Being this {animal} sleeping right now 🌙",
        "This sleeping {animal} has healed my soul 🥹",
        "Goodnight from this precious {animal} 💤",
    ],
    "little_treat": [
        "This {animal} getting a little treat is everything 🍰",
        "POV: You surprise your {animal} with their favourite treat 🎁",
        "The way this {animal} reacts to treats 😭💕",
        "This {animal} deserves every single treat 🌸",
    ],
    "exaggerated_reaction": [
        "This {animal}'s reaction will make your day 😂",
        "POV: This {animal} just received surprising news 😳",
        "The drama of this {animal} is unmatched 😭✨",
        "Nobody reacts like this {animal} 💀",
    ],
    "cozy": [
        "Living vicariously through this cozy {animal} 🌙",
        "POV: Being this {animal} on a rainy day ☔",
        "This {animal}'s cozy energy is all I need 🥺",
        "The coziest {animal} you'll see today 🍵",
    ],
    "yoga": [
        "This {animal} doing yoga is my entire personality 🧘",
        "POV: Your {animal} found inner peace 🌸",
        "Zen {animal} unlocked 🙏 watch till the end",
        "This {animal} is more flexible than me 😭",
    ],
    "reading": [
        "This bookworm {animal} is my spirit animal 📚",
        "POV: This {animal} discovered the joy of reading 🌙",
        "The way this {animal} reads... 😭💕",
        "Intellectual {animal} hours 📖✨",
    ],
    "gardening": [
        "This {animal} gardener will make your whole day 🌱",
        "POV: This {animal} has a green thumb 🌸",
        "The cutest little gardener in the world 🌻",
        "This {animal} growing flowers is everything 🥹",
    ],
    "baking": [
        "This {animal} baker will warm your heart instantly 🧁",
        "POV: Your {animal} decided to start baking 🍞",
        "The most wholesome {animal} baking video ever 🥹",
        "This {animal} and its bakery obsession 😭🌸",
    ],
    "painting": [
        "This artistic {animal} is everything 🎨",
        "POV: This {animal} discovered the joy of painting 🖌️",
        "The way this {animal} paints... 🥹✨",
        "Artist {animal} creating magic today 🌸",
    ],
    "playing": [
        "This playful {animal} is too cute to handle 🥹",
        "POV: Watching your {animal} play all day 🌸",
        "The energy of this {animal} playing 😭✨",
        "This {animal} playing will make your day instantly 💕",
    ],
    "stargazing": [
        "This {animal} watching the stars will heal you 🌟",
        "POV: You and your {animal} stargazing tonight 🌙",
        "This dreamy {animal} is my whole personality ✨",
        "Sending you this precious {animal} under the stars 🥺",
    ],
    "spring_window": [
        "This {animal} watching spring rain is so soothing 🌸",
        "POV: You're this {animal} on a cozy spring afternoon ☔",
        "The most calming {animal} spring moment 🌷",
        "This {animal} and the spring rain — instant peace 🌧️",
    ],
    "spring_walk": [
        "This {animal} running through spring flowers 🌸",
        "POV: First warm spring day with your {animal} 🌷",
        "The joy of this {animal} in spring is contagious 🥹",
        "Spring energy from the happiest {animal} alive 🌸✨",
    ],
    "spring_wakeup": [
        "This {animal} waking up to spring will make your day 🌸",
        "POV: You're this {animal} seeing spring for the first time 🌷",
        "The most wholesome spring moment with a {animal} 🥹",
        "This {animal} just discovered spring 🌸✨",
    ],
    "summer_beach": [
        "This {animal} at the beach is summer goals ☀️",
        "POV: Vacation with your {animal} 🏖️",
        "The most relaxed {animal} on the whole beach 🥥",
        "Summer vibes from this cool {animal} 😎",
    ],
    "summer_ice_cream": [
        "This {animal} eating ice cream is everything 🍦",
        "POV: You and your {animal} on a summer day 🍦",
        "The panic of this {animal}'s melting ice cream 😭",
        "Summer's cutest {animal} moment you'll see today 🌞",
    ],
    "autumn_harvest": [
        "This {animal} collecting autumn things is so cozy 🍂",
        "POV: Autumn adventures with your {animal} 🍁",
        "The most autumnal {animal} energy imaginable 🌿",
        "Fall in love with this {animal}'s harvest day 🍂",
    ],
    "autumn_cozy": [
        "This {animal} in autumn is pure cozy energy 🍂",
        "POV: Autumn afternoon tea with your {animal} ☕",
        "The coziest autumn {animal} moment on the internet 🍁",
        "Fall vibes from this precious {animal} 🥹",
    ],
    "winter_dance": [
        "This {animal} ice skating is the cutest thing ever ⛸️",
        "POV: Winter fun with your dancing {animal} ❄️",
        "The way this {animal} dances on ice 😭✨",
        "Winter magic from this adorable {animal} ⛸️",
    ],
    "winter_cozy": [
        "This {animal} by the fireplace will warm your heart 🔥",
        "POV: Winter night vibes with your {animal} ☕",
        "The cosiest winter {animal} moment ever 🥹",
        "Sending warmth through this precious winter {animal} ❄️",
    ],
    "birthday": [
        "This {animal}'s birthday moment is too precious 🥹🎂",
        "POV: It's your {animal}'s birthday today 🎂",
        "Happy birthday to the cutest {animal} 🥳🎉",
        "This birthday {animal} deserves all the cake 🎂💕",
    ],
}

# ── Action-specific description openers ──────────────────────────────────────
_ACTION_OPENERS: dict[str, str] = {
    "cooking":              "Chef {animal} is in the kitchen and it is absolutely adorable 🍳",
    "dancing":              "This {animal} cannot stop dancing and honestly neither can we 💃",
    "cozy_sleep":           "The most perfect {animal} nap you will see today 😴",
    "little_treat":         "Little treat for the best {animal} ever 🍰",
    "exaggerated_reaction": "This {animal}s reaction is sending us 😂",
    "cozy":                 "Pure cozy energy from this precious {animal} 🌙",
    "yoga":                 "Zen {animal} has found inner peace 🧘",
    "reading":              "Bookworm {animal} discovered their new favourite hobby 📚",
    "gardening":            "This little {animal} gardener is growing something magical 🌱",
    "baking":               "Baker {animal} just pulled something amazing out of the oven 🧁",
    "painting":             "Artist {animal} is creating todays masterpiece 🎨",
    "playing":              "Playtime with this {animal} is the best kind of therapy 🌸",
    "stargazing":           "This dreamy {animal} is watching the stars and so are we 🌟",
    "spring_window":        "This {animal} watching spring arrive is the most peaceful thing 🌸",
    "spring_walk":          "Spring joy through the eyes of the happiest {animal} alive 🌷",
    "spring_wakeup":        "First day of spring and this {animal} is living for it 🌸",
    "summer_beach":         "Beach mode ON — this {animal} is living the summer dream 🏖️",
    "summer_ice_cream":     "Race against the sun: this {animal} vs their melting ice cream 🍦",
    "autumn_harvest":       "Autumn adventures with the coziest {animal} collector 🍂",
    "autumn_cozy":          "Sweater weather tea and this precious {animal} 🍁",
    "winter_dance":         "Winter magic on ice with the most graceful {animal} ever ⛸️",
    "winter_cozy":          "Fireplace cocoa and the coziest {animal} in the world 🔥",
    "birthday":             "The most special birthday ever for our favourite little {animal} 🎂",
}
_DEFAULT_OPENER = "A little {animal} moment to brighten your whole day 🌸"

# ── Weekly rotating hashtag packs (week_number % 4) ──────────────────────────
# Provides variety without needing an external API.
_WEEKLY_HASHTAG_PACKS = [
    "#CutestAnimalEver #AnimalTok #ViralPets #TrendingAnimals #FunnyPetMoments",
    "#KawaiiAnimals #AnimatedPets #CuteAnimation #AnimalMemes #FunnyAnimalClips",
    "#WholesomeAnimals #HappyAnimals #AnimalLove #CutestEver #AnimalOfTheDay",
    "#FunnyAnimals #CuteShorts #AnimalShorts #ViralAnimal #InstantSmile",
]

# ── Spanish hashtags (broadens reach to Latin America / Spain) ────────────────
_HASHTAGS_ES = (
    "#AnimalesAdorables #AnimalesGraciosos #AnimalesTiernos "
    "#AnimalesKawaii #AnimalCute #AnimalesViral"
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


def generate_metadata(prompt_entry: dict | None = None) -> tuple[str, str, str]:
    """Return (title, description) for the upload.

    Priority:
      1. Action-specific viral title from ``_ACTION_TITLES`` (highest clickability).
      2. Generic ``title_templates`` from animal_prompts.json.
      3. Hard-coded fallback.
    """
    import random
    data = _load_prompt_data()

    if prompt_entry is None:
        prompt_entry = random.choice(data["prompts"])

    animal  = prompt_entry["animal"].title()
    action  = prompt_entry.get("action", "")
    action_display = action.replace("_", " ")

    # Priority 1: action-specific viral title
    title_source = "generic"
    action_pool = _ACTION_TITLES.get(action, [])
    if action_pool:
        template = random.choice(action_pool)
        title = template.replace("{animal}", animal)
        title_source = "action_specific"
    else:
        # Priority 2: generic templates from JSON
        templates = data.get("title_templates", [])
        if templates:
            template = random.choice(templates)
            title = (
                template
                .replace("{animal}", animal)
                .replace("{action}", action_display)
            )
        else:
            title = f"This cute {animal} {action_display} will make your day 🥹 #shorts"

    # Ensure title ≤ 100 chars (YouTube limit)
    if len(title) > 97:
        title = title[:97] + "…"

    # Action-specific opener line for description
    opener_tpl = _ACTION_OPENERS.get(action, _DEFAULT_OPENER)
    opener = opener_tpl.replace("{animal}", animal)

    # Animal-specific hashtags
    animal_tag = animal.replace(" ", "")
    animal_hashtags = (
        f"#{animal_tag} #{animal_tag}Shorts #{animal_tag}Lovers "
        f"#{animal_tag}Video #{animal_tag}Life #{animal_tag}OfTheDay "
        f"#{animal_tag}TikTok #{animal_tag}Cute #{animal_tag}Funny "
        f"#Cute{animal_tag} #Funny{animal_tag} #Baby{animal_tag}"
    )

    # Weekly rotating hashtag pack
    from datetime import date
    week_num = date.today().isocalendar()[1]
    weekly_pack = _WEEKLY_HASHTAG_PACKS[week_num % len(_WEEKLY_HASHTAG_PACKS)]

    description = (
        f"{opener}\n\n"
        f"\U0001f50a Turn on sound for the full experience!\n"
        f"\u2728 Subscribe for daily cute animal animations! \U0001f514\n"
        f"\U0001f514 New cute video every single day!\n\n"
        f"{HASHTAGS}\n"
        f"{animal_hashtags}\n"
        f"{weekly_pack}\n"
        f"{_HASHTAGS_ES}"
    )

    return title, description, title_source


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

    title, description, title_source = generate_metadata(prompt_entry)
    logger.info(f"Title [{title_source}]: {title}")

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
            action = prompt_entry.get("action", "") if prompt_entry else ""
            _record_upload(
                video_id, title, str(video_path),
                animal=animal, action=action, title_source=title_source,
            )

            # ── Post-upload actions (non-fatal) ──────────────────────────
            _auto_like(youtube, video_id)
            _post_comment(youtube, video_id, animal)
            if animal:
                from scripts.playlists import add_video_to_animal_playlist
                add_video_to_animal_playlist(youtube, video_id, animal)

            return video_id

        except HttpError as e:
            status = e.resp.status
            if status in (500, 502, 503, 504) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"HTTP {status} — retrying in {wait}s…")
                time.sleep(wait)
            elif status == 403 and "quotaExceeded" in str(e) and attempt < max_retries:
                wait = 60
                logger.warning(f"Quota exceeded — waiting {wait}s before retry…")
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


def _record_upload(
    video_id: str,
    title: str,
    video_path: str,
    animal: str = "",
    action: str = "",
    title_source: str = "",
) -> None:
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
        "action": action,
        "title_source": title_source,   # "action_specific" | "generic" — for A/B tracking
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
