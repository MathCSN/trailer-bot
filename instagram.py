"""Publication Instagram (Reels) via l'API officielle « Instagram API with Instagram Login ».

Variables : INSTAGRAM_TOKEN (token longue durée, 60 jours, renouvelé automatiquement).
La vidéo est déposée quelques minutes dans une release GitHub publique (Instagram exige une URL).
Le compte Instagram doit être un compte professionnel (Créateur ou Entreprise).
Tant que l'app Meta est en mode développement, ça marche pour TON compte : aucune validation nécessaire.
"""
import os
import re
import subprocess
import time
from pathlib import Path

import requests

GRAPH = "https://graph.instagram.com/v21.0"


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


def post_reel(video_path, caption, token=None, publish=True, cover=None):
    token = refresh_token(token or os.environ["INSTAGRAM_TOKEN"])
    me = requests.get(f"{GRAPH}/me", params={"fields": "user_id,username,account_type", "access_token": token}, timeout=30)
    _check(me, "profil")
    user_id = me.json()["user_id"]

    # 1. Instagram télécharge lui-même la vidéo : on la dépose temporairement sur GitHub (release publique)
    files = [video_path] + ([cover] if cover else [])
    urls, cleanup = host_files(files)
    try:
        return _create_and_publish(user_id, me.json(), urls[0], caption, token, publish,
                                   cover_url=urls[1] if cover else None)
    finally:
        cleanup()


def _create_and_publish(user_id, me, video_url, caption, token, publish, cover_url=None):
    data = {"media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
            "share_to_feed": "true", "access_token": token}
    if cover_url:
        data["cover_url"] = cover_url  # vignette = une belle scène du film, pas la 1re image
    r = requests.post(f"{GRAPH}/{user_id}/media", timeout=60, data=data)
    _check(r, "création du Reel")
    container = r.json()["id"]

    # 2. attente du traitement par Instagram
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
        return {"container": container, "status": "FINISHED", "username": me.get("username"),
                "account_type": me.get("account_type"), "published": False}

    # 3. publication
    p = requests.post(f"{GRAPH}/{user_id}/media_publish", timeout=60,
                      data={"creation_id": container, "access_token": token})
    _check(p, "publication")
    return {"media_id": p.json()["id"], "username": me.get("username")}


def host_files(paths):
    """Met les fichiers dans une release GitHub publique le temps qu'Instagram les récupère."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("Instagram a besoin d'une URL publique : lancer depuis GitHub Actions")
    tag = f"media-{int(time.time())}"
    args, urls = [], []
    for p in map(Path, paths):
        name = re.sub(r"[^A-Za-z0-9._-]", "-", p.name)
        args.append(f"{p}#{name}")
        urls.append(f"https://github.com/{repo}/releases/download/{tag}/{name}")
    subprocess.run(["gh", "release", "create", tag, *args, "--repo", repo, "--prerelease",
                    "--title", "fichier temporaire Instagram", "--notes", "Supprimé automatiquement après publication."],
                   check=True, capture_output=True)

    def cleanup():
        subprocess.run(["gh", "release", "delete", tag, "--repo", repo, "--yes", "--cleanup-tag"],
                       check=False, capture_output=True)
    return urls, cleanup


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
