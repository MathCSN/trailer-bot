"""Veille des chaînes YouTube officielles : les distributeurs y mettent les bandes-annonces à l'heure de
l'annonce, avant AlloCiné. C'est là que les sites d'actu ciné les récupèrent.

La détection passe par les flux RSS publics (aucun compte). Le téléchargement, lui, demande les cookies
d'un compte YouTube (secret YOUTUBE_COOKIES, format cookies.txt) : YouTube bloque les serveurs anonymes.
"""
import datetime as dt
import difflib
import html
import json
import os
import re
import subprocess
import tempfile
import unicodedata
import urllib.parse
from pathlib import Path

import requests

import allocine

# ordre = priorité quand plusieurs chaînes publient la même bande-annonce
CHANNELS = {
    # distributeurs officiels en France
    "UCZ0o1IeuSSceEixZbSATWtw": "Warner Bros. France",
    "UCWeGmNiIrrqcDYdLh9sjVDg": "Marvel FR",
    "UCakQLdwrxuo0KhJ49Sq2csA": "Disney FR",
    "UChbOoefuXz17nioY3sgvZxQ": "Universal Pictures France",
    "UC2I1RkNIDzTCAWB8M7BbAQw": "Sony Pictures France",
    "UCgBl5P2pybImwvroU8fK2Zw": "Paramount Pictures France",
    "UCGVrjz7s7ui-c4BHZ155Ylg": "Pathé",
    "UCKnyzIyfL5Zj7cHhcC6bTyg": "Gaumont",
    "UCCKPoUe4nvn6H-HaoY2UM0g": "StudioCanal France",
    "UCHzqRMGxW953fchyYhteCfw": "SND",
    "UCl01THIfTUqU2lMVgNG7mBQ": "Metropolitan Films",
    "UCIMVZBs4uegC9eM-CqGSlfA": "UGC Distribution",
    "UCf9ebFD0v4xJNTshVXZOycw": "Apollo Films",
    "UCAwwZRQ3NEOMyt8sot2A9Rw": "Le Pacte",
    "UCcsu5MaMohKr0XF8QRK6CnA": "Diaphana",
    "UCY6zLKMdauZgU8BvqQipKcg": "Wild Bunch",
    # relais très rapides (VF / VOSTFR de films étrangers)
    "UC5i9ji_nljs6-mp0jz_hOHg": "AlloCiné Bandes Annonces",
    "UCbdQBAMy9yEqS38PvwqkW_A": "Cinealliance",
}
OFFICIAL = set(list(CHANNELS)[:16])
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
KIND = re.compile(r"bande[- ]?annonce|teaser|trailer", re.I)
NOT_TRAILER = re.compile(r"extrait|making[- ]of|featurette|interview|clip|épisode|episode|saison|série|disney\+|netflix|"
                         r"prime video|nintendo|playstation|xbox|jeu vidéo|réaction|analyse|explication|récap|#shorts|"
                         r"audiodescription|langue des signes|\bLSF\b", re.I)


def _norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def feed(channel_id):
    """Dernières vidéos d'une chaîne (≈15) : [{id, title, published, channel, short}]."""
    r = requests.get(FEED.format(channel_id), timeout=20)
    r.raise_for_status()
    out = []
    for e in r.text.split("<entry>")[1:]:
        get = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", e) or [None, ""])[1]
        out.append({"id": get("yt:videoId"), "title": html.unescape(get("title")),
                    "published": dt.datetime.fromisoformat(get("published")),
                    "channel": channel_id, "short": "/shorts/" in e})
    return out


def version(title, channel):
    v = allocine.version(title.replace("(", " ").replace(")", " "))
    if v:
        return v
    # sans mention, une bande-annonce postée par le distributeur français est en VF
    return "VF" if channel in OFFICIAL and not re.search(r"\bVO\b|\(VO\)|original", title, re.I) else ""


def film_name(title):
    """« Dune : Troisième Partie | Bande-annonce finale (VF) | … » → « Dune : Troisième Partie »."""
    t = re.sub(r"\((?:\d{4}|VF|VOST(?:FR)?|VO|HD|4K)\)", " ", title, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.match(r"^(?:Nouvelle |Dernière )?Bande-annonce (.+?) (?:\(.+?\) )?(?:VF|VOSTFR|VOST)\b", t.strip())
    if m:  # format Cinealliance : « Bande-annonce Titre FR (Titre VO) (2026) VF »
        return m.group(1).strip()
    segs = [s.strip() for s in re.split(r"\s[|–—-]\s|\s\|\s?|\|", t) if s.strip()]
    for i, s in enumerate(segs):
        k = KIND.search(s)
        if not k:
            continue
        before = s[:k.start()].strip(" :,-")
        before = re.sub(r"\b(nouvelle|dernière|la|official|officielle|final[e]?)\s*$", "", before, flags=re.I).strip(" :,-")
        if before:
            return before
        if i > 0:
            return segs[i - 1]
        after = re.sub(r"\b(officielle|official|final[e]?|VF|VOSTFR|VOST|VO)\b", "", s[k.end():], flags=re.I)
        return after.strip(" :,-")
    return ""


def find_film(name):
    """Fiche AlloCiné du film (recherche AlloCiné) si le titre correspond bien, sinon None."""
    if len(_norm(name)) < 2:
        return None
    url = f"{allocine.BASE}/_/autocomplete/{urllib.parse.quote(name)}"
    try:
        results = json.loads(allocine.get(url, max_age=6 * 3600)).get("results", [])
    except Exception:
        return None
    want = _norm(name)
    hits = []
    for r in results[:8]:
        if r.get("entity_type") != "movie":
            continue
        if any(label and difflib.SequenceMatcher(None, want, _norm(label)).ratio() >= 0.82
               for label in (r.get("label"), r.get("original_label"))):
            hits.append((int((r.get("data") or {}).get("year") or 0), str(r["entity_id"])))
    # homonymes (« Eldorado ») : le plus récent est celui dont on vient de publier la bande-annonce
    return max(hits)[1] if hits else None


def new_trailers(seen, max_age_days=3):
    """Bandes-annonces récentes des chaînes suivies, pas encore vues : [{cmedia, cfilm, label, youtube, …}].

    cmedia vaut « yt:<id> » pour partager seen.json / posted.json avec AlloCiné."""
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)
    out = []
    for prio, cid in enumerate(CHANNELS):
        try:
            videos = feed(cid)
        except Exception as e:
            print(f"  YouTube {CHANNELS[cid]} indisponible : {e}")
            continue
        for v in videos:
            key = "yt:" + v["id"]
            if key in seen or v["published"] < since or v["short"]:
                continue
            if not KIND.search(v["title"]) or NOT_TRAILER.search(v["title"]):
                continue
            ver = version(v["title"], cid)
            cfilm = find_film(film_name(v["title"]))
            if not cfilm:
                seen.add(key)  # pas un film identifiable sur AlloCiné (série, jeu, film non distribué en France…)
                continue
            out.append({"cmedia": key, "cfilm": cfilm, "youtube": v["id"], "priority": prio,
                        "label": f"{v['title']} [{ver}]" if ver else v["title"], "version": ver,
                        "channel": CHANNELS[cid], "published": v["published"].isoformat()})
    return out


def download(video_id, dest):
    """Télécharge la vidéo (≤ 1080p, mp4) avec les cookies du compte YouTube."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    cmd = ["yt-dlp", "-q", "--no-warnings", "--no-playlist", "-f",
           "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", str(dest), f"https://www.youtube.com/watch?v={video_id}"]
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    tmp = None
    if cookies:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        tmp.write(cookies + "\n")
        tmp.close()
        cmd[1:1] = ["--cookies", tmp.name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    finally:
        if tmp:
            os.unlink(tmp.name)
    if r.returncode != 0 or not dest.exists():
        err = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["erreur inconnue"]
        hint = "" if cookies else " (secret YOUTUBE_COOKIES manquant)"
        raise RuntimeError(f"téléchargement YouTube impossible{hint} : {err[0]}")
    return dest
