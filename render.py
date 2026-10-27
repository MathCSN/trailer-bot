"""Montage 1080x1920 : décor du film en haut/bas, bande-annonce paysage au centre,
personnage détouré + particules qui passent devant la vidéo."""
import io
import json
import math
import re
import random
import subprocess
import textwrap
from pathlib import Path

import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageEnhance

import theme as th

W, H = 1080, 1920
VID_W, VID_H = 1080, 608
# Tout le contenu doit finir au-dessus de la légende TikTok (nom, description, barre de progression)
CONTENT_BOTTOM = 1640
VID_B_TARGET = 1090             # bas de la vidéo visé : laisse la place aux infos et aux crédits dessous
MIN_VID_Y = 520                 # place minimale en haut pour le badge et le titre
CONTENT_RIGHT = 840             # à droite : colonne de boutons TikTok (cœur, commentaires, favoris)
VID_Y = max(MIN_VID_Y, VID_B_TARGET - VID_H)  # haut de la vidéo
VID_B = VID_Y + VID_H           # bas de la vidéo
FONTS = Path(__file__).parent / "assets" / "fonts"
# Zones masquées par l'interface TikTok (onglets en haut, boutons à droite, légende en bas)
SAFE_TOP, SAFE_RIGHT, SAFE_BOTTOM = 150, 150, 1580
# TikTok zoome ~13 % sur les téléphones récents et rogne ~60 px de chaque côté : marge latérale
MARGIN = 110


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        f.set_variation_by_axes([weight])
    return f


def anton(size):
    return font("Anton-Regular.ttf", size)


def oswald(size, weight=400):
    return font("Oswald[wght].ttf", size, weight)


def _credit_words(info, lab, nam):
    """Mots du bloc de crédits, chacun avec sa police (étiquette fine, noms plus marqués)."""
    rows = [("UN FILM DE", info.get("directors", [])), ("AVEC", info.get("cast", [])[:5]),
            ("SCÉNARIO", info.get("writers", [])[:2]), ("PRODUIT PAR", info.get("producers", [])[:3]),
            ("UNE PRODUCTION", info.get("companies", [])[:3]), ("DISTRIBUTION", [info.get("distributor")])]
    words = []
    for label, names in rows:
        names = [n.upper() for n in names if n]
        if not names:
            continue
        if words:
            words.append(None)  # séparation entre deux rubriques
        words += [(w, lab, (165, 165, 170, 230)) for w in label.split()]
        for k, n in enumerate(names):
            if k:
                words.append(("·", lab, (165, 165, 170, 200)))
            words += [(w, nam, (230, 230, 235, 245)) for w in n.split()]
    return words


def _wrap(draw, words, max_w, space):
    lines, cur, cur_w = [], [], 0
    for word in words:
        if word is None:  # séparation : un grand espace, sauf en début de ligne
            if cur:
                cur.append(None)
                cur_w += space * 3
            continue
        w, f, _ = word
        ww = draw.textlength(w, font=f)
        if cur and cur_w + space + ww > max_w:
            while cur and cur[-1] is None:
                cur.pop()
            lines.append(cur)
            cur, cur_w = [], 0
        cur.append((*word, ww))
        cur_w += ww + space
    if cur:
        lines.append(cur)
    return lines


def _line_width(line, space):
    return sum(space * 3 if x is None else x[3] + space for x in line) - space


def _draw_logos(fg, logos, y, h=50, gap=44, max_w=CONTENT_RIGHT - MARGIN):
    imgs = [l.resize((max(1, int(l.width * h / l.height)), h), Image.LANCZOS) for l in logos]
    imgs = [i if i.width <= 230 else i.resize((230, int(i.height * 230 / i.width)), Image.LANCZOS) for i in imgs]
    total = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
    if total > max_w:
        k = max_w / total
        imgs = [i.resize((int(i.width * k), int(i.height * k)), Image.LANCZOS) for i in imgs]
        gap, total = int(gap * k), max_w
    x = int(MARGIN + (CONTENT_RIGHT - MARGIN - total) / 2)
    for i in imgs:
        i = i.copy()
        i.putalpha(Image.fromarray((np.asarray(i.getchannel("A")) * 0.85).astype("uint8")))
        fg.alpha_composite(i, (x, y + (h - i.height) // 2))
        x += i.width + gap
    return y + h


def credits_block(fg, info, logos, y, bottom=CONTENT_BOTTOM):
    """Bloc de crédits façon affiche de cinéma + rangée de logos, sans dépasser `bottom`."""
    draw = ImageDraw.Draw(fg)
    lab, nam = oswald(22, 300), oswald(22, 500)
    space = draw.textlength(" ", font=nam)
    logo_h = 44 if logos else 0
    max_lines = max(0, min(3, int((bottom - y - (logo_h + 14 if logos else 0)) // 30)))
    for line in _wrap(draw, _credit_words(info, lab, nam), CONTENT_RIGHT - MARGIN, space)[:max_lines]:
        x = MARGIN + (CONTENT_RIGHT - MARGIN - _line_width(line, space)) / 2
        for item in line:
            if item is None:
                x += space * 3
                continue
            w, f, c, ww = item
            draw.text((x, y), w, font=f, fill=c)
            x += ww + space
        y += 30
    if logos and y + 14 + logo_h <= bottom:
        y = _draw_logos(fg, logos, y + 14, h=logo_h)
    return y


def mont(size, weight=600):
    return font("Montserrat[wght].ttf", size, weight)


def set_layout(aspect):
    """Adapte la hauteur de la vidéo à son vrai format (16:9, 2.39:1 cinémascope...)."""
    global VID_H, VID_Y, VID_B
    VID_H = int(round(VID_W / aspect / 2)) * 2
    VID_Y = max(MIN_VID_Y, VID_B_TARGET - VID_H)
    VID_B = VID_Y + VID_H


def detect_crop(trailer):
    """Détecte les bandes noires incrustées dans la bande-annonce -> (w, h, x, y) ou None."""
    dur = probe_duration(trailer)
    found = {}
    for start in (dur * 0.25, dur * 0.5, dur * 0.7):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start:.1f}", "-t", "4", "-i", str(trailer),
                            "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"],
                           capture_output=True, text=True)
        crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        if crops:
            found[crops[-1]] = found.get(crops[-1], 0) + 1
    if not found:
        return None
    w, h, x, y = map(int, max(found, key=found.get))
    full = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", str(trailer)],
                          capture_output=True, text=True).stdout.strip().split(",")
    fw, fh = int(full[0]), int(full[1])
    if h > fh * 0.95 and w > fw * 0.95:
        return None
    # on prend la plus grande crop sûre (ffmpeg rogne parfois quelques pixels de trop)
    return (w, h, x, y)


# --------------------------------------------------------------------------- utilitaires image

def cover(img, w, h, focus_y=0.4):
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((math.ceil(img.width * s), math.ceil(img.height * s)), Image.LANCZOS)
    x = (img.width - w) // 2
    y = int((img.height - h) * focus_y)
    return img.crop((x, y, x + w, y + h))


def vgrad(w, h, stops):
    """Masque L vertical à partir de [(position 0..1, opacité 0..255)]."""
    col = np.interp(np.linspace(0, 1, h), [s[0] for s in stops], [s[1] for s in stops])
    return Image.fromarray(np.repeat(col[:, None], w, axis=1).astype("uint8"), "L")


def shadow(size, box, radius, blur, opacity=200, color=(0, 0, 0)):
    sh = Image.new("RGBA", size, color + (0,))
    ImageDraw.Draw(sh).rounded_rectangle(box, radius, fill=color + (opacity,))
    return sh.filter(ImageFilter.GaussianBlur(blur))


def text_glow(canvas, xy, text, fnt, fill, glow_col, glow=14, anchor="la", spacing=0):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.multiline_text(xy, text, font=fnt, fill=glow_col + (230,), anchor=anchor, spacing=spacing)
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(glow)))
    d2 = ImageDraw.Draw(canvas)
    d2.multiline_text((xy[0] + 3, xy[1] + 4), text, font=fnt, fill=(0, 0, 0, 160), anchor=anchor, spacing=spacing)
    d2.multiline_text(xy, text, font=fnt, fill=fill, anchor=anchor, spacing=spacing)


def fit_title(text, max_w, max_h, max_lines=3, start=150, min_size=40):
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    text = re.sub(r" ([:;!?»])", "\u00a0\\1", text.upper())  # jamais de « : » en début de ligne
    for size in range(start, min_size - 1, -4):
        f = anton(size)
        for width in range(8, 60):
            lines = textwrap.wrap(text, width=width, break_long_words=False)
            if len(lines) > max_lines:
                continue
            block = "\n".join(lines)
            l, t, r, b = draw.multiline_textbbox((0, 0), block, font=f, spacing=int(size * 0.05))
            if r - l <= max_w and b - t <= max_h:
                return block, f, b - t
            if r - l > max_w:
                break
    lines = textwrap.wrap(text, width=22)[:max_lines]
    return "\n".join(lines), anton(min_size), min_size * len(lines)


# --------------------------------------------------------------------------- détourage

def has_subtitles(path):
    """Sous-titres incrustés : lignes de texte blanc dans le bas de l'image."""
    g = np.asarray(Image.open(path).convert("RGB")).astype(int)
    h, w, _ = g.shape
    zone = g[int(h * 0.72):, int(w * 0.15):int(w * 0.85)]
    white = zone.min(axis=2) > 215
    transitions = np.abs(np.diff(white.astype(int), axis=1)).sum(axis=1)
    return (transitions >= 14).sum() >= 6


def best_cutout(images, cache_dir):
    """Détoure le personnage le plus « posable » parmi les photos du film (rembg)."""
    try:
        from rembg import remove, new_session
    except ImportError:
        return None
    # modèle spécialisé « humains » : on ne veut détourer que des personnages
    session = new_session("u2net_human_seg")
    best, best_score = None, 0
    for i, path in enumerate(images):
        with Image.open(path) as im:
            if im.width / im.height < 1.2:   # format portrait = affiche (avec texte) : on l'ignore
                continue
        if has_subtitles(path):              # sous-titres incrustés : ils seraient détourés avec le personnage
            continue
        out = Path(cache_dir) / f"cutout_human_{Path(path).stem}.png"
        if out.exists():
            cut = Image.open(out)
        else:
            src = Image.open(path).convert("RGB")
            src.thumbnail((1400, 1400))
            cut = remove(src, session=session, post_process_mask=True)
            cut.save(out)
        alpha = np.asarray(cut.getchannel("A"))
        # on ne garde que la plus grande silhouette (une seule personne)
        lab, n = ndimage.label(alpha > 128)
        if n == 0:
            continue
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        a = lab == (int(np.argmax(sizes)) + 1)
        cover_ratio = a.mean()
        if cover_ratio < 0.06 or cover_ratio > 0.5:
            continue
        ys, xs = np.nonzero(a)
        bh, bw = ys.max() - ys.min(), xs.max() - xs.min()
        if bh < a.shape[0] * 0.55:   # personnage trop petit dans l'image
            continue
        # tête coupée par le cadre : il faut de l'air au-dessus et un sommet de crâne arrondi
        if ys.min() < a.shape[0] * 0.08:      # au moins 8 % d'air au-dessus de la tête
            continue
        top_w = a[ys.min():ys.min() + 3].any(axis=0).sum()
        if top_w / max(1, a.any(axis=0).sum()) > 0.2:
            continue
        body = a[:int(a.shape[0] * 0.85)]     # seul le bas du corps peut toucher les bords (cadrage normal)
        if body[:, :6].any() or body[:, -6:].any():  # bras ou épaule coupés par le bord de l'image
            continue
        g = np.asarray(cut.convert("L")).astype(float)
        # bas : un seul morceau (le buste) peut sortir de l'image ; deux = un bras coupé
        bottom = a[-3:].any(axis=0)
        if ndimage.label(bottom)[1] > 1:
            continue
        # cheveux foncés sur fond sombre : le détourage rate le haut du crâne
        top, cols = ys.min(), np.nonzero(a[ys.min():ys.min() + 12].any(axis=0))[0]
        if len(cols):
            hair = g[top:top + 12, cols.min():cols.max() + 1][a[top:top + 12, cols.min():cols.max() + 1]].mean()
            above = g[max(0, top - 30):max(1, top - 5), cols.min():cols.max() + 1].mean()
            if hair < 70 and abs(hair - above) < 30:
                continue
        inner = ndimage.binary_erosion(a, iterations=8)
        if inner.sum() < 500:
            continue
        brightness, sharpness = g[inner].mean(), ndimage.laplace(g)[inner].var()
        if brightness < 30 or sharpness < 10:  # personnage trop sombre ou flou : rendu moche
            continue
        touches_bottom = a[-6:].mean() > 0.02  # personnage coupé en bas : idéal pour « sortir » de la vidéo
        compact = a[ys.min():ys.max(), xs.min():xs.max()].mean()  # forme pleine (pas du bruit)
        score = compact * (1.5 if touches_bottom else 0.7) * min(1, bh / max(bw, 1)) * (0.5 + cover_ratio) * min(1, brightness / 60)
        if score > best_score and compact > 0.45:
            keep = ndimage.binary_dilation(a, iterations=3)
            cut.putalpha(Image.fromarray(np.where(keep, alpha, 0).astype("uint8")))
            best, best_score = cut.crop(cut.getbbox()), score
    if best is not None:  # adoucit les bords latéraux éventuellement coupés net
        a = np.asarray(best.getchannel("A")).astype(float)
        ramp = np.clip(np.arange(best.width) / 25, 0, 1)
        ramp = np.minimum(ramp, ramp[::-1])
        if a[:, :3].mean() > 1 or a[:, -3:].mean() > 1:
            best.putalpha(Image.fromarray((a * ramp[None, :]).astype("uint8")))
    return best


# --------------------------------------------------------------------------- calques

def build_layers(info, assets, out_dir, cfg):
    """Crée bg.png (fond animé), mask.png (bords de la vidéo) et fg.png (devant la vidéo)."""
    out_dir = Path(out_dir)
    poster = Image.open(assets["poster"]).convert("RGB")
    stills = [Image.open(p).convert("RGB") for p in assets["stills"]]
    wide = sorted(stills, key=lambda im: -im.width / im.height)
    top_src = wide[0] if wide else poster
    bot_src = wide[1] if len(wide) > 1 else (wide[0] if wide else poster)

    theme_name = cfg.get("theme") or th.pick_theme(info["genres"], info["title"])
    t = th.THEMES[theme_name]
    accent = th.accent_from_image(poster, t["accent"])

    # ---- fond (un peu plus grand que l'écran pour le léger mouvement de caméra)
    BW, BH = W + 120, H + 160
    base = cover(poster, BW, BH, 0.3).filter(ImageFilter.GaussianBlur(40))
    base = ImageEnhance.Brightness(base).enhance(0.35)
    bg = base.convert("RGBA")
    # bandeau du haut : photo du film, légèrement floutée, fondue vers le bas
    top_h = VID_Y + 140
    top = cover(top_src, BW, top_h, 0.35).filter(ImageFilter.GaussianBlur(3))
    top = ImageEnhance.Brightness(top).enhance(0.62)
    bg.paste(top, (0, 0), vgrad(BW, top_h, [(0, 255), (0.75, 230), (1, 0)]))
    # bandeau du bas
    bot_y = VID_B + 60 - 100
    bot_h = BH - bot_y
    bot = cover(bot_src, BW, bot_h, 0.5).filter(ImageFilter.GaussianBlur(4))
    bot = ImageEnhance.Brightness(bot).enhance(0.5)
    bg.paste(bot, (0, bot_y), vgrad(BW, bot_h, [(0, 0), (0.12, 220), (0.55, 200), (1, 120)]))
    # voile coloré + vignette
    veil = Image.new("RGBA", (BW, BH), accent + (0,))
    veil.putalpha(vgrad(BW, BH, [(0, 40), (0.5, 10), (1, 70)]))
    bg.alpha_composite(veil)
    vig = Image.new("L", (BW, BH), 0)
    ImageDraw.Draw(vig).ellipse([-BW * 0.35, -BH * 0.15, BW * 1.35, BH * 1.15], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(160))
    bg = Image.composite(bg, Image.new("RGBA", (BW, BH), (0, 0, 0, 255)), vig)
    # halo lumineux derrière la vidéo
    ox, oy = 60, 80
    halo = shadow((BW, BH), [ox - 10, oy + VID_Y - 12, ox + W + 10, oy + VID_B + 12], 10, 40, 170, accent)
    bg.alpha_composite(halo)
    bg.convert("RGB").save(out_dir / "bg.png")

    # ---- masque des bords de la vidéo
    mask = Image.new("L", (VID_W, VID_H), 255)
    if t["edge"] == "torn":
        rnd = random.Random(info["title"])
        d = ImageDraw.Draw(mask)
        for edge in ("top", "bottom"):
            pts, phases = [], [rnd.uniform(0, 6.28) for _ in range(4)]
            for x in range(0, VID_W + 8, 8):
                # bord déchiré léger : 2 à 9 px seulement, pour ne presque rien cacher de l'image
                depth = 5 + 2 * math.sin(x / 61 + phases[0]) + 1.5 * math.sin(x / 23 + phases[1]) \
                    + 1 * math.sin(x / 9 + phases[2]) + rnd.uniform(-1, 1)
                pts.append((x, depth if edge == "top" else VID_H - depth))
            poly = [(0, 0)] + pts + [(VID_W, 0)] if edge == "top" else [(0, VID_H)] + pts + [(VID_W, VID_H)]
            d.polygon(poly, fill=0)
        mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    else:
        m = Image.new("L", (VID_W, VID_H), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, VID_W - 1, VID_H - 1], 0, fill=255)
        mask = Image.composite(m, Image.new("L", m.size, 0), vgrad(VID_W, VID_H, [(0, 0), (8 / VID_H, 255), (1 - 8 / VID_H, 255), (1, 0)]))
    mask.save(out_dir / "mask.png")

    # ---- premier plan (passe DEVANT la vidéo)
    fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cut = assets.get("cutout")
    title_box_w = W - 2 * MARGIN
    if cut is not None and cfg.get("cutout", True):
        # le personnage n'est jamais recadré : on le réduit pour qu'il tienne en entier dans 560 x 640
        # ... et sans jamais dépasser le haut de l'écran (sinon le crâne est coupé net par le bord)
        overlap = 30
        room = VID_Y + overlap - 120              # place entre le haut (sous les onglets TikTok) et la vidéo
        k = min(640 / cut.height, 560 / cut.width, room / cut.height)
        cut = cut.resize((max(1, int(cut.width * k)), max(1, int(cut.height * k))), Image.LANCZOS)
        ch = cut.height
        # overlap : pixels du personnage qui débordent sur la vidéo
        cx = W - cut.width - MARGIN // 2           # assez loin du bord pour le zoom de TikTok
        cy = VID_Y + overlap - ch
        a = cut.getchannel("A")
        # fondu progressif sur le bas du personnage : il « sort » du décor sans coupure nette
        f1 = max(0.05, (ch - overlap - 170) / ch)
        f2 = max(f1 + 0.01, (ch - overlap) / ch)
        fade = vgrad(cut.width, ch, [(0, 255), (f1, 255), (f2, 90), (1, 0)])
        cut.putalpha(Image.fromarray(np.minimum(np.asarray(a), np.asarray(fade))))
        # liseré lumineux autour du personnage pour l'intégrer à la lumière du décor.
        # On laisse 24 px de marge autour : sinon la lueur est coupée net au ras des cheveux (trait droit)
        pad = 24
        padded = Image.new("RGBA", (cut.width + 2 * pad, cut.height + 2 * pad), (0, 0, 0, 0))
        padded.paste(cut, (pad, pad))
        rim = Image.new("RGBA", padded.size, accent + (0,))
        rim.putalpha(padded.getchannel("A").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(6)))
        fg.alpha_composite(rim, (cx - pad, cy - pad))
        fg.alpha_composite(cut, (cx, cy))
        title_box_w = cx - 40 + 60   # le titre peut légèrement chevaucher le personnage

    # ---- haut : badge + titre + genres
    draw = ImageDraw.Draw(fg)
    badge = f"BANDE-ANNONCE {info.get('version') or ''}".strip()
    bf = mont(30, 800)
    l, tt, r, b = draw.textbbox((0, 0), badge, font=bf)
    bx, by = MARGIN, SAFE_TOP + 30
    fg.alpha_composite(shadow((W, H), [bx, by, bx + r + 44, by + 60], 30, 12, 140))
    draw.rounded_rectangle([bx, by, bx + r + 44, by + 60], 30, fill=accent + (255,))
    draw.text((bx + 22, by + 30), badge, font=bf, fill=(15, 12, 10), anchor="lm")

    title_top = by + 84
    max_h = VID_Y - 92 - title_top            # entre le badge et la ligne des genres
    block, tf, th_ = fit_title(info["title"], min(title_box_w, W - 2 * MARGIN), max_h)
    title_y = title_top + max(0, (max_h - th_) // 2)
    text_glow(fg, (MARGIN, title_y), block, tf, (255, 255, 255, 255), accent, glow=18, spacing=int(tf.size * 0.05))
    items = [g.upper() for g in info["genres"][:3]] + ([info["duration"]] if info.get("duration") else [])
    gf = mont(28, 700)
    max_gw = (title_box_w - 60) if cut is not None else W - 2 * MARGIN
    while len(items) > 1 and draw.textlength("  •  ".join(items), font=gf) > max_gw:
        items.pop(-2 if info.get("duration") and len(items) > 2 else -1)  # garde la durée si possible
    draw.text((MARGIN + 2, VID_Y - 44), "  •  ".join(items), font=gf, fill=accent + (255,), anchor="lm")

    # ---- bas : affiche (déborde sur la vidéo) + infos
    pw, ph_ = 250, 333
    px, py = MARGIN, VID_B - 24                      # l'affiche ne mord que très légèrement sur la vidéo
    pst = cover(poster, pw, ph_, 0.0)
    fg.alpha_composite(shadow((W, H), [px, py + 10, px + pw, py + ph_ + 10], 16, 22, 230))
    pm = Image.new("L", (pw, ph_), 0)
    ImageDraw.Draw(pm).rounded_rectangle([0, 0, pw - 1, ph_ - 1], 16, fill=255)
    fg.paste(pst, (px, py), pm)
    draw.rounded_rectangle([px, py, px + pw - 1, py + ph_ - 1], 16, outline=accent + (255,), width=4)

    tx = px + pw + 40
    col_w = CONTENT_RIGHT - tx                 # à gauche des boutons TikTok
    y = VID_B + 40
    if info.get("release_date_fr"):
        draw.text((tx, y), "AU CINÉMA LE", font=mont(28, 800), fill=accent + (255,))
        y += 40
        date = info["release_date_fr"].upper()
        df = anton(78)
        while draw.textlength(date, font=df) > col_w and df.size > 44:
            df = anton(df.size - 4)
        text_glow(fg, (tx, y), date, df, (255, 255, 255, 255), accent, glow=10)
        y += df.size + 26
    lf, nf = mont(26, 500), mont(29, 700)
    if info.get("directors"):  # réalisateur sur une seule ligne : « De Alexandre Castagnetti »
        names = list(info["directors"][:2])
        d = ", ".join(names)
        dfont = nf
        while draw.textlength("De " + d, font=dfont) > col_w and len(names) > 1:
            names.pop()                       # on retire un nom entier, jamais un morceau de nom
            d = ", ".join(names)
        while draw.textlength("De " + d, font=dfont) > col_w and dfont.size > 20:
            dfont = mont(dfont.size - 2, 700)  # un seul nom trop long : on réduit la taille
        draw.text((tx, y), "De", font=lf, fill=(220, 220, 220, 255))
        draw.text((tx + draw.textlength("De ", font=lf), y - 2), d, font=dfont, fill=(255, 255, 255, 255))
        y += 50
    for label, names in (("Avec", info.get("cast", [])[:3]),):
        if not names:
            continue
        draw.text((tx, y), label, font=lf, fill=(220, 220, 220, 255))
        y += 36
        # autant de noms que possible sur 2 lignes, sans jamais couper un nom ni finir par une virgule
        while True:
            lines, cur = [], ""
            for n in names:
                nxt = f"{cur}, {n}" if cur else n
                if cur and draw.textlength(nxt + ",", font=nf) > col_w:
                    lines.append(cur + ",")
                    cur = n
                else:
                    cur = nxt
            lines.append(cur)
            if len(lines) <= 2 or len(names) == 1:
                break
            names = names[:-1]
        for line in lines[:2]:
            draw.text((tx, y), line, font=nf, fill=(255, 255, 255, 255))
            y += 38
        y += 14
    y = max(y, py + ph_ + 6)                  # sous l'affiche et sous les infos
    # bas de l'image : un léger voile sombre pour la lisibilité, puis @ , crédits et logos
    shade = Image.new("RGBA", (W, H - y), (0, 0, 0, 0))
    shade.putalpha(vgrad(W, H - y, [(0, 0), (0.2, 120), (1, 170)]))
    fg.alpha_composite(shade, (0, y))
    if cfg.get("handle"):
        draw.text(((MARGIN + CONTENT_RIGHT) // 2, y + 18), cfg["handle"], font=mont(24, 700), fill=(255, 255, 255, 150), anchor="mm")
        y += 36
    credits_block(fg, info, assets.get("logos") or [], y + 12)
    fg.save(out_dir / "fg.png")
    return theme_name, accent


# --------------------------------------------------------------------------- vidéo

def branded_end(trailer, window=6.0, step=0.25):
    """Détecte un carton final de marque (écran uni et coloré, ex. rouge) dans les dernières secondes.
    Renvoie la durée à garder, ou None si la fin est propre."""
    dur = probe_duration(trailer)
    first_bad = None
    t = max(0.0, dur - window)
    while t < dur - 0.05:
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(trailer),
                              "-frames:v", "1", "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        if len(raw) == 64 * 36 * 3:
            px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(float)
            mean, spread = px.mean(0), px.std(0).mean()
            saturation = mean.max() - mean.min()
            flat_colored = spread < 18 and saturation > 60 and mean.max() > 90
            if flat_colored and first_bad is None:
                first_bad = t
            elif not flat_colored:
                first_bad = None   # le carton doit aller jusqu'à la fin
        t += step
    return first_bad


def best_scene_time(trailer, crop=None, samples=18, frames=None, cache_dir=None):
    """Moment de la bande-annonce qui fera une belle vignette : des personnages bien visibles, image
    lumineuse, nette, colorée, sans sous-titres ni carton (texte, critique presse). Renvoie des secondes.

    `frames` : [(temps, chemin)] déjà extraits ; s'ils ont été détourés (cache_dir), la présence
    d'un personnage est un gros bonus."""
    dur = probe_duration(trailer)
    vf = ("crop=%d:%d:%d:%d," % crop if crop else "") + "scale=192:-2"
    best_t, best_score = dur * 0.35, -1.0
    if frames is None:
        frames = [(dur * (0.12 + 0.6 * i / (samples - 1)), None) for i in range(samples)]
    for t, path in frames:
        if path:
            im = Image.open(path).convert("RGB")
            im.thumbnail((192, 192))
        else:
            raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(trailer), "-frames:v", "1",
                                  "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
            if not raw:
                continue
            im = Image.open(io.BytesIO(raw)).convert("RGB")
        g = np.asarray(im.convert("L")).astype(float)
        rgb = np.asarray(im).astype(float)
        bright = g.mean()
        if bright < 35 or g.std() < 25:          # trop sombre ou carton uni
            continue
        h = g.shape[0]
        low = rgb[int(h * 0.72):]
        white = (low.min(axis=2) > 215)
        if (np.abs(np.diff(white.astype(int), axis=1)).sum(axis=1) >= 8).sum() >= 3:  # sous-titres incrustés
            continue
        sharp = ndimage.laplace(g).var()
        color = (rgb.max(axis=2) - rgb.min(axis=2)).mean()
        score = min(sharp, 400) / 400 + color / 80 + (1 - abs(bright - 110) / 110)
        if path and cache_dir:
            mask = Path(cache_dir) / f"cutout_human_{Path(path).stem}.png"
            people = (np.asarray(Image.open(mask).getchannel("A")) > 128).mean() if mask.exists() else 0
            score += 2.0 if 0.06 <= people <= 0.6 else 0  # une scène avec des personnages, pas un carton
        if score > best_score:
            best_t, best_score = t, score
    return best_t


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def compose(trailer, layers_dir, particles_pattern, out_path, accent, crop=None, max_seconds=None, preview=False):
    layers_dir = Path(layers_dir)
    dur = probe_duration(trailer)
    if max_seconds:
        dur = min(dur, max_seconds)
    acc = "0x%02X%02X%02X" % accent
    fc = (
        # fond : lent mouvement de caméra
        f"[0:v]crop={W}:{H}:x='60+55*sin(t/7)':y='80+70*sin(t/9)',setsar=1[bg];"
        # bande-annonce mise à l'échelle + bords découpés
        f"[1:v]{'crop=%d:%d:%d:%d,' % crop if crop else ''}scale={VID_W}:{VID_H}:force_original_aspect_ratio=decrease,"
        f"pad={VID_W}:{VID_H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=rgba[tr];"
        f"[2:v]format=gray[mk];[tr][mk]alphamerge[trm];"
        f"[bg][trm]overlay=0:{VID_Y}:shortest=1[a];"
        # barre de progression sous la vidéo
        f"color=c={acc}:s={W}x5:r=30[bar];"
        f"[a][bar]overlay=x='-{W}+{W}*t/{dur:.3f}':y={VID_B + 2}[b];"
        # premier plan (personnage, titre, affiche, infos)
        f"[b][3:v]overlay=0:0[c];"
        # particules par-dessus tout (y compris la vidéo)
        f"[4:v]scale={W}:{H},format=rgba,colorchannelmixer=aa=0.85[p];"
        f"[c][p]overlay=0:0,format=yuv420p[out]"
    )
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", "30", "-i", str(layers_dir / "bg.png"),
           "-i", str(trailer),
           "-loop", "1", "-framerate", "30", "-i", str(layers_dir / "mask.png"),
           "-loop", "1", "-framerate", "30", "-i", str(layers_dir / "fg.png"),
           "-stream_loop", "-1", "-framerate", "30", "-i", str(particles_pattern),
           "-filter_complex", fc, "-map", "[out]", "-map", "1:a?",
           "-t", f"{dur:.3f}", "-r", "30",
           "-c:v", "libx264", "-preset", "veryfast" if preview else "medium", "-crf", "20",
           "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
           "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


def still_preview(layers_dir, trailer, particles_dir, out_png, crop=None, at=40):
    """Image fixe de contrôle (rapide) : composite à l'instant `at` secondes."""
    layers_dir = Path(layers_dir)
    frame = layers_dir / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at), "-i", str(trailer),
                    "-frames:v", "1", str(frame)], check=True)
    bg = Image.open(layers_dir / "bg.png").convert("RGBA").crop((60, 80, 60 + W, 80 + H))
    fr = Image.open(frame).convert("RGB")
    if crop:
        w, h, x, y = crop
        fr = fr.crop((x, y, x + w, y + h))
    fr = ImageOps.pad(fr, (VID_W, VID_H), color=(0, 0, 0))
    fr.putalpha(Image.open(layers_dir / "mask.png"))
    bg.alpha_composite(fr, (0, VID_Y))
    bg.alpha_composite(Image.open(layers_dir / "fg.png"))
    p = Image.open(Path(particles_dir) / "0000.png").resize((W, H))
    bg.alpha_composite(p)
    bg.convert("RGB").save(out_png)
    return out_png
