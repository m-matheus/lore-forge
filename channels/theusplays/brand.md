# TheusPlays — brand

Eu vou lá ver pra você. A Portuguese gameplay channel (GTA VI first, other games later):
secrets, places and tests, never story spoilers. YouTube: **TheusPlays**, `@theusplaysbr`
(`@theusplays` belongs to an unrelated channel, inactive since 2017).

This folder lives in lore-forge only so `loreforge channel brand` can build the assets.
It moves to play-forge when that app exists (APO-44/APO-45). No lore pipeline runs here.

## Symbol
A heavy geometric **T** whose stem ends in the point of a map pin, inside a plain ring
that opens at the bottom for the point: ring plus point is the silhouette of a map
pin, which is the channel's promise ("I went there"). The ring has no ticks on purpose:
with ticks it read as a gun sight. Briefed in `brand/avatar_prompt.txt`.

## Colour
| Role | Hex | Where |
|---|---|---|
| Background | `#07080A` | everything sits on near-black |
| Wordmark | `#FFFFFF` | THEUS |
| Yellow accent | `#FFD21F` | PLAYS, the tagline, the avatar mark, the banner route |

Lemon yellow, not Rockstar's orange-yellow, and never a letter inside a filled yellow
square: the brand must not read as GTA or Rockstar.

## Type
**Montserrat ExtraBold** (SIL Open Font License, in `assets/fonts/`), the same face as the
how-forge thumbnails. The name is set in capitals as THEUS + PLAYS in two colours
(`brand.accent_suffix`) so the two words read apart.

## Assets (rebuilt with `loreforge channel brand --channel theusplays`)
| File | Size | Use |
|---|---|---|
| `assets/brand/avatar_art.png` | 1536×1024 | source art for the avatar |
| `assets/brand/banner_art.png` | 1536×1024 | source art for the banner: night map, yellow route through the middle band |
| `assets/brand/avatar.png` | 800×800 | YouTube profile picture (masked to a circle) |
| `assets/brand/banner.png` | 2560×1440 | YouTube banner; on desktop only the middle 2560×423 strip shows, so the route runs through it |
| `assets/brand/video_watermark.png` | 150×150 | YouTube Studio video watermark |
| `assets/watermark.png` | 1920×1080 | corner mark for an edit timeline (not used yet) |
| `assets/band.png` | 1280×122 | built by the command for Midnight-style thumbnails; unused, TheusPlays thumbnails follow how-forge (APO-48) |

`--force` regenerates the two AI images; without it only the compositing is redone. On
2026-11-18 two candidates of each were generated and the best kept (avatar without
lettering, route clear of the tagline).
