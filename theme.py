"""Choix de l'ambiance visuelle selon le genre du film + génération des particules animées."""
import colorsys
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Chaque ambiance : type de particules, couleur d'accent par défaut, style des bords de la vidéo.
THEMES = {
    "fire":    {"particles": "embers",   "accent": (255, 120, 40),  "edge": "torn"},
    "horror":  {"particles": "ash",      "accent": (200, 20, 30),   "edge": "torn"},
    "scifi":   {"particles": "stars",    "accent": (60, 200, 255),  "edge": "clean"},
    "magic":   {"particles": "sparkles", "accent": (255, 200, 90),  "edge": "clean"},
    "family":  {"particles": "bokeh",    "accent": (255, 190, 60),  "edge": "clean"},
    "romance": {"particles": "bokeh",    "accent": (255, 110, 150), "edge": "clean"},
    "crime":   {"particles": "rain",     "accent": (120, 170, 255), "edge": "torn"},
    "winter":  {"particles": "snow",     "accent": (180, 220, 255), "edge": "clean"},
    "drama":   {"particles": "dust",     "accent": (240, 200, 140), "edge": "clean"},
}

# Ordre de priorité : le premier genre reconnu l'emporte.
GENRE_MAP = [
    ("épouvante", "horror"), ("horreur", "horror"),
    ("science fiction", "scifi"), ("science-fiction", "scifi"),
    ("fantastique", "magic"), ("fantasy", "magic"),
    ("animation", "family"), ("famille", "family"),
    ("romance", "romance"), ("comédie musicale", "romance"),
    ("thriller", "crime"), ("policier", "crime"), ("espionnage", "crime"),
    ("action", "fire"), ("guerre", "fire"), ("western", "fire"), ("aventure", "fire"),
    ("comédie", "family"),
]


def pick_theme(genres, title=""):
    """Genre principal (le 1er listé par AlloCiné) ; l'horreur l'emporte toujours."""
    t = title.lower()
    if "noël" in t or "noel" in t or "christmas" in t:
        return "winter"
    gl = [g.lower() for g in genres]
    if any("épouvante" in g or "horreur" in g for g in gl):
        return "horror"
    for g in gl:
        for key, theme in GENRE_MAP:
            if key in g:
                return theme
    return "drama"


def accent_from_image(img, fallback):
    """Couleur la plus « vive » et assez présente de l'affiche."""
    small = img.convert("RGB").resize((120, 180))
    pal = small.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
    colors = pal.getpalette()[:30]
    counts = sorted(pal.getcolors(), reverse=True)
    best, best_score = None, 0
    total = sum(c for c, _ in counts)
    for count, idx in counts:
        r, g, b = colors[idx * 3: idx * 3 + 3]
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if l < 0.2 or l > 0.85 or count / total < 0.02:
            continue
        score = s * (0.5 + count / total)
        if score > best_score:
            best, best_score = (h, l, s), score
    if not best or best[2] < 0.25:
        return fallback
    h, l, s = best
    r, g, b = colorsys.hls_to_rgb(h, 0.6, max(0.75, min(1, s * 1.2)))  # éclaircie pour bien ressortir sur fond sombre
    return int(r * 255), int(g * 255), int(b * 255)


# --------------------------------------------------------------------------- particules

def render_particles(kind, accent, out_dir, w=540, h=960, fps=30, seed=7):
    """Génère une boucle PNG transparente (sans coupure) de particules.

    Rendu en demi-résolution puis agrandi par ffmpeg : c'est flou de toute façon (lueurs)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    loop_s = {"embers": 6, "stars": 6, "rain": 2, "snow": 12, "ash": 12}.get(kind, 10)
    n_frames = loop_s * fps
    n = {"embers": 70, "ash": 90, "stars": 80, "sparkles": 45, "bokeh": 18,
         "rain": 120, "snow": 110, "dust": 70}[kind]
    parts = []
    for _ in range(n):
        parts.append({
            "x": rnd.uniform(0, w), "y": rnd.uniform(0, h),
            "r": rnd.uniform(0.6, 2.4), "k": rnd.choice([1, 1, 2]),
            "ph": rnd.uniform(0, 2 * math.pi), "amp": rnd.uniform(4, 30),
            "a": rnd.uniform(0.35, 1.0), "tw": rnd.choice([1, 2, 3]),
        })
    white = (255, 255, 255)
    for f in range(n_frames):
        p = f / n_frames  # phase 0..1 (boucle parfaite)
        sharp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ds, dg = ImageDraw.Draw(sharp), ImageDraw.Draw(glow)
        for q in parts:
            wob = math.sin(2 * math.pi * p * q["tw"] + q["ph"])
            if kind == "embers":
                y = (q["y"] - p * h * q["k"]) % h
                x = q["x"] + q["amp"] * wob
                a = q["a"] * (0.55 + 0.45 * math.sin(2 * math.pi * p * 6 * q["tw"] + q["ph"]))
                core = (255, 220, 150)
                _dot(ds, x, y, q["r"], core, a)
                _dot(dg, x, y, q["r"] * 4, accent, a * 0.8)
            elif kind in ("snow", "ash"):
                y = (q["y"] + p * h * q["k"]) % h
                x = q["x"] + q["amp"] * wob
                col = (230, 230, 235) if kind == "snow" else (150, 140, 135)
                _dot(ds, x, y, q["r"] * (1.2 if kind == "snow" else 0.9), col, q["a"] * 0.8)
                if kind == "snow":
                    _dot(dg, x, y, q["r"] * 3, white, q["a"] * 0.3)
            elif kind == "stars":
                x = (q["x"] - p * w * q["k"]) % w
                a = q["a"] * (0.6 + 0.4 * wob)
                ds.line([(x, q["y"]), (x + q["r"] * 6 * q["k"], q["y"])], fill=accent + (int(255 * a * 0.6),), width=1)
                _dot(ds, x, q["y"], q["r"] * 0.8, white, a)
                _dot(dg, x, q["y"], q["r"] * 3, accent, a * 0.6)
            elif kind == "rain":
                y = (q["y"] + p * h * q["k"] * 2) % h
                x = (q["x"] - (p * h * q["k"] * 2) * 0.2) % w
                ds.line([(x, y), (x - 3, y + 16 * q["r"])], fill=(190, 210, 255, int(110 * q["a"])), width=1)
            elif kind == "sparkles":
                a = max(0.0, math.sin(2 * math.pi * p * q["tw"] + q["ph"])) ** 3 * q["a"]
                x, y = q["x"] + 6 * wob, q["y"] - 10 * p
                s = q["r"] * 5 * (0.5 + a)
                ds.line([(x - s, y), (x + s, y)], fill=(255, 245, 220, int(255 * a)), width=1)
                ds.line([(x, y - s), (x, y + s)], fill=(255, 245, 220, int(255 * a)), width=1)
                _dot(dg, x, y, q["r"] * 5, accent, a * 0.8)
            elif kind == "bokeh":
                x = q["x"] + q["amp"] * 2 * wob
                y = q["y"] + q["amp"] * 2 * math.cos(2 * math.pi * p + q["ph"])
                a = q["a"] * (0.6 + 0.4 * wob)
                col = accent if q["tw"] != 2 else (255, 240, 220)
                _dot(dg, x, y, q["r"] * 14, col, a * 0.35)
            else:  # dust
                x = q["x"] + q["amp"] * wob
                y = q["y"] + q["amp"] * math.cos(2 * math.pi * p * q["tw"] + q["ph"])
                a = q["a"] * (0.5 + 0.5 * math.sin(2 * math.pi * p * 2 + q["ph"]))
                _dot(ds, x, y, q["r"] * 0.7, (255, 235, 200), a * 0.7)
                _dot(dg, x, y, q["r"] * 2.5, accent, a * 0.3)
        glow = glow.filter(ImageFilter.GaussianBlur(3 if kind != "bokeh" else 6))
        frame = Image.alpha_composite(glow, sharp)
        frame.save(out_dir / f"{f:04d}.png", optimize=False, compress_level=1)
    return out_dir / "%04d.png", n_frames


def _dot(draw, x, y, r, col, a):
    a = max(0, min(1, a))
    draw.ellipse([x - r, y - r, x + r, y + r], fill=tuple(col) + (int(255 * a),))
