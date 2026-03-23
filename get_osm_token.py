import urllib.request
import urllib.parse
import json
import webbrowser

print("=== OSM OAuth 2.0 Token Generator ===")
print("Use this tool to securely generate your permanent Bearer Token for ArcGIS Pro.")

client_id = input("Enter your Client ID: ").strip()

# Build the authorization URL
auth_url = f"https://www.openstreetmap.org/oauth2/authorize?client_id={client_id}&response_type=code&redirect_uri=urn:ietf:wg:oauth:2.0:oob&scope=write_api"

print("\nOpening web browser! Please log in to OpenStreetMap and click 'Authorize'.")
print("If the browser doesn't open automatically, copy and paste this link:")
print(auth_url)

# Attempt to open browser automatically
try:
    webbrowser.open(auth_url)
except:
    pass

auth_code = input("\nPaste the Authorization Code provided by OSM here: ").strip()

print("\nGenerating Bearer Token...")
token_url = "https://www.openstreetmap.org/oauth2/token"
data = urllib.parse.urlencode({
    'client_id': client_id,
    'grant_type': 'authorization_code',
    'code': auth_code,
    'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob'
}).encode('utf-8')

req = urllib.request.Request(token_url, data=data, method='POST')
try:
    with urllib.request.urlopen(req) as response:
        resp_data = json.loads(response.read().decode('utf-8'))
        token = resp_data.get('access_token')
        print("\n" + "="*50)
        print("SUCCESS! Here is your permanent Bearer Token:")
        print("="*50)
        print(token)
        print("="*50)
        print("Copy this string and paste it directly into OSM_BEARER_TOKEN in OSM_Editor.pyt! Then you are good to go forever.")
except urllib.error.URLError as e:
    err = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
    print(f"\nFailed to generate token: {err}")
    print("Double check that you unchecked the 'Confidential application' box when making the app!")
input("\nPress Enter to exit...")
