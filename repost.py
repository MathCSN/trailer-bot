"""Republie sur Facebook une vidéo déjà fabriquée (sans repasser par Instagram ni TikTok).

  python repost.py "Irish Travellers"     # titre (ou morceau du titre) ou id AlloCiné
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import allocine
import caption
import facebook

STATE = Path(__file__).parent / "posted.json"


def find(posted, query):
    q = query.strip().lower()
    hits = [(k, v) for k, v in posted.items() if k == q or q in v["title"].lower()]
    if len(hits) != 1:
        sys.exit(f"Film « {query} » introuvable ou ambigu. Films publiés : " + ", ".join(v["title"] for v in posted.values()))
    return hits[0]


def main(query):
    posted = json.loads(STATE.read_text())
    cfilm, entry = find(posted, query)
    stem = Path(entry["file"]).stem
    tmp = Path(tempfile.mkdtemp())
    # la vidéo a été mise en téléchargement par notify.py dans la release « video-<nom> »
    subprocess.run(["gh", "release", "download", f"video-{stem}", "--pattern", "*.mp4", "--dir", str(tmp)], check=True)
    video = next(tmp.glob("*.mp4"))

    info = allocine.film(cfilm)
    trailer = allocine.pick_trailer(allocine.film_trailers(cfilm))
    text = caption.build({**info, "version": allocine.version(trailer["label"]) if trailer else ""})
    print(f"▶ {info['title']} → Facebook")
    entry["facebook"] = facebook.post_reel(video, text)
    print("  Facebook :", entry["facebook"])
    STATE.write_text(json.dumps(posted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
