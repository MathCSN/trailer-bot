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


def _check(r, what):
    """Comme raise_for_status, mais avec le message d'erreur d'Instagram."""
    if not r.ok:
        try:
            err = r.json().get("error", r.json())
        except ValueError:
            err = r.text[:300]
        raise RuntimeError(f"Instagram ({what}) {r.status_code} : {err}")
    return r


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


def post_reel(video_path, caption, token=None, publish=True):
    token = refresh_token(token or os.environ["INSTAGRAM_TOKEN"])
    me = requests.get(f"{GRAPH}/me", params={"fields": "user_id,username,account_type", "access_token": token}, timeout=30)
    _check(me, "profil")
    user_id = me.json()["user_id"]

    # 1. conteneur Reel en upload direct (pas besoin d'héberger la vidéo)
    r = requests.post(f"{GRAPH}/{user_id}/media", timeout=30, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
        "share_to_feed": "true", "access_token": token,
    })
    _check(r, "création du Reel")
    container = r.json()["id"]

    # 2. envoi du fichier
    video_path = Path(video_path)
    with open(video_path, "rb") as fh:
        up = requests.post(f"{RUPLOAD}/{container}", data=fh, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0",
            "file_size": str(video_path.stat().st_size),
        })
    _check(up, "envoi de la vidéo")

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

    if not publish:
        return {"container": container, "status": "FINISHED", "username": me.json().get("username"),
                "account_type": me.json().get("account_type"), "published": False}

    # 4. publication
    p = requests.post(f"{GRAPH}/{user_id}/media_publish", timeout=60,
                      data={"creation_id": container, "access_token": token})
    _check(p, "publication")
    return {"media_id": p.json()["id"], "username": me.json().get("username")}


if __name__ == "__main__":
    # Test sans publier : python instagram.py --test
    import sys
    import tempfile
    if "--test" in sys.argv:
        tmp = Path(tempfile.mkdtemp()) / "test.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x202020:s=1080x1920:d=4:r=30",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(tmp)], check=True)
        print(post_reel(tmp, "test (non publié)", publish=False))
