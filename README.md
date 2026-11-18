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
loreforge channel prepare     # one-time: LUT, film overlay, vignette
loreforge channel brand       # avatar, banner, watermarks, thumbnail band
```

`channel brand` reads the two image briefs from `channels/{slug}/brand/avatar_prompt.txt` and
`banner_prompt.txt` when they exist, and falls back to the Midnight Realm ones otherwise.
`channels/theusplays/` is here only to build the TheusPlays brand until play-forge exists.

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
| `thumbnail` | AI key art with no text + title and badge drawn locally | Optional references in `thumbnail/refs/`: sent to the image tool as a mood board; on a refusal Claude describes them and the art is generated from that text alone (`--set reference=mood` skips straight to that) |
| `metadata` | Title, description, tags and chapters from the real timings | — |
| `podcast` | `audio/podcast.mp3` for Spotify (narration + music bed at `music.podcast_gain_db`, or at the level set on A2 if a saved Premiere project already exists) and `script/podcast_upload.md`, every field of the episode form (the UI shows them with Copy buttons, like the YouTube ones). The description comes from `metadata`, the square episode art from `thumbnail` | Post the episode, then paste its link (`https://open.spotify.com/episode/...`) in the step's **Spotify episode link** field |
| `premiere` | The full FCP7 XML timeline, with the Spotify QR code pointing to this video's episode (the show until the link is set) | — |

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
channels/{slug}/                    # channel.json, brand.md, brand/, style_guide.md, banned_phrases.txt, templates/, assets/, reference/
```

## The look in Premiere

The XML brings V1 scenes, V2 film damage (with alpha, *Normal* blend),
V3 vignette and V4 watermark. The colour grade can't be expressed in FCP7 XML, so it goes
on an adjustment layer. **One-time setup per channel:**

1. In any project: *New Item → Adjustment Layer*, put it on a track above V1 and below V2.
2. Lumetri Color → Basic Correction → **Input LUT → Browse** →
   `channels/{slug}/assets/look.cube`.
3. Effects → Noise & Grain → **Noise**, Amount ≈ 6–8%, *Use Color Noise* off.
4. Right-click the effects → **Save Preset** (e.g. "LoreForge look").

Per video: import the XML, add an adjustment layer spanning the sequence, drop the
preset on it. `loreforge look preview` renders the same grade + overlays with ffmpeg, so
what you tune in `channel.json` (`look.grade`) matches Premiere after `channel prepare --force`.

## The Spotify notice

With a public `spotify_url` (`https://open.spotify.com/show/...`, not the creators
dashboard) in `channel.json`, every description gets a Spotify line and every timeline
gets two clips on the top tracks at `spotify_notice.start` (10 s): the "also on Spotify"
lower third and a QR code. FCP7 XML can't carry a Motion Graphics template, so the lower
third is exported from Premiere **once per channel**:

1. New sequence 1920×1080, 30 fps, containing only the configured mogrt (11 s).
2. *File → Export → Media*: Format **QuickTime**, Video Codec **Apple ProRes 4444**,
   Depth **8-bpc + alpha**, *Render at Maximum Depth* on.
3. Save it as `channels/{slug}/assets/spotify/notice.mov`.

The QR code opens the video's own episode: run `podcast` before `premiere`, post the MP3,
paste the episode link in the podcast step (or `spotify_episode_url` in `project.json`)
and the premiere step builds `premiere/spotify_qr.mov` from it (share tracking `?si=...`
is dropped so the code stays sparse). Redo `premiere` if the link changes. Without an
episode link it falls back to the show's QR clip (`assets/spotify/qr.mov`), rebuilt by
`loreforge channel brand` whenever `spotify_url` changes. Set `spotify_notice.qr_inverted`
to `false` if a phone can't read the cream-on-dark code.

## Tests

```bash
pytest
```
