"""Bot TikTok de bandes-annonces.

  python main.py --cfilm 1000004528            # un film précis (id AlloCiné)
  python main.py --latest 1                    # la dernière BA pas encore publiée
  python main.py --latest 1 --publish          # ... et la publie sur TikTok
  python main.py --cfilm 1000004528 --preview  # rendu rapide de 15 s pour vérifier le décor
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import allocine
import caption
import logos
import render
import theme

ROOT = Path(__file__).parent
STATE = ROOT / "posted.json"
CONFIG = ROOT / "config.json"


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50]


def candidates(cfg, posted):
    """Films qui sortent bientôt (liste « Prochainement » d'AlloCiné), du plus proche au plus lointain.

    Seuls les films pas encore sortis sont pris (max_days_after_release = 0 par défaut) ; si la liste est
    épuisée, on se rabat sur les dernières bandes-annonces mises en ligne."""
    today = dt.date.today()
    grace = dt.timedelta(days=cfg.get("max_days_after_release", 0))
    # « release_day » : uniquement les films qui sortent ce jour-là de la semaine (en France : le mercredi)
    target = None
    if cfg.get("release_day") is not None:
        target = today + dt.timedelta(days=(cfg["release_day"] - today.weekday()) % 7)
    seen = set()
    sources = (allocine.upcoming_trailers(),) if target else (allocine.upcoming_trailers(), allocine.latest_trailers())
    for source in sources:
        found = []
        for t in source:
            if t["cfilm"] in posted or t["cfilm"] in seen or not allocine.is_trailer(t["label"]):
                continue
            if cfg.get("versions") and allocine.version(t["label"]) not in cfg["versions"]:
                continue
            seen.add(t["cfilm"])
            info = allocine.film(t["cfilm"])
            if not info["release_date"]:
                continue
            rel = dt.date.fromisoformat(info["release_date"])
            if rel < today - grace:
                continue  # déjà sorti (ou ressortie d'un vieux film)
            if target and rel != target:
                continue  # pas un film du prochain mercredi
            found.append((rel, t, info))
        for rel, t, info in sorted(found, key=lambda x: x[0]):
            yield t, info


def make(trailer, info, cfg, preview=False):
    info = {**info, "version": allocine.version(trailer["label"])}
    work = ROOT / "cache" / info["cfilm"]
    work.mkdir(parents=True, exist_ok=True)
    print(f"▶ {info['title']} — {trailer['label']}")

    mp4 = allocine.download(allocine.trailer_mp4(trailer["cmedia"], trailer["cfilm"]), work / f"trailer_{trailer['cmedia']}.mp4")
    poster = allocine.download(info["poster"], work / "poster.jpg") if info.get("poster") else None
    stills = [str(allocine.download(u, work / f"still{i}.jpg")) for i, u in enumerate(info["stills"][:10])]
    if not poster:  # pas d'affiche : on prend une image de la BA
        poster = work / "poster.jpg"
        render.subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "20", "-i", str(mp4), "-frames:v", "1", str(poster)], check=True)
    if not stills:  # pas de photos : images extraites de la BA
        dur = render.probe_duration(mp4)
        for i, f in enumerate((0.2, 0.45, 0.7)):
            p = work / f"still{i}.jpg"
            render.subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * f:.1f}", "-i", str(mp4), "-frames:v", "1", str(p)], check=True)
            stills.append(str(p))

    crop = render.detect_crop(mp4)
    cut_at = render.branded_end(mp4)
    if cut_at:
        print(f"  carton de marque détecté à la fin : vidéo coupée à {cut_at:.1f} s")
    if crop:
        render.set_layout(crop[0] / crop[1])
    else:
        w, h = map(int, render.subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                               "stream=width,height", "-of", "csv=p=0", str(mp4)],
                                              capture_output=True, text=True).stdout.strip().split(","))
        render.set_layout(w / h)

    cut = None
    if cfg.get("cutout", True):
        # candidats : photos officielles + images extraites de la bande-annonce
        frames = []
        dur = render.probe_duration(mp4)
        vf = ["-vf", "crop=%d:%d:%d:%d" % crop] if crop else []
        for i in range(24):
            p = work / f"frame{i:02d}.jpg"
            if not p.exists():
                render.subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{dur * (0.05 + 0.037 * i):.1f}",
                                       "-i", str(mp4), *vf, "-frames:v", "1", "-q:v", "2", str(p)], check=True)
            frames.append(str(p))
        cut = render.best_cutout(stills + frames, work)
    try:
        film_logos = logos.for_film(info)
    except Exception as e:  # les logos sont un bonus : jamais bloquant
        print("  logos indisponibles :", e)
        film_logos = []
    assets = {"poster": str(poster), "stills": stills, "cutout": cut, "logos": film_logos}
    theme_name, accent = render.build_layers(info, assets, work, cfg)
    pdir = work / f"particles_{theme_name}"
    if not (pdir / "0000.png").exists():
        theme.render_particles(theme.THEMES[theme_name]["particles"], accent, pdir)
    print(f"  ambiance : {theme_name}  accent : {accent}  recadrage : {crop}")

    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    name = f"{dt.date.today()}_{slug(info['title'])}"
    render.still_preview(work, mp4, pdir, out_dir / f"{name}.png", crop=crop)
    out = out_dir / f"{name}{'_preview' if preview else ''}.mp4"
    render.compose(mp4, work, pdir / "%04d.png", out, accent, crop=crop,
                   max_seconds=15 if preview else (cut_at or cfg.get("max_seconds")), preview=preview)
    text = caption.build(info, cfg.get("handle"))
    (out_dir / f"{name}.txt").write_text(text)
    print(f"  ✔ {out}")
    return out, text, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfilm", help="id AlloCiné du film (fichefilm_gen_cfilm=XXXX)")
    ap.add_argument("--latest", type=int, default=0, help="nombre de nouvelles BA à traiter")
    ap.add_argument("--preview", action="store_true", help="rendu rapide de 15 s")
    ap.add_argument("--publish", action="store_true", help="publier sur TikTok")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg est requis")

    cfg = load(CONFIG, {})
    posted = load(STATE, {})
    jobs = []
    if a.cfilm:
        t = allocine.pick_trailer(allocine.film_trailers(a.cfilm))
        if not t:
            sys.exit("Aucune bande-annonce trouvée pour ce film")
        jobs.append((t, allocine.film(a.cfilm)))
    elif a.latest:
        for job in candidates(cfg, posted):
            jobs.append(job)
            if len(jobs) >= a.latest:
                break
    else:
        ap.print_help()
        return

    for t, info in jobs:
        out, text, info = make(t, info, cfg, preview=a.preview)
        entry = {"title": info["title"], "cmedia": t["cmedia"], "file": str(out), "date": str(dt.datetime.now())}
        if a.publish and not a.preview:
            # chaque réseau est indépendant : une erreur sur l'un ne bloque pas l'autre
            if os.environ.get("TIKTOK_REFRESH_TOKEN") or os.environ.get("TIKTOK_ACCESS_TOKEN"):
                import publish
                try:
                    entry["tiktok"] = publish.post_video(out, text)
                except Exception as e:
                    entry["tiktok"] = {"error": str(e)}
                print("  TikTok :", entry["tiktok"])
            if os.environ.get("INSTAGRAM_TOKEN"):
                import instagram
                try:
                    entry["instagram"] = instagram.post_reel(out, text)
                except Exception as e:
                    entry["instagram"] = {"error": str(e)}
                print("  Instagram :", entry["instagram"])
            posted[info["cfilm"]] = entry
            STATE.write_text(json.dumps(posted, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
