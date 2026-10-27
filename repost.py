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
    # vignette : une image du milieu de la bande-annonce (scène du film + habillage)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(video)], capture_output=True, text=True, check=True).stdout)
    cover = tmp / "cover.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * 0.4:.1f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "2", str(cover)], check=True)

    info = allocine.film(cfilm)
    trailer = allocine.pick_trailer(allocine.film_trailers(cfilm))
    text = caption.build({**info, "version": allocine.effective_version(trailer["label"], info) if trailer else ""})
    print(f"▶ {info['title']} → Facebook")
    entry["facebook"] = facebook.post_reel(video, text, cover=cover)
    print("  Facebook :", entry["facebook"])
    STATE.write_text(json.dumps(posted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
