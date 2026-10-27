"""Publication des Reels sur la Page Facebook (API officielle « Pages », Reels Publishing API).

Secrets : FACEBOOK_PAGE_ID et FACEBOOK_PAGE_TOKEN (token de Page longue durée, créé par le
workflow « connexion Facebook »). L'app Meta en mode développement suffit pour TA Page.
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/v21.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/v21.0"


def _check(r, what):
    if not r.ok:
        try:
            err = r.json().get("error", r.json())
        except ValueError:
            err = r.text[:300]
        raise RuntimeError(f"Facebook ({what}) {r.status_code} : {err}")
    return r.json()


def cover_first(video, cover):
    """Copie de la vidéo dont la 1re image (0,3 s) est la belle scène : Facebook prend la 1re image en vignette."""
    out = Path(tempfile.mkdtemp()) / Path(video).name
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-t", "0.3", "-i", str(cover),
        "-f", "lavfi", "-t", "0.3", "-i", "anullsrc=r=44100:cl=stereo",
        "-i", str(video),
        "-filter_complex",
        "[0:v]scale=1080:1920,setsar=1,format=yuv420p[c];[2:v]setsar=1,format=yuv420p[v];"
        "[c][1:a][v][2:a]concat=n=2:v=1:a=1[outv][outa]",
        "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out)], check=True)
    return out


def post_reel(video_path, caption, cover=None, page_id=None, token=None):
    page_id = page_id or os.environ["FACEBOOK_PAGE_ID"]
    token = token or os.environ["FACEBOOK_PAGE_TOKEN"]
    video_path = Path(cover_first(video_path, cover) if cover else video_path)

    # 1. ouverture de l'envoi
    start = _check(requests.post(f"{GRAPH}/{page_id}/video_reels", timeout=30,
                                 data={"upload_phase": "start", "access_token": token}), "démarrage")
    video_id = start["video_id"]

    # 2. envoi du fichier
    with open(video_path, "rb") as fh:
        _check(requests.post(f"{RUPLOAD}/{video_id}", data=fh, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0",
            "file_size": str(video_path.stat().st_size)}), "envoi de la vidéo")

    # 3. publication avec la description
    _check(requests.post(f"{GRAPH}/{page_id}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": caption[:2200], "access_token": token}), "publication")

    # 4. vignette = une belle scène du film (bonus : sans effet bloquant si Facebook la refuse)
    if cover:
        try:
            with open(cover, "rb") as fh:
                requests.post(f"{GRAPH}/{video_id}/thumbnails", timeout=60,
                              data={"is_preferred": "true", "access_token": token},
                              files={"source": ("cover.jpg", fh, "image/jpeg")})
        except requests.RequestException:
            pass

    # 5. attente du traitement
    status = {}
    for _ in range(40):
        time.sleep(10)
        status = requests.get(f"{GRAPH}/{video_id}", timeout=30,
                              params={"fields": "status", "access_token": token}).json().get("status", {})
        if status.get("video_status") in ("ready", "error") or \
                status.get("publishing_phase", {}).get("status") == "complete":
            break
    if status.get("video_status") == "error":
        raise RuntimeError(f"Facebook a refusé la vidéo : {status}")
    return {"video_id": video_id, "status": status.get("video_status", "processing")}
