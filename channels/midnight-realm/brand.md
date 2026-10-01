# Midnight Realm — brand

Video game lore to fall asleep to.

## Symbol
The initials **MR** as a single interlocked monogram, aged gold, inside a thin engraved
ring with four cardinal points. It is briefed to the image model the way you would brief
a designer — two directions, one accent colour, a legibility floor — rather than
described as an object; see `AVATAR_PROMPT` in `brand_service.py`.

The ring is what earns its place: the avatar is the one asset that gets shrunk to 24
pixels in a comment thread, and at that size the letters blur but the ring still holds a
recognizable silhouette.

## Colour
| Role | Hex | Where |
|---|---|---|
| Background | `#07080A` | everything sits on near-black |
| Wordmark | `#EDE6D6` | channel name, thumbnail titles |
| Amber accent | `#E0AA55` | the avatar monogram, the lantern glow in the banner, the tagline, the thumbnail badge |

The amber is the only warm colour in the whole identity, and it matches the video
grade, which desaturates everything except bright light sources (`look.grade.highlight_saturation`).

## Type
**Cinzel** (SIL Open Font License, in `assets/fonts/`) for everything: an inscriptional
serif that reads as old and carved without tipping into horror-blackletter. Always in
capitals with wide letter spacing; the tagline sits under the name at ~26% of its size.

## Assets (all rebuilt with `loreforge channel brand`)
| File | Size | Use |
|---|---|---|
| `assets/brand/avatar_art.png` | 1536×1024 | source art for the avatar logo |
| `assets/brand/banner_art.png` | 1536×1024 | source art for the banner |
| `assets/brand/avatar.png` | 800×800 | YouTube avatar: the logo art, centre-cropped square (it is masked to a circle) |
| `assets/brand/banner.png` | 2560×1440 | channel banner, wordmark inside the 1546×423 safe area |
| `assets/watermark.png` | 1920×1080 | the corner mark burned into every video |
| `assets/band.png` | 1280×122 | the bottom band behind the thumbnail badge |
| `assets/thumb_band/` | 1280×720 layers | the thumbnail band for Photoshop: fade, amber rule with the ring's four-point star, gilded length plate, MR seal. `band.jsx` builds the editable PSD; rebuilt with `python scripts/make_thumb_band.py` |

`--force` regenerates the two AI images; without it only the compositing is redone, so
tweaking type or colour costs nothing.

## Voice of the brand
The channel never shouts. No exclamation marks, no hype, no "you won't believe". The
name appears three times in a video at most: the welcome, the "about" block in the
description, and the farewell. Every video ends on a good night written for that game's
world (`outline.flavor.farewell`).
