"""Montage 1080x1920 : décor du film en haut/bas, bande-annonce paysage au centre,
personnage détouré + particules qui passent devant la vidéo."""
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
VID_CENTER = 950                # centre vertical de la vidéo
VID_Y = VID_CENTER - VID_H // 2  # haut de la vidéo
VID_B = VID_Y + VID_H           # bas de la vidéo
FONTS = Path(__file__).parent / "assets" / "fonts"
# Zones masquées par l'interface TikTok (onglets en haut, boutons à droite, légende en bas)
SAFE_TOP, SAFE_RIGHT, SAFE_BOTTOM = 150, 150, 1580


def font(name, size, weight=None):
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight:
        f.set_variation_by_axes([weight])
    return f


def anton(size):
    return font("Anton-Regular.ttf", size)


def mont(size, weight=600):
    return font("Montserrat[wght].ttf", size, weight)


def set_layout(aspect):
    """Adapte la hauteur de la vidéo à son vrai format (16:9, 2.39:1 cinémascope...)."""
    global VID_H, VID_Y, VID_B
    VID_H = int(round(VID_W / aspect / 2)) * 2
    VID_Y = VID_CENTER - VID_H // 2
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


def fit_title(text, max_w, max_h, max_lines=3, start=150, min_size=58):
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    text = text.upper()
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
        if ys.min() < max(12, a.shape[0] * 0.04):
            continue
        top_w = a[ys.min():ys.min() + 3].any(axis=0).sum()
        if top_w / max(1, a.any(axis=0).sum()) > 0.2:
            continue
        if max(a[:, :6].mean(), a[:, -6:].mean()) > 0.15:  # trop collé à un bord latéral
            continue
        g = np.asarray(cut.convert("L")).astype(float)
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
    title_box_w = W - 120
    if cut is not None and cfg.get("cutout", True):
        ch = 640                                   # hauteur du personnage
        cut = cut.resize((int(cut.width * ch / cut.height), ch), Image.LANCZOS)
        if cut.width > 560:                        # trop large : on recadre au centre, bords adoucis
            x0 = (cut.width - 560) // 2
            cut = cut.crop((x0, 0, x0 + 560, ch))
            ramp = np.clip(np.arange(560) / 70, 0, 1)
            ramp = np.minimum(ramp, ramp[::-1])
            cut.putalpha(Image.fromarray((np.asarray(cut.getchannel("A")) * ramp[None, :]).astype("uint8")))
        overlap = 30                              # pixels du personnage qui débordent sur la vidéo
        cx = W - cut.width + 30
        cy = VID_Y + overlap - ch
        a = cut.getchannel("A")
        # fondu progressif sur le bas du personnage : il « sort » du décor sans coupure nette
        fade = vgrad(cut.width, ch, [(0, 255), ((ch - overlap - 170) / ch, 255), ((ch - overlap) / ch, 90), (1, 0)])
        cut.putalpha(Image.fromarray(np.minimum(np.asarray(a), np.asarray(fade))))
        # liseré lumineux autour du personnage pour l'intégrer à la lumière du décor
        rim = Image.new("RGBA", cut.size, accent + (0,))
        rim.putalpha(cut.getchannel("A").filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(6)))
        fg.alpha_composite(rim, (cx, cy))
        fg.alpha_composite(cut, (cx, cy))
        title_box_w = cx - 40 + 60   # le titre peut légèrement chevaucher le personnage

    # ---- haut : badge + titre + genres
    draw = ImageDraw.Draw(fg)
    badge = f"BANDE-ANNONCE {info.get('version') or ''}".strip()
    bf = mont(30, 800)
    l, tt, r, b = draw.textbbox((0, 0), badge, font=bf)
    bx, by = 60, SAFE_TOP + 40
    fg.alpha_composite(shadow((W, H), [bx, by, bx + r + 44, by + 60], 30, 12, 140))
    draw.rounded_rectangle([bx, by, bx + r + 44, by + 60], 30, fill=accent + (255,))
    draw.text((bx + 22, by + 30), badge, font=bf, fill=(15, 12, 10), anchor="lm")

    title_top = by + 90
    max_h = VID_Y - 70 - title_top
    block, tf, th_ = fit_title(info["title"], min(title_box_w, W - 120), max_h - 50)
    title_y = title_top + (max_h - 50 - th_) // 2
    text_glow(fg, (60, title_y), block, tf, (255, 255, 255, 255), accent, glow=18, spacing=int(tf.size * 0.05))
    genres = "  •  ".join(g.upper() for g in info["genres"][:3])
    if info.get("duration"):
        genres += f"  •  {info['duration']}"
    draw.text((62, VID_Y - 58), genres, font=mont(28, 700), fill=accent + (255,), anchor="lm")

    # ---- bas : affiche (déborde sur la vidéo) + infos
    pw, ph_ = 250, 333
    px, py = 60, VID_B - 24                      # l'affiche ne mord que très légèrement sur la vidéo
    pst = cover(poster, pw, ph_, 0.0)
    fg.alpha_composite(shadow((W, H), [px, py + 10, px + pw, py + ph_ + 10], 16, 22, 230))
    pm = Image.new("L", (pw, ph_), 0)
    ImageDraw.Draw(pm).rounded_rectangle([0, 0, pw - 1, ph_ - 1], 16, fill=255)
    fg.paste(pst, (px, py), pm)
    draw.rounded_rectangle([px, py, px + pw - 1, py + ph_ - 1], 16, outline=accent + (255,), width=4)

    tx = px + pw + 40
    col_w = W - SAFE_RIGHT - tx + 40
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
    for label, names in (("Réalisé par", info.get("directors")), ("Avec", info.get("cast", [])[:3])):
        if not names:
            continue
        lf, nf = mont(26, 500), mont(29, 700)
        draw.text((tx, y), label, font=lf, fill=(220, 220, 220, 255))
        y += 36
        lines, cur = [], ""
        for n in names:  # on ne coupe jamais un nom en deux
            nxt = f"{cur}, {n}" if cur else n
            if cur and draw.textlength(nxt + ",", font=nf) > col_w:
                lines.append(cur + ",")
                cur = n
            else:
                cur = nxt
        lines.append(cur)
        for line in lines[:2]:
            draw.text((tx, y), line, font=nf, fill=(255, 255, 255, 255))
            y += 38
        y += 14
    if cfg.get("handle"):
        draw.text((W // 2, SAFE_BOTTOM + 10), cfg["handle"], font=mont(26, 700),
                  fill=(255, 255, 255, 150), anchor="mm")
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
