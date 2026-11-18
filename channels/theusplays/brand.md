# TheusPlays — brand

A Portuguese gameplay channel (GTA VI first, other games later), never with story
spoilers. YouTube: **TheusPlays**, `@theusplaysbr` (`@theusplays` belongs to an unrelated
channel, inactive since 2017).

No motto or tagline: the channel will make more kinds of video than exploration, so the
name stands alone on the banner.

This folder lives in lore-forge only so `loreforge channel brand` can build the assets.
It moves to play-forge when that app exists (APO-44/APO-45). No lore pipeline runs here.

## Symbol
A capital **T** built from a game controller's D-pad: the plus-shaped cross without its
top arm, with the centre dimple and an arrow notch on each of the three arms, inside a
plain yellow ring. It says "games" without tying the channel to one game, one console or
one kind of video. At 24 px the arrows disappear and the T in the ring carries it.
Briefed in `brand/avatar_prompt.txt`.

## Colour
| Role | Hex | Where |
|---|---|---|
| Background | `#07080A` | everything sits on near-black |
| Wordmark | `#FFFFFF` | THEUS |
| Yellow accent | `#FFD21F` | PLAYS, the avatar mark, the banner stripes |

Lemon yellow, not Rockstar's orange-yellow, and never a letter inside a filled yellow
square: the brand must not read as GTA or Rockstar.

## Type
**Bungee** (SIL Open Font License, in `assets/fonts/`) for the name: heavy, square,
signage-like, close to the D-pad's shapes. Set in capitals as THEUS + PLAYS in two colours
(`brand.accent_suffix`) with tight spacing (`brand.wordmark_tracking` 0.04 em).
**Montserrat ExtraBold** stays as the secondary face (`thumbnail.badge_font`), the same as
the how-forge thumbnails.

## Banner
Near-black with yellow diagonal stripes on both sides, crossing the middle band that
desktop shows, and a faint texture of small D-pad crosses everywhere, echoing the avatar.
Unlike Midnight Realm, the art is not darkened (`brand.banner_dim: false`) so the stripes
keep the exact yellow of PLAYS, and the veil behind the name is light
(`brand.banner_veil: 90`) so the texture still shows there.

## Assets (rebuilt with `loreforge channel brand --channel theusplays`)
| File | Size | Use |
|---|---|---|
| `assets/brand/avatar_art.png` | 1536×1024 | source art for the avatar |
| `assets/brand/banner_art.png` | 1536×1024 | source art for the banner |
| `assets/brand/avatar.png` | 800×800 | YouTube profile picture (masked to a circle) |
| `assets/brand/banner.png` | 2560×1440 | YouTube banner; desktop shows only the middle 2560×423 strip |
| `assets/brand/video_watermark.png` | 150×150 | YouTube Studio video watermark |
| `assets/watermark.png` | 1920×1080 | corner mark for an edit timeline (not used yet) |
| `assets/band.png` | 1280×122 | built by the command for Midnight-style thumbnails; unused, TheusPlays thumbnails follow how-forge (APO-48) |

`--force` regenerates the two AI images from the briefs; without it only the compositing
is redone.

## How it was chosen (2026-11-18)
A first set built on a map (a T ending in a map pin, a night map with a yellow route) was
dropped: it tied the channel to exploration videos. Alternatives were compared side by
side: two avatars (D-pad T, esports T in a yellow disc), five banner textures (halftone,
carbon, grunge, honeycomb, D-pad crosses) and seven fonts. The kept banner art was made
in two steps, the stripes first and then the texture added with the stripes as a
reference image; `brand/banner_prompt.txt` describes the result as one brief.
