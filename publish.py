"""Publication TikTok via l'API officielle « Content Posting API » (Direct Post).

Pré-requis (une seule fois) :
  1. Créer une app sur https://developers.tiktok.com, ajouter le produit « Content Posting API »
     et activer « Direct Post », scope `video.publish`.
  2. Récupérer un access token utilisateur (OAuth) -> variable TIKTOK_ACCESS_TOKEN.
  3. Tant que l'app n'a pas passé l'audit TikTok, les vidéos ne peuvent être publiées
     qu'en privé (SELF_ONLY). Après l'audit : PUBLIC_TO_EVERYONE.
"""
import os
import time
from pathlib import Path

import requests

API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


def access_token():
    """Token direct, ou renouvelé automatiquement via le refresh token (valable 1 an)."""
    if os.environ.get("TIKTOK_REFRESH_TOKEN"):
        r = requests.post(f"{API}/oauth/token/", timeout=30, data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
        })
        data = r.json() if r.content else {}
        if "access_token" not in data:
            raise RuntimeError("TikTok refuse de renouveler la clé (" + str(data.get("error_description") or data.get("error") or r.status_code)
                               + ") : refaire la connexion via https://mathcsn.github.io/trailer-bot/connect.html puis le workflow « connexion TikTok »")
        return data["access_token"]
    return os.environ["TIKTOK_ACCESS_TOKEN"]


def creator_info(token):
    r = requests.post(f"{API}/post/publish/creator_info/query/", headers=_headers(token), timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def post_video(video_path, caption, token=None, privacy=None, cover_ms=None):
    """TIKTOK_MODE=draft (défaut) : la vidéo arrive dans tes brouillons/notifications TikTok,
    tu colles la description et tu publies en public (marche sans l'audit TikTok).
    TIKTOK_MODE=direct : publication 100 % auto (public seulement après l'audit TikTok)."""
    token = token or access_token()
    video_path = Path(video_path)
    size = video_path.stat().st_size
    chunk = min(CHUNK, size)
    n_chunks = max(1, size // chunk)  # TikTok : le dernier morceau absorbe le reste
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": n_chunks}

    if os.environ.get("TIKTOK_MODE", "draft") == "draft":
        r = requests.post(f"{API}/post/publish/inbox/video/init/", headers=_headers(token),
                          json={"source_info": source}, timeout=30)
        r.raise_for_status()
        data = r.json()["data"]
        _upload(video_path, data["upload_url"], size, chunk, n_chunks)
        return {"mode": "draft", **_wait(token, data["publish_id"], ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE", "FAILED"))}

    info = creator_info(token)
    allowed = info.get("privacy_level_options", [])
    privacy = privacy or os.environ.get("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE")
    if privacy not in allowed:
        privacy = "SELF_ONLY" if "SELF_ONLY" in allowed else allowed[0]

    body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
            "video_cover_timestamp_ms": cover_ms or 3000,  # vignette = une belle scène du film
        },
        "source_info": source,
    }
    r = requests.post(f"{API}/post/publish/video/init/", headers=_headers(token), json=body, timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    publish_id = data["publish_id"]
    _upload(video_path, data["upload_url"], size, chunk, n_chunks)

    return {"privacy": privacy, **_wait(token, publish_id, ("PUBLISH_COMPLETE", "FAILED"))}


def _wait(token, publish_id, final):
    """Attend la fin du traitement côté TikTok (statut + raison d'un éventuel échec)."""
    s = {}
    for _ in range(60):
        time.sleep(5)
        s = requests.post(f"{API}/post/publish/status/fetch/", headers=_headers(token),
                          json={"publish_id": publish_id}, timeout=30).json()
        s = s.get("data") or s
        if s.get("status") in final:
            break
    return {"publish_id": publish_id, "status": s.get("status", "PENDING"), "fail_reason": s.get("fail_reason")}


def _upload(video_path, upload_url, size, chunk, n_chunks):
    with open(video_path, "rb") as fh:
        for i in range(n_chunks):
            start = i * chunk
            end = size - 1 if i == n_chunks - 1 else start + chunk - 1
            fh.seek(start)
            part = fh.read(end - start + 1)
            requests.put(upload_url, data=part, timeout=300, headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {start}-{end}/{size}",
            }).raise_for_status()
