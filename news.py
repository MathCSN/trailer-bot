"""Veille des nouvelles bandes-annonces (AlloCiné « plus récentes » + chaînes YouTube officielles).

  python news.py      # vérification rapide : y a-t-il une nouveauté à publier ? (go=1 pour GitHub Actions)

Ne charge que requests : la veille tourne toutes les 15 min sans installer les outils de montage.
"""
import datetime as dt
import json
import os
from pathlib import Path

import allocine
import youtube

ROOT = Path(__file__).parent
STATE = ROOT / "posted.json"
SEEN = ROOT / "seen.json"      # bandes-annonces déjà examinées (AlloCiné : cmedia, YouTube : « yt:<id> »)
CONFIG = ROOT / "config.json"


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def fresh_trailers(cfg, posted, seen):
    """Nouvelles bandes-annonces (jamais vues) de films à venir, même lointains : à publier en priorité.

    Les bandes-annonces écartées (film déjà sorti, VO sans sous-titres…) sont marquées « vues » pour
    ne plus être réexaminées ; celles retenues restent « non vues » jusqu'à leur publication.
    Une seule par film : la meilleure version (VF avant VOSTFR), AlloCiné avant YouTube (pas besoin de
    cookies), puis la chaîne officielle avant les relais."""
    today = dt.date.today()
    rank = {v: i for i, v in enumerate(cfg.get("versions") or ["VF", "VOSTFR"])}
    rank_any = {**rank, "": len(rank)}
    # film déjà publié ces 30 derniers jours : pas de doublon
    recent = {k for k, d in posted.items() if dt.datetime.fromisoformat(d["date"]).date() > today - dt.timedelta(days=30)}
    found = []
    for t in allocine.recent_trailers():
        if t["cmedia"] in seen:
            continue
        if not allocine.is_trailer(t["label"]) or allocine.version(t["label"]) not in rank_any or t["cfilm"] in recent:
            seen.add(t["cmedia"])
            continue
        found.append((0, t))
    # YouTube seulement si les cookies du compte sont configurés (sinon téléchargement impossible)
    if cfg.get("youtube", True) and os.environ.get("YOUTUBE_COOKIES", "").strip():
        for t in youtube.new_trailers(seen, max_age_days=cfg.get("youtube_max_age_days", 5)):
            if t["cfilm"] in recent:
                seen.add(t["cmedia"])
                continue
            found.append((1 + t["priority"], t))
    best = {}
    for src, t in found:
        key = (rank_any.get(allocine.version(t["label"]), len(rank)), src)
        if t["cfilm"] not in best or key < best[t["cfilm"]][0]:
            best[t["cfilm"]] = (key, t)
    out = []
    for _, t in best.values():
        info = allocine.film(t["cfilm"])
        if not info["release_date"] or dt.date.fromisoformat(info["release_date"]) < today \
                or allocine.effective_version(t["label"], info) not in rank:
            seen.add(t["cmedia"])  # film déjà sorti, sans date, ressortie ou VO non sous-titrée : pas une nouveauté
            continue
        out.append((t, info))
    # la plus fraîche d'abord : c'est elle qu'il faut sortir avant tout le monde
    return sorted(out, key=lambda x: x[0].get("published", "9999"), reverse=True)


def too_soon(cfg, posted, now=None):
    """Écart minimal entre deux publications (évite d'enchaîner 5 vidéos d'affilée)."""
    gap = cfg.get("min_gap_minutes", 30)
    dates = [dt.datetime.fromisoformat(v["date"]) for v in posted.values() if v.get("date")]
    now = now or dt.datetime.now()
    return bool(dates) and (now - max(dates)).total_seconds() < gap * 60


def main():
    cfg, posted = load(CONFIG, {}), load(STATE, {})
    seen = set(load(SEEN, []))
    news = fresh_trailers(cfg, posted, seen)
    SEEN.write_text(json.dumps(sorted(seen)))
    for t, info in news:
        print(f"▶ Nouveauté : {info['title']} — {t['label']} ({t.get('channel', 'AlloCiné')})")
    go = bool(news) and not too_soon(cfg, posted)
    if news and not go:
        print(f"  publication précédente il y a moins de {cfg.get('min_gap_minutes', 30)} min : au prochain passage")
    if not news:
        print("Rien de nouveau.")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"go={int(go)}\n")


if __name__ == "__main__":
    main()
