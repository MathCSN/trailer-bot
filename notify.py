"""Après chaque lancement : met la vidéo en téléchargement direct et te prévient.

- la vidéo est mise dans une release GitHub (lien de téléchargement direct, pratique sur téléphone) ;
- un ticket te mentionne (@ton-pseudo) : tu reçois une notification GitHub (appli mobile et e-mail)
  avec le lien de la vidéo et la description prête à copier ;
- les vidéos de plus de 21 jours sont supprimées pour ne pas encombrer le dépôt.
"""
import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO = os.environ["GITHUB_REPOSITORY"]
OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", REPO.split("/")[0])
KEEP_DAYS = 21


def gh(*args, check=True):
    return subprocess.run(["gh", *args, "--repo", REPO], check=check, capture_output=True, text=True).stdout


def announce(video):
    caption_file = video.with_suffix(".txt")
    caption = caption_file.read_text() if caption_file.exists() else ""
    title = (caption.splitlines() or [video.stem])[0].removeprefix("🎬 ")
    tag = f"video-{video.stem}"
    gh("release", "create", tag, str(video), "--title", f"🎬 {title}",
       "--notes", "Vidéo prête à publier. Description dans le ticket correspondant.")
    url = f"https://github.com/{REPO}/releases/download/{tag}/{video.name}"
    body = (f"@{OWNER} nouvelle bande-annonce prête à publier sur TikTok 👇\n\n"
            f"📥 **Télécharger la vidéo :** {url}\n\n"
            f"**Description à copier :**\n```\n{caption.strip()}\n```\n\n"
            "Une fois publiée sur TikTok, ferme ce ticket ✅")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
        fh.write(body)
    print(gh("issue", "create", "--title", f"🎬 {title}", "--body-file", fh.name).strip())


def cleanup():
    releases = json.loads(gh("release", "list", "--limit", "200", "--json", "tagName,createdAt") or "[]")
    limit = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)
    for r in releases:
        created = dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
        if r["tagName"].startswith("video-") and created < limit:
            gh("release", "delete", r["tagName"], "--yes", "--cleanup-tag", check=False)


if __name__ == "__main__":
    for mp4 in sorted(Path("output").glob("*.mp4")):
        if not mp4.stem.endswith("_preview"):
            announce(mp4)
    cleanup()
