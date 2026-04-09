"""
get_youtube_token.py — One-time local script to obtain a YouTube OAuth2 refresh token.

Run this ONCE on your local machine:
    python get_youtube_token.py

It will open a browser, ask you to authorise with your Google account,
then print the refresh token to copy into your .env as YOUTUBE_REFRESH_TOKEN.

Requirements:
  - client_secrets.json present in this directory
  - google-auth-oauthlib installed  (pip install google-auth-oauthlib)
"""

import json
import sys
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


if __name__ == "__main__":
    main()
