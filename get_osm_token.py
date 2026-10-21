import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "osm_token.txt")

print("=== OSM OAuth 2.0 Token Generator ===")
print("Use this tool to securely generate your permanent Bearer Token for ArcGIS Pro.")

client_id = input("Enter your Client ID: ").strip()

# PKCE (RFC 7636): recommended by OSM for public (non-confidential) clients
code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")

auth_url = "https://www.openstreetmap.org/oauth2/authorize?" + urllib.parse.urlencode({
    "client_id": client_id,
    "response_type": "code",
    "redirect_uri": REDIRECT_URI,
    "scope": "write_api",
    "code_challenge": code_challenge,
    "code_challenge_method": "S256",
})

print("\nOpening web browser! Please log in to OpenStreetMap and click 'Authorize'.")
print("If the browser doesn't open automatically, copy and paste this link:")
print(auth_url)

try:
    webbrowser.open(auth_url)
except Exception:
    pass

auth_code = input("\nPaste the Authorization Code provided by OSM here: ").strip()

print("\nGenerating Bearer Token...")
data = urllib.parse.urlencode({
    "client_id": client_id,
    "grant_type": "authorization_code",
    "code": auth_code,
    "redirect_uri": REDIRECT_URI,
    "code_verifier": code_verifier,
}).encode("utf-8")

req = urllib.request.Request("https://www.openstreetmap.org/oauth2/token", data=data, method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as response:
        token = json.loads(response.read().decode("utf-8")).get("access_token")
    if not token:
        raise ValueError("No access_token in response.")
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    print("\n" + "=" * 50)
    print(f"SUCCESS! Token saved to: {TOKEN_FILE}")
    print("=" * 50)
    print("The toolbox loads it automatically. This file is git-ignored - never commit it.")
    print("Alternatively, set it as the OSM_BEARER_TOKEN environment variable.")
except urllib.error.URLError as e:
    err = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
    print(f"\nFailed to generate token: {err}")
    print("Double check that you unchecked the 'Confidential application' box when making the app!")
except Exception as e:
    print(f"\nFailed to generate token: {e}")
input("\nPress Enter to exit...")
