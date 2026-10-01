# LoreForge

Ultra long-form video game lore for sleep (1h45–3h). From a brief (game, format, scope,
duration), LoreForge researches a **lore bible**, writes an original script section by
section with repetition and banned-phrase control, narrates it with ElevenLabs, cuts
footage into scenes, and exports a **Premiere Pro timeline (FCP7 XML)** with the channel
look, music bed, captions, thumbnail and YouTube metadata with chapters.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env        # fill in the keys + YOUTUBE_COOKIES_FILE
loreforge channel prepare     # one-time: LUT, rain/film overlays, vignette
loreforge channel brand       # avatar, banner, watermark, thumbnail band
```

Requires `ffmpeg`/`ffprobe` on PATH. Keep yt-dlp current (`pip install -U yt-dlp`): if
downloads die with HTTP 403 around 10%, that's YouTube's PO token; see the notes in
`integrations/youtube_dl.py`.

## Everyday use

UI: double-click **`LoreForge.bat`** (opens http://127.0.0.1:8770). Or use the CLI:

```bash
loreforge new "Bloodborne" --format history --minutes 130 --ref <youtube-url> --footage <youtube-url>
loreforge status 20260921-bloodborne-history
loreforge step 20260921-bloodborne-history research
loreforge step 20260921-bloodborne-history script --section s05 --force   # redo one section
loreforge look preview footage.mp4 --start 60        # 10 s with the full look, + PNG still
```

Formats: `history` (chronological "Entire Lore of X"), `facts` ("N Hours of X Facts"),
`catalog` ("Every Boss in X").

## The pipeline

| Step | What it does | What it needs from you |
|---|---|---|
| `research` | Lore bible per game: facts tagged canon / inferred / theory, deduplicated, plus a chronology | Wiki pages or notes in `sources/` and/or reference video URLs |
| `outline` | Sections with word budgets and assigned facts, plus the game's address term and farewell | — |
| `script` | Writes section by section with a ledger, lint and surgical fixes | — |
| `narration` | ElevenLabs voices via J1 (default) or ElevenLabs direct; word timings without Whisper | A voice id |
| `duration` | Expands and re-narrates only what's needed to clear the duration floor | — |
| `captions` | `captions.srt` + Premiere transcript | — |
| `footage` | Downloads, cuts into scenes, rejects bright / shaky / HUD shots | Footage URLs or captures in `footage/raw/` |
| `music` | One ambient bed as long as the narration, looped and levelled | A royalty-free track (see below) |
| `thumbnail` | AI key art with no text + title and badge drawn locally | Optional references in `thumbnail/refs/` |
| `metadata` | Title, description, tags and chapters from the real timings | — |
| `premiere` | The full FCP7 XML timeline | — |

**Music:** put a royalty-free ambient loop (a few minutes is enough) in
`channels/{slug}/assets/music/`, or drop one at `output/{run}/audio/music.mp3` for a
single video. The step loops it with crossfades to the narration's length.

**Cost per 2-hour video** (measured on the Bloodborne run): Anthropic ~US$5 for the
script (prompt caching does the heavy lifting: 711k of 940k input tokens were cache
reads), ~US$0.30 for two thumbnail variants, and ElevenLabs ~102k credits, which is
1 character per credit. ElevenLabs plans: Creator US$22/mo = 121k credits (about one
video), Pro US$99/mo = 600k (about five). `eleven_flash_v2_5` halves the credits.
With `TTS_PROVIDER=j1` (default) the same voices go through api.j1tts.com instead:
no stitching or voice settings there, so speed is applied with ffmpeg and word timings
are estimated from the audio's pauses (cue starts within ~0.05s median / 0.18s p90 of
ElevenLabs' own timestamps, measured on 228 chunks).
Run `loreforge step <run> narration --set dry=1` to build the timeline with estimated
timings and silent audio, and spend credits only when the script is final.

**Narration pace:** the reference channel narrates at ~126 words per minute, measured
over its whole transcript (`channels/{slug}/reference/`). Voices
differ, so measure yours: the first chunk's log line prints its length, and
`voice.speed` in `channel.json` corrects it (this channel's voice needed 0.87).

## Run layout

```
output/{YYYYMMDD}-{slug}/
  project.json   sources/   research/   script/   audio/   footage/   thumbnail/   premiere/
library/{game}/lore_bible.json      # per game, reused across videos
channels/{slug}/                    # channel.json, brand.md, style_guide.md, banned_phrases.txt, templates/, assets/, reference/
```

## The look in Premiere

The XML brings V1 scenes, V2 rain, V3 film damage (both with alpha, *Normal* blend),
V4 vignette and V5 watermark. The colour grade can't be expressed in FCP7 XML, so it goes
on an adjustment layer. **One-time setup per channel:**

1. In any project: *New Item → Adjustment Layer*, put it on a track above V1 and below V2.
2. Lumetri Color → Basic Correction → **Input LUT → Browse** →
   `channels/{slug}/assets/look.cube`.
3. Effects → Noise & Grain → **Noise**, Amount ≈ 6–8%, *Use Color Noise* off.
4. Right-click the effects → **Save Preset** (e.g. "LoreForge look").

Per video: import the XML, add an adjustment layer spanning the sequence, drop the
preset on it. `loreforge look preview` renders the same grade + overlays with ffmpeg, so
what you tune in `channel.json` (`look.grade`) matches Premiere after `channel prepare --force`.

## Tests

```bash
pytest
```
