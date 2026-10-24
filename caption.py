"""Description TikTok, dans l'esprit des chaînes de bandes-annonces (AlloCiné, etc.)."""
import re
import unicodedata

GENRE_TAGS = {
    "action": "action", "aventure": "aventure", "drame": "drame", "comédie": "comedie",
    "science fiction": "sciencefiction", "épouvante-horreur": "horreur", "thriller": "thriller",
    "animation": "animation", "fantastique": "fantastique", "romance": "romance",
    "policier": "policier", "famille": "filmfamille", "biopic": "biopic", "guerre": "filmdeguerre",
}
MAX_LEN = 2200  # limite du titre/légende de l'API TikTok
MAX_TAGS = 5


def hashtag(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9 ]", " ", t)
    return "#" + "".join(w.capitalize() for w in t.split())[:40]


def short_synopsis(s, limit=420):
    if len(s) <= limit:
        return s
    cut = s[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:end + 1] if end > 150 else cut.rsplit(" ", 1)[0] + "…")


def build(info, handle=None):
    v = info.get("version")
    lines = [f"🎬 {info['title']} – Bande-annonce{' ' + v if v else ''}"]
    if info.get("release_date_fr"):
        lines.append(f"📅 Au cinéma le {info['release_date_fr']}")
    lines.append("")
    if info.get("synopsis"):
        lines += [short_synopsis(info["synopsis"]), ""]
    if info.get("directors"):
        lines.append(f"🎥 Réalisé par {', '.join(info['directors'])}")
    if info.get("writers"):
        lines.append(f"✍️ Scénario : {', '.join(info['writers'][:2])}")
    if info.get("cast"):
        lines.append(f"🎭 Avec {', '.join(info['cast'][:4])}")
    if info.get("producers"):
        lines.append(f"🎞️ Produit par {', '.join(info['producers'][:3])}")
    if info.get("companies"):
        lines.append(f"🏢 Production : {', '.join(info['companies'][:3])}")
    if info.get("distributor"):
        lines.append(f"🍿 Distribution : {info['distributor']}")
    if info.get("original_title") and info["original_title"].lower() != info["title"].lower():
        lines.append(f"🌍 Titre original : {info['original_title']}")
    lines.append("")
    lines.append("Tu vas le voir au cinéma ? Dis-le en commentaire 👇")
    lines.append("")

    # 5 hashtags maximum : le type de vidéo, le film, le cinéma, le genre principal, la sortie
    genre = next((GENRE_TAGS[g.lower()] for g in info.get("genres", []) if g.lower() in GENRE_TAGS), "film")
    tags = ["#bandeannonce", hashtag(info["title"]), "#cinema", "#" + genre, "#sortiecinema"][:MAX_TAGS]
    lines.append(" ".join(dict.fromkeys(tags)))
    text = "\n".join(lines)
    if len(text) > MAX_LEN:  # on raccourcit le synopsis en priorité
        info = {**info, "synopsis": short_synopsis(info["synopsis"], 200)}
        text = build(info, handle)[:MAX_LEN]
    return text
