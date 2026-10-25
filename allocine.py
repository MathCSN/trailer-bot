"""Récupération des bandes-annonces et des fiches films depuis AlloCiné."""
import hashlib
import html
import json
import re
import time
from pathlib import Path

import requests

BASE = "https://www.allocine.fr"
CACHE = Path(__file__).parent / "cache" / "http"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def get(url, max_age=3600):
    """GET avec petit cache disque (évite de marteler le site)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / hashlib.md5(url.encode()).hexdigest()
    if f.exists() and time.time() - f.stat().st_mtime < max_age:
        return f.read_text()
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    f.write_text(r.text)
    return r.text


def download(url, dest):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    with requests.get(url, headers={**HEADERS, "Referer": BASE + "/"}, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
    return dest


def _text(s):
    s = re.sub(r"<[^>]+>", " | ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"(\s*\|\s*)+", " | ", s)


def full_res(img_url):
    """Retire le recadrage AlloCiné (c_400_225/...) pour avoir l'image originale."""
    return re.sub(r"/c_\d+_\d+/", "/", img_url)


# --------------------------------------------------------------------------- listes

def latest_trailers():
    """Dernières bandes-annonces publiées : [{cmedia, cfilm, label}]."""
    s = html.unescape(get(BASE + "/video/bandes-annonces/", max_age=900))
    out, seen = [], set()
    for m in re.finditer(r'href="/video/player_gen_cmedia=(\d+)&cfilm=(\d+)\.html"[^>]*>([^<]*)<', s):
        cmedia, cfilm, label = m.group(1), m.group(2), m.group(3).strip()
        if cmedia in seen or not label:
            continue
        seen.add(cmedia)
        out.append({"cmedia": cmedia, "cfilm": cfilm, "label": label})
    return out


def upcoming_trailers(pages=3):
    """Bandes-annonces des films à venir (« Prochainement »), classées par date de sortie."""
    out, seen = [], set()
    for page in range(1, pages + 1):
        url = BASE + "/video/bandes-annonces/prochainement/" + (f"?page={page}" if page > 1 else "")
        try:
            s = html.unescape(get(url, max_age=900))
        except requests.HTTPError:
            break
        for m in re.finditer(r'href="/video/player_gen_cmedia=(\d+)&cfilm=(\d+)\.html"[^>]*>([^<]*)<', s):
            cmedia, cfilm, label = m.group(1), m.group(2), m.group(3).strip()
            if cmedia in seen or not label:
                continue
            seen.add(cmedia)
            out.append({"cmedia": cmedia, "cfilm": cfilm, "label": label})
    return out


def is_trailer(label):
    return "bande-annonce" in label.lower() or "teaser" in label.lower()


def version(label):
    l = label.upper()
    if "VF" in l:          # « VF » et « VF STFR » (passages sous-titrés) sont en français
        return "VF"
    if "VOST" in l or "STFR" in l:
        return "VOSTFR"
    if " VO" in l or l.endswith("VO"):
        return "VO"
    return ""


# --------------------------------------------------------------------------- vidéo

def trailer_mp4(cmedia, cfilm):
    s = html.unescape(get(f"{BASE}/video/player_gen_cmedia={cmedia}&cfilm={cfilm}.html"))
    urls = [u.replace("\\/", "/") for u in re.findall(r'https?:\\?/\\?/[^"\s]+?\.mp4', s)]
    if not urls:
        raise RuntimeError(f"Aucun mp4 trouvé pour cmedia={cmedia}")
    # préférence : hd > sd > le reste
    urls.sort(key=lambda u: (0 if "_hd_" in u else 1 if "_sd_" in u else 2))
    return urls[0]


# --------------------------------------------------------------------------- fiche film

def _iso_date(fr):
    m = re.match(r"(\d{1,2})(?:er)? (\w+) (\d{4})", fr or "")
    if not m or m.group(2).lower() not in MOIS:
        return None
    return f"{m.group(3)}-{MOIS.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"


def film(cfilm):
    s = get(f"{BASE}/film/fichefilm_gen_cfilm={cfilm}.html")
    ld = {}
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', s, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if d.get("@type") == "Movie":
            ld = d
            break

    body = _text(s[s.find("<body"):])
    head = body[body.find("Accueil"):][:1500] if "Accueil" in body else body[:3000]

    date_fr = None
    m = re.search(r"(\d{1,2}(?:er)? [a-zéû]+ \d{4}) \| en salle|(\d{1,2}(?:er)? [a-zéû]+ \d{4}) en salle", head)
    if m:
        date_fr = m.group(1) or m.group(2)
    m_dur = re.search(r"\b(\d{1,2}h \d{1,2}min)\b", head)
    m_par = re.search(r"\| Par \| (.+?) \| (?:Avec|Titre|Séances)", head)

    def names(v):
        v = v if isinstance(v, list) else [v] if v else []
        return [x.get("name") for x in v if isinstance(x, dict) and x.get("name")]

    cast = []
    for a in ld.get("actor", []) or []:
        p = a.get("actor", a) if isinstance(a, dict) else {}
        if p.get("name"):
            cast.append(p["name"])

    poster = (ld.get("image") or {}).get("url") if isinstance(ld.get("image"), dict) else ld.get("image")
    stills = []
    for m in re.finditer(r'(https://[a-z0-9.]+acsta\.net/c_\d+_\d+/img/[0-9a-f/]+\.jpg)"[^>]*class="[^"]*shot-img', s):
        u = full_res(m.group(1))
        if u not in stills:
            stills.append(u)
    if not stills:  # ordre des attributs variable selon les pages
        for m in re.finditer(r'class="[^"]*shot-img[^"]*"[^>]*?(https://[a-z0-9.]+acsta\.net/c_\d+_\d+/img/[0-9a-f/]+\.jpg)', s):
            u = full_res(m.group(1))
            if u not in stills:
                stills.append(u)

    crew = _crew(cfilm)
    info = {
        "cfilm": cfilm,
        "title": ld.get("name") or "",
        "original_title": ld.get("alternateName") or "",
        "synopsis": html.unescape(ld.get("description") or "").strip(),
        "genres": ld.get("genre") or [],
        "directors": names(ld.get("director")) or crew.get("Réalisateur", []),
        "writers": [w.strip(" |") for w in re.split(r"[,|]", m_par.group(1)) if w.strip(" |")] if m_par else crew.get("Scénariste", []),
        "cast": cast[:6],
        "producers": crew.get("Producteur", []),
        "companies": crew.get("_production", []),
        "distributor": (crew.get("_distribution") or [None])[0],
        "release_date_fr": date_fr,
        "release_date": _iso_date(date_fr),
        "duration": m_dur.group(1) if m_dur else None,
        "poster": full_res(poster) if poster else None,
        "stills": stills,
        "url": f"{BASE}/film/fichefilm_gen_cfilm={cfilm}.html",
    }
    return info


def _crew(cfilm):
    """Page casting : réalisateur, scénaristes, producteurs, sociétés."""
    try:
        t = _text(get(f"{BASE}/film/fichefilm-{cfilm}/casting/"))
    except requests.HTTPError:
        return {}
    out = {}
    # motif "Nom | Rôle" dans la section Production / Scénaristes
    for name, role in re.findall(r"(?<=\| )([^|]{3,60}?) \| (Producteur délégué|Producteur|Scénariste|Réalisateur)(?= \|)", t):
        out.setdefault(role, [])
        if name not in out[role]:
            out[role].append(name)
    soc = t[t.find("Sociétés"):][:1500] if "Sociétés" in t else ""
    for name, role in re.findall(r"(?<=\| )([^|]{2,60}?) \| (Distribution|Production)(?= \|)", soc):
        out.setdefault("_" + role.lower(), []).append(name)
    return out


def film_trailers(cfilm):
    """Bandes-annonces listées sur la fiche du film : [{cmedia, cfilm, label}]."""
    s = html.unescape(get(f"{BASE}/film/fichefilm_gen_cfilm={cfilm}.html"))
    out, seen = [], set()
    for title, cmedia in re.findall(r'"title":"([^"]+)","trackingIdentifier":"(\d+)-', s):
        if cmedia not in seen:
            seen.add(cmedia)
            out.append({"cmedia": cmedia, "cfilm": cfilm, "label": json.loads(f'"{title}"')})
    return out


def pick_trailer(trailers):
    """Préférence : bande-annonce VF > VOSTFR > teaser > le reste ; la plus récente d'abord."""
    def rank(t):
        l, v = t["label"].lower(), version(t["label"])
        kind = 0 if "bande-annonce" in l else 1 if "teaser" in l else 3
        return (kind, {"VF": 0, "VOSTFR": 1}.get(v, 2), -int(t["cmedia"]))
    good = [t for t in trailers if is_trailer(t["label"])]
    return sorted(good or trailers, key=rank)[0] if (good or trailers) else None
