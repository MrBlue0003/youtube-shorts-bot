"""
get_tiktok_token.py — Obtine TikTok access token pentru @cutedaily03
"""
import os
import hashlib
import base64
import secrets
import webbrowser
import urllib.parse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "awn6qylg9usyxk8u")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "H6cyAUptNzq5ltdf3dkKop370w3oXwyf")
REDIRECT_URI = "https://mrblue0003.github.io/youtube-shorts-bot/callback.html"
SCOPES = "user.info.basic,video.publish,video.upload"

# PKCE
code_verifier = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b"=").decode()

auth_code = None

class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Autentificare reusita! Inchide aceasta fereastra.</h1>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Eroare! Incearca din nou.</h1>")

    def log_message(self, format, *args):
        pass

def get_auth_url():
    params = {
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": "cutedaily_bot",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)

def exchange_code_for_token(code):
    import requests
    r = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15
    )
    return r.json()

if __name__ == "__main__":
    print("Deschid browserul pentru autentificare TikTok...")
    url = get_auth_url()
    webbrowser.open(url)
    print(f"\nDaca browserul nu s-a deschis, copiaza URL-ul:\n{url}\n")
    print("Dupa ce te loghezi, vei vedea un cod pe pagina.")
    print("Copiaza codul si lipeste-l aici:")
    auth_code = input("Cod: ").strip()

    if auth_code:
        print(f"Cod primit!")
        token_data = exchange_code_for_token(auth_code)
        print(json.dumps(token_data, indent=2))

        if "access_token" in token_data:
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token", "")
            open_id = token_data.get("open_id", "")

            env_path = Path(__file__).parent / ".env"
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\nTIKTOK_ACCESS_TOKEN={access_token}")
                f.write(f"\nTIKTOK_REFRESH_TOKEN={refresh_token}")
                f.write(f"\nTIKTOK_OPEN_ID={open_id}")

            print(f"\n[OK] Token salvat in .env!")
            print(f"Open ID: {open_id}")
        else:
            print("\n[FAIL] Nu s-a putut obtine token-ul!")
    else:
        print("[FAIL] Nu s-a primit niciun cod!")
