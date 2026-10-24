"""Publication Instagram (Reels) via l'API officielle « Instagram API with Instagram Login ».

Variables : INSTAGRAM_TOKEN (token longue durée, 60 jours, renouvelé automatiquement).
Le compte Instagram doit être un compte professionnel (Créateur ou Entreprise).
Tant que l'app Meta est en mode développement, ça marche pour TON compte : aucune validation nécessaire.
"""
import os
import subprocess
import time
from pathlib import Path

import requests

GRAPH = "https://graph.instagram.com/v21.0"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/v21.0"


def refresh_token(token):
    """Prolonge le token de 60 jours et l'enregistre dans les secrets GitHub si possible."""
    r = requests.get("https://graph.instagram.com/refresh_access_token", timeout=30,
                     params={"grant_type": "ig_refresh_token", "access_token": token})
    if not r.ok:
        return token
    new = r.json().get("access_token", token)
    if new != token and os.environ.get("GH_PAT") and os.environ.get("GITHUB_REPOSITORY"):
        subprocess.run(["gh", "secret", "set", "INSTAGRAM_TOKEN", "--body", new,
                        "--repo", os.environ["GITHUB_REPOSITORY"]],
                       env={**os.environ, "GH_TOKEN": os.environ["GH_PAT"]}, check=False)
    return new


def post_reel(video_path, caption, token=None):
    token = refresh_token(token or os.environ["INSTAGRAM_TOKEN"])
    me = requests.get(f"{GRAPH}/me", params={"fields": "user_id,username", "access_token": token}, timeout=30)
    me.raise_for_status()
    user_id = me.json()["user_id"]

    # 1. conteneur Reel en upload direct (pas besoin d'héberger la vidéo)
    r = requests.post(f"{GRAPH}/{user_id}/media", timeout=30, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
        "share_to_feed": "true", "access_token": token,
    })
    r.raise_for_status()
    container = r.json()["id"]

    # 2. envoi du fichier
    video_path = Path(video_path)
    with open(video_path, "rb") as fh:
        up = requests.post(f"{RUPLOAD}/{container}", data=fh, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0",
            "file_size": str(video_path.stat().st_size),
        })
    up.raise_for_status()

    # 3. attente du traitement par Instagram
    for _ in range(90):
        time.sleep(10)
        s = requests.get(f"{GRAPH}/{container}", timeout=30,
                         params={"fields": "status_code,status", "access_token": token}).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram a refusé la vidéo : {s}")
    else:
        raise RuntimeError("Instagram : traitement trop long")

    # 4. publication
    p = requests.post(f"{GRAPH}/{user_id}/media_publish", timeout=60,
                      data={"creation_id": container, "access_token": token})
    p.raise_for_status()
    return {"media_id": p.json()["id"], "username": me.json().get("username")}
