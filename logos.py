"""Logos des sociétés (production, distribution) récupérés sur Wikipédia et gardés dans assets/logos/.

Chaque logo est converti en blanc (comme le bloc de crédits d'une affiche) et n'est téléchargé qu'une fois.
"""
import io
import json
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

import numpy as np
import requests
from PIL import Image

CACHE = Path(__file__).parent / "assets" / "logos"
MISSING = CACHE / "_missing.json"
HEADERS = {"User-Agent": "trailer-bot/1.0 (https://github.com/MathCSN/trailer-bot)"}
KEYWORDS = ("cinéma", "film", "production", "distribu", "studio", "télévision", "chaîne", "audiovisuel",
            "média", "company", "television", "channel", "media")


def slug(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _get(url, **kw):
    for wait in (0, 2, 5, 10, 20):
        time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, **kw)
        except requests.RequestException:
            continue
        if r.status_code != 429:
            return r
    return None


def _summary(lang, title):
    r = _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")))
    return r.json() if r is not None and r.ok else {}


def _search(lang, query):
    r = _get(f"https://{lang}.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": query, "srlimit": 3, "format": "json"})
    if r is None or not r.ok:
        return []
    return [x["title"] for x in r.json().get("query", {}).get("search", [])]


def _logo_url(name):
    for lang, extra in (("fr", "société cinéma"), ("en", "film company")):
        for title in [name] + _search(lang, f"{name} {extra}"):
            j = _summary(lang, title)
            desc = (j.get("description") or "").lower()
            thumb = (j.get("thumbnail") or {}).get("source", "")
            if not thumb or not any(k in desc for k in KEYWORDS):
                continue
            if "logo" not in thumb.lower() and ".svg" not in thumb.lower():
                continue  # une photo (bâtiment, personne…) et pas un logo
            return thumb  # taille proposée par Wikipédia (les autres tailles sont refusées)
    return None


def _whiten(img):
    """Logo en blanc sur fond transparent, quel que soit le fichier d'origine."""
    img = img.convert("RGBA")
    rgba = np.asarray(img).astype(float)
    alpha = rgba[..., 3]
    if (alpha < 250).mean() > 0.1:  # le logo a déjà un fond transparent
        lum = rgba[..., :3].mean(axis=2) / 255
        opaque = alpha > 128
        dom = np.median(lum[opaque]) if opaque.any() else 1.0
        # ce qui est nettement plus clair que la couleur principale (ex. texte blanc dans la bulle Pathé)
        # devient transparent, pour que le texte reste lisible une fois le logo passé en blanc
        a = alpha * np.clip(1 - (lum - dom - 0.12) / 0.15, 0, 1)
    else:  # fond uni : l'opacité = l'écart à la couleur du fond
        border = np.concatenate([rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]])
        bg = np.median(border, axis=0)
        a = np.clip(np.linalg.norm(rgba[..., :3] - bg, axis=2) * 2.2, 0, 255)
    out = np.zeros(rgba.shape, np.uint8)
    out[..., :3] = 255
    out[..., 3] = a.astype(np.uint8)
    out = Image.fromarray(out, "RGBA")
    box = out.getbbox()
    return out.crop(box) if box else None


def get(name):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{slug(name)}.png"
    if path.exists():
        return Image.open(path)
    missing = json.loads(MISSING.read_text()) if MISSING.exists() else {}
    if time.time() - missing.get(slug(name), 0) < 30 * 86400:
        return None  # déjà cherché récemment sans succès
    url = _logo_url(name)
    logo = None
    if url:
        r = _get(url)
        if r is not None and r.ok:
            try:
                logo = _whiten(Image.open(io.BytesIO(r.content)))
            except Exception:
                logo = None
    if logo is None or logo.width < 20 or logo.height < 10:
        missing[slug(name)] = time.time()
        MISSING.write_text(json.dumps(missing, indent=1))
        return None
    logo.save(path)
    return logo


def for_film(info, limit=5):
    names = []
    for n in [info.get("distributor")] + list(info.get("companies") or []):
        if n and slug(n) not in {slug(x) for x in names}:
            names.append(n)
    found = []
    for n in names:
        logo = get(n)
        if logo is not None:
            found.append(logo)
        if len(found) >= limit:
            break
    return found
