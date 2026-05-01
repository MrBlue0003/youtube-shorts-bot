"""
get_youtube_token.py — One-time local script to obtain a YouTube OAuth2 refresh token.

Run this ONCE on your local machine:
    python get_youtube_token.py

It will open a browser, ask you to authorise with your Google account,
then print the refresh token AND automatically push it to Railway
(if RAILWAY_TOKEN is set in your .env).

Requirements:
  - client_secrets.json present in this directory
  - google-auth-oauthlib installed  (pip install google-auth-oauthlib)

Optional (for auto-sync to Railway):
  - Add RAILWAY_TOKEN=<your token> to .env
  - Get your Railway token at: https://railway.app/account/tokens
"""

import json
import sys
import urllib.request
import webbrowser
from pathlib import Path

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: google-auth-oauthlib is not installed.")
    print("       Run: pip install google-auth-oauthlib")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",   # upload
    "https://www.googleapis.com/auth/youtube.force-ssl", # delete + channel read
]


def _find_chrome() -> webbrowser.BaseBrowser:
    """Return a Chrome browser controller, checking common Windows paths."""
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Users\%USERNAME%\AppData\Local\Google\Chrome\Application\chrome.exe",
    ]
    # Expand %USERNAME% for the user-profile path
    import os
    chrome_paths = [os.path.expandvars(p) for p in chrome_paths]

    for path in chrome_paths:
        if Path(path).exists():
            webbrowser.register(
                "chrome",
                None,
                webbrowser.BackgroundBrowser(path),
            )
            print(f"Chrome gasit: {path}")
            return

    print("AVERTISMENT: Chrome nu a fost gasit in caile standard.")
    print("             Se va folosi browserul default.")
    # Register default browser under the name "chrome" so run_local_server works
    webbrowser.register("chrome", None, webbrowser.get())


def _read_env_file() -> dict:
    """Read key=value pairs from .env file in BASE_DIR."""
    env_path = BASE_DIR / ".env"
    result = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _sync_to_railway(refresh_token: str) -> None:
    """Push the new refresh token to Railway automatically.

    Requires RAILWAY_TOKEN in your local .env file.
    Get your token at: https://railway.app/account/tokens
    """
    env = _read_env_file()
    import os
    api_token = env.get("RAILWAY_TOKEN") or os.getenv("RAILWAY_TOKEN")

    # Hardcoded IDs for youtube-shorts-bot (production environment)
    service_id = env.get("RAILWAY_SERVICE_ID", "da59f4c1-35ba-487c-a204-79a5d3320f00")
    environment_id = env.get("RAILWAY_ENVIRONMENT_ID", "5adcc1b0-93be-46ba-b400-3067d47fd7e4")

    if not api_token:
        print()
        print("─" * 60)
        print("  TIP: Auto-sync to Railway not configured.")
        print("  Add this to your .env to never update Railway manually:")
        print("    RAILWAY_TOKEN=<your Railway API token>")
        print("  Get it at: https://railway.app/account/tokens")
        print("─" * 60)
        return

    print()
    print("Syncing token to Railway...")
    query = "mutation variableUpsert($input: VariableUpsertInput!) { variableUpsert(input: $input) }"
    payload = json.dumps({
        "query": query,
        "variables": {
            "input": {
                "serviceId": service_id,
                "environmentId": environment_id,
                "name": "YOUTUBE_REFRESH_TOKEN",
                "value": refresh_token,
            }
        }
    }).encode()

    req = urllib.request.Request(
        "https://backboard.railway.app/graphql/v2",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("errors"):
                print(f"  Railway sync FAILED: {result['errors']}")
                print("  Update YOUTUBE_REFRESH_TOKEN in Railway manually.")
            else:
                print("  ✓ Railway YOUTUBE_REFRESH_TOKEN updated automatically!")
                print("  No manual copy-paste needed.")
    except Exception as e:
        print(f"  Railway sync error: {e}")
        print("  Update YOUTUBE_REFRESH_TOKEN in Railway manually.")


def main() -> None:
    # Find client secrets
    secrets_path = BASE_DIR / "client_secrets.json"

    # Also look for the long-named Google file if client_secrets.json doesn't exist
    if not secrets_path.exists():
        candidates = sorted(BASE_DIR.glob("client_secret_*.json"))
        if candidates:
            found = candidates[0]
            print(f"Found credentials file: {found.name}")
            print(f"Copying to client_secrets.json for convenience…")
            import shutil
            shutil.copy(found, secrets_path)
        else:
            print("ERROR: client_secrets.json not found.")
            print("       Download it from Google Cloud Console:")
            print("       APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON")
            sys.exit(1)

    print("=" * 60)
    print("  YouTube OAuth2 Token Generator")
    print("=" * 60)
    print()
    print("A browser window will open. Sign in with the Google account")
    print("that owns your YouTube channel, then click Allow.")
    print()

    # Force Chrome as the browser for the OAuth flow
    _find_chrome()  # registers "chrome" in webbrowser if found

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", browser="chrome")

    refresh_token = creds.refresh_token
    client_id = creds.client_id
    client_secret = creds.client_secret

    # Save full token to file as backup
    token_path = BASE_DIR / "youtube_token.json"
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    print()
    print("=" * 60)
    print("  SUCCESS! Add this to your .env file:")
    print("=" * 60)
    print()
    print(f"YOUTUBE_REFRESH_TOKEN={refresh_token}")
    print()
    print("The full token has also been saved to:")
    print(f"  {token_path}")
    print()
    print("IMPORTANT: Keep your refresh token secret.")
    print("           Do not commit it to git.")
    print("=" * 60)

    # Quick verification
    with open(secrets_path) as f:
        secrets = json.load(f)
    web_or_installed = secrets.get("web") or secrets.get("installed") or {}
    print(f"\nClient ID (for reference): {web_or_installed.get('client_id', 'unknown')[:40]}…")

    # Auto-sync to Railway (no-op if RAILWAY_TOKEN not in .env)
    _sync_to_railway(refresh_token)


if __name__ == "__main__":
    main()
