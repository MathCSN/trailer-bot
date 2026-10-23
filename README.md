# 🎬 trailer-bot — bandes-annonces automatiques pour TikTok

Chaque jour : récupère la dernière bande-annonce sortie, fabrique une vidéo verticale 1080×1920
avec un décor aux couleurs du film, écrit la description et publie sur TikTok.

## Ce que fait le montage
- **Bande-annonce en entier, en paysage**, au centre (bandes noires du cinémascope retirées automatiquement).
- **Décor en haut et en bas** construit avec les photos officielles du film, teinté avec la couleur dominante de l'affiche.
- **Ambiance selon le genre** : braises (action), pluie (thriller/policier), cendres (horreur),
  étoiles filantes (SF), paillettes (fantastique), bokeh (romance/animation), poussière (drame), neige (Noël).
- **Éléments qui passent devant la vidéo** : un personnage détouré (IA) qui sort du cadre,
  l'affiche qui déborde sur la vidéo, les particules par-dessus, bords déchirés pour action/horreur.
- Titre, badge « BANDE-ANNONCE VF/VOSTFR », genres, durée, date de sortie, réalisateur, casting.
- Barre de progression sous la vidéo. Zones cachées par l'interface TikTok respectées.

## Description générée (style AlloCiné)
Titre + version, date de sortie, synopsis, réalisateur, scénariste, casting, producteurs,
sociétés de production, distributeur, titre original, question pour les commentaires, hashtags.

## Utilisation
```bash
pip install -r requirements.txt          # + ffmpeg installé
python main.py --cfilm 1000004528 --preview   # aperçu 15 s d'un film (id AlloCiné)
python main.py --cfilm 1000004528             # vidéo complète
python main.py --latest 3                     # les 3 dernières BA pas encore traitées
python main.py --latest 1 --publish           # + publication TikTok
```
Résultats dans `output/` : `.mp4`, `.png` (aperçu) et `.txt` (description).

`config.json` :
| clé | rôle |
|---|---|
| `handle` | ton @ affiché discrètement en bas (vide = rien) |
| `versions` | versions acceptées : `VF`, `VOSTFR`, `VO` |
| `max_days_after_release` | ignore les films sortis depuis plus de N jours (ressorties) |
| `cutout` | active le personnage détouré |
| `theme` | forcer une ambiance (`fire`, `horror`, `scifi`, `magic`, `family`, `romance`, `crime`, `winter`, `drama`) |

## Publication TikTok
`publish.py` utilise l'API officielle **Content Posting API** (Direct Post) :
1. Crée une app sur developers.tiktok.com → produit *Content Posting API* → active *Direct Post* (scope `video.publish`).
2. Connecte ton compte (OAuth) pour obtenir un *refresh token*.
3. Variables : `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REFRESH_TOKEN`.
4. ⚠️ Tant que TikTok n'a pas validé ton app (audit), les vidéos sont publiées **en privé** uniquement.

Alternatives sans créer d'app : Postiz, Composio/Rube (skill `tiktok-automation`), ou les outils TikTok de Higgsfield.

## Automatisation (gratuit)
Pousse ce dossier sur un dépôt GitHub, ajoute les 3 secrets ci-dessus,
et `.github/workflows/daily.yml` publie une bande-annonce tous les jours à 18h.
Mets la variable `TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE` une fois l'app validée.
