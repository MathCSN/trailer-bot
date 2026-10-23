"""Récupère ton TIKTOK_REFRESH_TOKEN (à faire une seule fois).

  python get_token.py
"""
import secrets
import urllib.parse

import requests

print("=== Connexion TikTok (une seule fois) ===\n")
client_key = input("Client key : ").strip()
client_secret = input("Client secret : ").strip()
redirect_uri = input("Redirect URI (exactement celle saisie dans le portail TikTok) : ").strip()

state = secrets.token_urlsafe(12)
url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode({
    "client_key": client_key,
    "scope": "user.info.basic,video.publish,video.upload",
    "response_type": "code",
    "redirect_uri": redirect_uri,
    "state": state,
})
print("\n1) Ouvre ce lien, connecte-toi avec le compte TikTok de la chaîne et accepte :\n")
print(url)
print("\n2) Tu arrives sur une page (même si elle affiche une erreur, c'est normal).")
back = input("   Copie-colle ici l'adresse COMPLÈTE de cette page : ").strip()

q = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
if "code" not in q:
    raise SystemExit(f"Pas de 'code' dans l'adresse. Erreur renvoyée : {q.get('error_description') or q}")

r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", timeout=30, data={
    "client_key": client_key,
    "client_secret": client_secret,
    "code": q["code"][0],
    "grant_type": "authorization_code",
    "redirect_uri": redirect_uri,
})
data = r.json()
if "refresh_token" not in data:
    raise SystemExit(f"Échec : {data}")

print("\n✅ C'est bon ! Ajoute ces 3 secrets dans GitHub :\n")
print(f"TIKTOK_CLIENT_KEY     = {client_key}")
print(f"TIKTOK_CLIENT_SECRET  = {client_secret}")
print(f"TIKTOK_REFRESH_TOKEN  = {data['refresh_token']}")
print(f"\n(autorisations accordées : {data.get('scope')} — valable ~1 an)")
