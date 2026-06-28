# Milestone 7B — Continue Watching Row

**Goal:** The home screen shows a **"Continue Watching"** row as its first row,
listing every series you have an unfinished episode in, most-recent first.
Selecting a card opens that series' normal detail/episode-list screen (where the
M7A progress bars show exactly which episode to resume), so you never have to
re-search for a show you were already watching.

**Depends on [Milestone 7A](milestone-7a.md).** 7A already persists per-episode
progress *and* a lightweight AniList `media` snapshot per record. 7B is almost
entirely a **frontend** feature that reads that store and renders a row — no new
playback or scraping logic.

---

## Why little new backend is needed

7A's `progress.json` records each carry:
- `anilistId`, `episode`, `percent`, `completed`, `updatedAt`,
- a `media` snapshot (the fields needed to draw a card and reopen the detail
  screen: `id`, `title`, `coverImage`, `bannerImage`, `description`, `episodes`,
  `averageScore`, `genres`).

That snapshot is the key design choice: the home screen is normally rendered
from a **live AniList fetch**, but a Continue Watching card must be drawable from
the store alone (no extra AniList round-trip, instant render). Because we stored
the snapshot at play time, the frontend can reconstruct a full `media` object
and hand it straight to the existing `openDetail(media)` — the detail screen
doesn't care whether the media came from AniList or from the store.

**Optional backend nicety:** a `GET /continue` endpoint that returns the store
already collapsed to one entry per series (newest in-progress episode per
`anilistId`, completed-only series excluded, sorted by `updatedAt`). Otherwise
the frontend does that grouping itself from `GET /progress`. *Recommendation: do
the grouping in the frontend* — it already fetches `/progress` for 7A, and
keeping the logic client-side avoids a second endpoint. Use `GET /continue` only
if you later want other clients.

---

## What "Continue Watching" contains

From the progress map, build one entry **per series** (`anilistId`):
- Consider only episodes that are **in progress** — `completed === false` and
  `percent` above a small floor (e.g. > 2%, so an accidental 3-second open
  doesn't pin a show to the row).
- A series qualifies if it has **at least one** such episode.
- Sort series by the most recent `updatedAt` among their records (most recently
  watched first).
- Each card represents the **series**, not a single episode (matching the user's
  flow: card → episode list → pick). The card may optionally surface "Ep N" of
  the most-recent in-progress episode as a sublabel.

A series drops off the row automatically once all its tracked episodes are
`completed` (or were never started). No explicit "remove" is required for the
MVP; a BACK-style "remove from row" is a possible later add.

---

## Frontend changes — [frontend/app.js](frontend/app.js)

The home grid is already data-driven: `render(catalog)` takes an array of
`{ title, media }` rows and builds the focus `grid`. Adding a row is just
prepending one more entry — the existing d-pad navigation, focus, and hero
update all work unchanged.

1. **Build the continue list**: a `buildContinueRow()` that reads
   `progressByKey` (the 7A map), groups by `anilistId`, applies the in-progress
   filter, sorts by newest `updatedAt`, and returns
   `{ title: "Continue Watching", media: [ ...reconstructed media snapshots... ] }`.

2. **Prepend it in `fetchCatalog`/render**: when the continue list is non-empty,
   make it the **first** row of the home catalog, ahead of "Trending Now". When
   it's empty (fresh device, nothing watched), omit it entirely — don't render
   an empty row. Keep this in `homeCatalog` so BACK-from-search restores it too
   (see `restoreHome`).

3. **Card → detail**: cards already store `card._media` and `homeSelect()` calls
   `openDetail(card._media)`. Because the continue entries *are* media snapshots,
   selecting one opens the normal detail screen with no special-casing. There the
   7A progress bars indicate which episode to resume, and selecting it resumes
   via 7A's `--start`.

4. **Keep it fresh**: rebuild the continue row after playback ends (`endPlayback`
   already a good hook — it re-fetches `/progress` in 7A) and on boot. If a row
   was focused when playback started, re-applying focus after a re-render should
   keep the experience smooth (the row order may shift as the just-watched show
   jumps to the front — acceptable; just re-clamp focus).

5. **Optional polish**: the continue cards can reuse the existing
   `card-badge`/`card-scrim`; consider a small "▶ Continue" or "Ep N" badge to
   distinguish them from catalog cards. Not required for "done."

No CSS changes are strictly required — continue cards reuse `.card`. The 7A
progress bar already lives in the detail list; optionally add a mini bar to the
continue card art too (reuse 7A's `.ep-progress` styling) so the row hints at
how far along each show is.

---

## Edge cases
- **Snapshot missing fields**: AniList media without `bannerImage`/`episodes`
  already has fallbacks in `updateHero`/`openDetail`/`makeCard`; the same code
  paths handle reconstructed snapshots. No new handling needed.
- **Series fully completed**: excluded by the `completed` filter — falls off
  the row.
- **Stale/renamed shows**: the snapshot is a point-in-time copy; we never
  re-validate against AniList. Fine for this single-user appliance.
- **Empty store**: no Continue Watching row at all (clean home screen).

---

## Tasks
- [x] `buildContinueRow()` — group `/progress` by series, filter in-progress, sort by `updatedAt`
- [x] Prepend "Continue Watching" as the first home row when non-empty; omit when empty
- [x] Ensure it's part of `homeCatalog` so search-restore (`restoreHome`) keeps it
- [x] Rebuild the row on boot and after `endPlayback`
- [x] Verify card selection opens the normal detail screen via the stored snapshot
- [x] (Optional) distinguishing badge / mini progress bar on continue cards
- [x] Dev-machine test: watch part of show A and show B, return home → both appear, most-recent first; finish show A's episode past the threshold → it leaves the row (if no other in-progress episodes)
- [x] On-hardware test via remote: partial-watch a show, exit, confirm it's the first home row and reopening it lands on the episode list

**Done when:** After watching part of any episode, the show appears as the first
card in a "Continue Watching" row on the home screen; selecting it opens that
series' episode list (with 7A's progress bars); the row is ordered most-recent
first and drops shows once their episodes are completed — all from the remote,
no re-searching.

---

## Open decisions (confirm before building)
1. **Grouping location** — frontend (recommended) vs. a `GET /continue` backend
   endpoint. Frontend keeps it simple and reuses the 7A fetch.
2. **In-progress floor** — default >2% so trivial opens don't stick.
3. **Card granularity** — per *series* (recommended, matches the described flow)
   vs. per *episode*. Per-series keeps the row short and lands on the episode
   list as the user described.
