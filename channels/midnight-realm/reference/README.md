# The reference channel

Midnight Realm is modelled on **Nightfall Lore**, and specifically on
*"The Entire Bloodborne Lore Explained To Fall Asleep"* (`cFWWOx3Kfh8`, 2h00,
uploaded 2025-12-16, ~360k views, ~7.1k likes). The full narration is in
`nightfall-bloodborne.txt`, transcribed from the video's own captions.

That file is **not** raw material to write from. It is fed to the script lint as a
reference transcript (`lint_service.check_copied`), which flags any eight-word sequence
our script shares with it. We copy the shape of the video; the sentences have to be ours.

## Numbers

| | |
|---|---|
| Runtime | 7,223 s (2h00) |
| Words | ~15,100 |
| Pace | **126 words per minute** |
| Chapters | 11, announced out loud |
| Body chapter length | 6–14 min (avg ~10) |
| Opening | 0:00–2:27 (~300 words) |
| Conclusion | 105:04–end (~15 min) |

126 wpm matches `settings.narration_wpm` and the `voice.speed: 0.95` already set in
`channel.json`. The chapter lengths are why `WORDS_PER_SECTION` is 1,100.

## The opening, beat by beat (0:00–2:27)

This is the part worth copying most closely, because it is what decides whether someone
stays. This particular video never builds a fictional scene inside the game world: it
talks to a real person lying in a real bed, and only the story goes anywhere. That is one
of the four entrances we rotate, not a rule — see below.

1. **Two words of greeting.** "Hello and welcome."
2. **The question, before anything else.** Where are you listening from tonight? Then
   two guesses at the listener's actual situation — tucked into bed with rain on the
   window, or lying in the dark hoping to sleep. This is the comment bait, and it is
   placed in the first fifteen seconds, not at the end.
3. **You're welcome here.** Permission to settle: take a breath, settle in, let the
   world outside fade away.
4. **Name the subject, once, with a superlative that is about the story and not about
   the video.** "One of the most hauntingly beautiful and intricate stories ever told
   in gaming: the complete lore of Bloodborne."
5. **Why this story is hard to hold.** Told through item descriptions and environmental
   storytelling, never through cutscenes — a puzzle box. This earns the video's
   existence without ever saying "I did the research for you".
6. **No homework.** This isn't a test. Drifting off is a success, staying awake is also
   a success, there is no right way to listen.
7. **The itinerary.** Four or five clauses naming where we will travel, in order,
   with no twists spoiled: back to the ancient civilization, forward to the founding of
   the church, through the descent, and finally to the revelation. Then a second pass on
   *who* we will meet: hunters and scholars, gods and monsters, victims and villains.
8. **Close your eyes.** Body cue: let the tension drain from your shoulders.
9. **Three "about" clauses** naming the themes, not the plot: a night that never ends,
   blood that promises healing and delivers damnation, the price of seeking eyes on the
   inside.
10. **Straight into "Chapter one."** No pause, no music sting, no "let's get started".

## The body

- **Every chapter is announced**: "Chapter three. The scourge of beasts begins."
  Spoken, in the narration, with no break before it. It is the handhold for a listener
  who half-wakes at 40 minutes and has no idea where they are.
- **Every chapter opens by re-anchoring** place and time ("Our story now moves forward
  through time to an era of gas lamps and Gothic architecture").
- **Every chapter ends on a thematic beat**, one or two sentences that land the meaning
  of what was just told, and often hand off to the next: "The sin at the hamlet wasn't
  an isolated incident. It was the foundation upon which everything else was built."
- **One engagement break**, at the chapter boundary nearest the middle (43:24, chapter
  5 of 11). It acknowledges both kinds of listener ("still wide awake, or drifting"),
  thanks them, asks for a subscribe and a comment, and returns with two words:
  "Let's continue." It is about forty seconds and it never happens again.

## The conclusion (105:04–end, ~15 min)

1. "And so we reach the end of our long journey through..." — retraces the route in one
   sentence, using the places from the itinerary in the opening.
2. "As you drift towards sleep, or perhaps you're already sleeping, these words becoming
   part of your dreams."
3. **A theme essay**: what the story says about ambition, knowledge, institutions that
   claim authority over truth. Ten minutes of it. No new lore.
4. **The bridge to the listener's own life**, explicitly: the beasts of stress and fear,
   the nightmares of doubt, an indifferent universe — framed as things they can face.
5. **A blessing built from the game's own vocabulary**: a run of "May you..." sentences.
6. **The last line, then nothing.** "Sleep well. The hunt can wait until morning."

## The entrance is not fixed, even at the reference channel

A second video from the same channel, *"The Truth Behind Bloodborne's Blood Vials"*
(`be9rlaTTD5o`, 2h12, Sept 2026), keeps the pace, the spoken chapters and the shape of
the ending, but opens completely differently: no greeting, no channel name, no question.
It cold-opens on the click of a blood vial and the red glass catching lamplight, and only
turns to the listener half a minute later.

So the greeting is not the formula — the beats after it are. `templates/opening.md`
carries four entrances (`welcome`, `question`, `cold open`, `arrival`) and the outline
picks the first one this channel has not used in its last two videos, recording it as
`opening_mode` so it can be changed by hand before the script is written.

That second video is also worth knowing for what it gets wrong: its chapters are spoken
but missing from the description, so it loses YouTube's chapter bar, and it never asks
for a comment. Its subscribe ask, on the other hand, is better than the first video's —
it admits the channel is small and says the viewer can do it "tomorrow, or never".

## Where we deliberately differ

- **No unsourced lore.** The reference states inference as fact throughout. Our facts
  carry a `status` and inference is flagged in the narration.
- **No repetition.** A two-hour single-pass script repeats its own images; ours is
  linted for that (`check_repeated_ngrams`, the ledger).
- **Names get reintroduced.** The reference says a bare "Laurence" forty minutes after
  the last mention. See the "Clarity for the ear" rules in `style_guide.md`.
