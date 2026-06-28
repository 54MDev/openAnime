# Milestone 7C — Currently-Airing Episode Lists  ✅ IMPLEMENTED (frontend; pending hardware test)

## The problem

Shows that are still airing (One Piece, *That Time I Got Reincarnated as a Slime*,
etc.) open to a single **Play** button instead of a real episode list — even
though episodes are already out. You can't pick "episode 1130"; you can only hit
one generic Play.

## Why it actually happens (correcting the mental model)

There is **no episode indexing anywhere** — not on the backend, not on startup.
The backend ([backend/app.py](backend/app.py)) only:
- bridges IR remote → WebSocket,
- serves the static frontend,
- on `/play`, scrapes a single stream **on demand** for the one episode you picked.

It never enumerates or caches a show's episodes. So "re-index on startup" isn't
the lever — there's nothing being indexed to refresh.

The episode list is built **live in the browser** from one AniList field. In
[frontend/app.js:457-468](frontend/app.js#L457-L468):

```js
const n = media.episodes;          // total episode COUNT from AniList
if (n && n > 0) {
  for (let i = 1; i <= n; i++) makeEpisode(`Episode ${i}`, i);
} else {
  makeEpisode("Play", 1);          // <-- the fallback you're seeing
}
```

For a **finished** show, AniList sets `episodes` to the final total (e.g. 12, 24).
For a **currently-airing** show, AniList sets `episodes: null` — because the
*final* total isn't known yet. `null` falls into the `else` branch → one "Play"
button. That's the whole bug.

## The fix (the lever that actually exists)

AniList already knows how many episodes have **aired** — we're just not asking
for it. Two extra fields on the query in
[frontend/app.js:92-101](frontend/app.js#L92-L101):

```graphql
status                         # RELEASING | FINISHED | NOT_YET_RELEASED ...
nextAiringEpisode { episode }  # the NEXT unaired ep number
```

For an airing show, **aired episodes = `nextAiringEpisode.episode - 1`**.
(One Piece: if the next airing is 1131, then 1130 are out.)

So the episode-count logic becomes:

```
airedCount =
    media.episodes                              // finished show: real total
    ?? (nextAiringEpisode.episode - 1)          // airing show: aired so far
    ?? 1                                          // truly unknown: single Play
```

Render `Episode 1 … airedCount` exactly like a finished show. No backend change
needed for the list itself — `/play` already takes an episode number.

This touches ~3 small spots: the GraphQL `MEDIA_FIELDS`, the count logic in
`openDetail`, and `mediaSnapshot` (so Continue Watching keeps working). The
`fetchCatalog`/`searchAnime`/fallback paths all reuse `MEDIA_FIELDS`, so they get
the new fields for free.

## The real risk to decide on: episode-number alignment

The scraper resolves a stream with `--playlist-items N`, where **N is the AniList
episode number** passed straight through
([backend/scraper.py:158-198](backend/scraper.py#L158-L198)). That works only if
the streaming source (anikoto) numbers episodes **the same way AniList does**.

For 12–24 episode seasonal shows this is almost always fine. For long-running
shows it's the danger zone:
- AniList counts One Piece as one continuous series (absolute numbering, 1130+).
- A streaming source may split it into arcs/seasons, or its playlist position may
  not equal the absolute episode number.

So generating 1130 tiles is easy; guaranteeing tile "Episode 1130" actually
resolves to the right stream is the part that needs a real-hardware check. This
is a *playback-resolution* question, separate from the *list-rendering* fix above
— the list fix is safe and worth doing regardless.

## Secondary consideration: very long lists

One Piece would render ~1130 episode tiles in one flat grid. Worth deciding how
that should feel on a 10-foot D-pad UI (a flat 1130-long scroll is rough). Options
live in the open questions below.

## Scope summary

| Piece | Effort | Risk |
|---|---|---|
| Add `status` + `nextAiringEpisode` to query | trivial | none |
| Use aired-count for the episode list | small | none |
| Long-list UX (jump / grouping) | medium | none (UX only) |
| Verify source numbering for long shows | — | needs hardware test |

## Decisions (locked)

1. **Long lists → group by 100s / arcs.** Huge shows collapse into chunks
   (e.g. 1–100, 101–200 … or named arcs) that you drill into, rather than one
   flat 1130-long scroll. Keeps D-pad navigation sane. Most of the UI work lives
   here; the underlying count still comes from the aired-episode fix above.
2. **Order → newest first** for airing shows, so the just-aired episode is
   front-and-center. (This differs from finished shows; decide whether finished
   shows stay ascending or also flip — default: leave finished shows ascending,
   only airing shows reverse.)
3. **Scope this pass → just the list fix** (query fields + aired-count + grouped
   rendering). Do **not** touch scraper numbering yet — defer long-running-show
   stream-resolution alignment until a hardware test shows it's actually wrong.

## What was built (frontend only)

1. Added `status` + `nextAiringEpisode { episode }` to `MEDIA_FIELDS` and to
   `mediaSnapshot` (so Continue Watching cards carry them too).
2. New `airedEpisodeCount(media)`: real total for finished shows, `next - 1` for
   releasing shows, `0` when genuinely unknown.
3. `openDetail` now builds the list from that count. Airing shows (`status ===
   "RELEASING"`) list **newest-first**; finished shows stay ascending.
4. Shows over `CHUNK_SIZE` (100) episodes render a **range picker** (`buildChunks`
   → `renderChunkList`); selecting a range drills into `renderEpisodeList`. Ranges
   are labelled by real episode numbers (e.g. "1031 – 1130"). Reuses the existing
   `.episode` d-pad grid nav — range/back tiles are just `.episode.chunk` buttons.
5. BACK is now `detailBack()`: steps from a drilled-in range up to the picker,
   else closes the show. `applyEpisodeProgress` skips range/back tiles.
6. `/play` and the scraper are **untouched** — long-running-show stream-number
   alignment remains the known follow-up to verify on hardware (see risk above).

Touched: [frontend/app.js](frontend/app.js), [frontend/style.css](frontend/style.css).
