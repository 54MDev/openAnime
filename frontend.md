# Frontend — Planned Features

Spec for frontend work. **Implementation status (2026-06-27): all three
sections below are built** against the placeholder `POST /play` (everything
that does not require Milestone 4). Decisions made during the build: alphabetical
on-screen keyboard, live-as-you-type search (debounced ~350ms), and a single
"Play" entry when AniList episode count is null. M4 (real stream resolution)
remains the only outstanding dependency. This file captures the agreed behavior.

Existing shell (already built, working): live AniList catalog, hero banner, card
rows, 2D focus grid driven by the remote over WebSocket, `OK` → placeholder
`POST /play`, keyboard fallback. The features below extend that shell.

Remote has six buttons only: `UP` / `DOWN` / `LEFT` / `RIGHT` / `OK` / `BACK`.
Every interaction must be reachable with just those.

---

## 1. Camera follow (vertical scroll)

**Problem today:** moving `DOWN`/`UP` changes which row is focused, but the
viewport does not scroll — the focused row can end up off-screen. Horizontal
movement within a row already works fine; this is vertical only.

**Desired behavior:** keep the **focused row vertically centered** in the
viewport.

- When focus moves to a new row, the page scrolls so that row sits at the
  vertical center of the screen.
- The scroll is animated/smooth (matches the existing focus-ring transition
  feel), not an instant jump.
- The hero banner scrolls away normally as the user moves down through rows.
- Horizontal within-row behavior is unchanged.

**Notes / to refine when building:**
- Decide centering for the very first and very last rows (a strictly centered
  first row would leave empty space above the hero — likely clamp so the page
  never scrolls past its natural top/bottom).

---

## 2. Episode list screen (Netflix-style detail view)

**Trigger:** pressing `OK` on a show card. Instead of immediately playing, it
opens a dedicated detail screen for that show.

**Transition:** the home grid fades out and the episode screen fades in (a
soft cross-fade, like Netflix opening a title). `BACK` reverses it — fades back
to the home grid with the previously focused card still focused.

**Content of the detail screen:**
- Show hero/art, title, and metadata (score, genres, description) — reuse the
  data we already pull from AniList.
- An **episode list** the user can scroll through with the remote.

**Episode data source (decided):** episodes are **numbered 1..N from AniList's
episode count**. The screen renders one entry per episode (Episode 1 … Episode
N). Selecting an episode hands `"<title> episode N"` to the backend/scraper at
play time, which resolves the actual stream URL (M4 work). The frontend does not
need real per-episode URLs up front.

**Navigation on the detail screen:**
- D-pad moves focus through the episode entries (`OK` plays the focused
  episode; this is where the real `POST /play` flow fires later).
- `BACK` returns to the home grid.

**Notes / to refine when building:**
- Episode layout: grid vs. vertical list, how many visible at once, and whether
  the list also needs the centered-scroll behavior from section 1.
- Shows where AniList episode count is `null` (ongoing/unknown) — how to render
  (e.g. fall back to a single "Play" entry, or hide the count).
- Exact play payload shape (`{title, episode}` vs a constructed query string) is
  a backend/scraper concern for M4; the frontend just needs to send enough for
  the scraper to find the episode.

---

## 3. Search

**Entry point (decided):** a persistent **search bar at the top** of the home
screen, above the first card row. Pressing `UP` from the first row moves focus
up to the search bar. `OK` on the search bar opens the on-screen keyboard.

**Text input (decided):** an **on-screen D-pad keyboard** — a grid of characters
navigated with the remote. `OK` types the focused character. No second device
required.

- Needs the usual keys: A–Z, 0–9, space, backspace/delete, and a "search"/submit
  action. `BACK` should close the keyboard (and/or clear).
- As the query builds, results update (live or on submit — to decide when
  building).

**Search results:** query AniList's search and render the matches as cards
(same card style as the home rows). Selecting a result opens that show's
**episode list screen** (section 2) — same flow as selecting a show from home.

**Notes / to refine when building:**
- Live-as-you-type vs. submit-to-search.
- Keyboard layout (QWERTY vs. alphabetical grid) and how `BACK` behaves
  (delete one char vs. close keyboard).
- Possible later enhancement: phone-based text entry over the existing
  WebSocket (faster typing) — not in scope now, on-screen keyboard is primary.

**ON HOLD — search relevance (do not build yet):** current search returns
poor/irrelevant matches (e.g. typing "KONOSUBA" surfaces unrelated shows). We
want results ranked by **how closely the title matches the typed keyword**, so
the obvious name match is at/near the top. To investigate when we build:
- The AniList query already passes `sort: SEARCH_MATCH`; confirm whether the
  noise comes from live-as-you-type firing on 2–3 char fragments (intermediate
  junk) vs. AniList's ranking itself.
- Options: only show results once the query is longer / on submit; client-side
  re-rank by string similarity between the query and english/romaji titles;
  prefer exact/prefix matches; drop very-low-relevance entries.
- Decide which title field to match against (english vs. romaji vs. synonyms).

---

## 4. Currently airing anime (new-episode tracking) — ON HOLD, not built yet

**Why this matters:** fast, accurate currently-airing support is a flagship
feature. The biggest weakness of commercial OTT providers is that their
currently-airing metadata updates too slowly. We avoid that entirely.

This breaks into two problems, and **both are essentially already solved**:

**(a) Knowing what aired and when — solved by the AniList API.** AniList's
airing data updates within minutes of broadcast (community/automation-driven),
faster and more accurate than typical OTT metadata. Everything we need is in the
API, no extra infrastructure and no caching (fits the online-only design):
- `AiringSchedule`: per-episode `airingAt` (UTC unix timestamp), `episode`
  number, `timeUntilAiring`.
- `Media.nextAiringEpisode`: next episode number + when it airs.
- `Media.status: RELEASING` to filter for currently-airing shows.

Planned UI use:
- A top **"Airing Now / New This Week"** row, from `status_in: [RELEASING]`
  (or the airing-schedule endpoint windowed to the last/next 7 days).
- On the episode-list screen (section 2): derive the latest aired episode from
  `nextAiringEpisode.episode - 1`, gray out not-yet-aired episodes, and show a
  live "airs in 2d 4h" countdown on the next one.
- Periodic re-fetch to stay current; no local cache.

**(b) Actually finding the new episode's stream — solved by fast fan sources.**
This lives in M4 (the scraper), not the UI. The source sites (animepahe/anikoto/
reanime) post new **subbed** episodes roughly **30 min – 1 hr after official
release** — fast enough that "new episode → playable" feels immediate. To make
this reliable, **multi-source fallback** (currently a stretch goal) should be a
first-class M4 requirement: if site A doesn't have episode N yet, retry B/C.

**Open questions to settle when building (deferred):**
- Subs-only for the airing path (fast), or also surface dubs (lag weeks)?
- "Airing Now" as a row on the home grid, or its own dedicated screen/tab?
- Per-show "following" (newest episode of followed shows floats to top) vs. a
  flat schedule-driven row for everyone.
- Timezone handling: `airingAt` is UTC unix — convert to device local time for
  countdowns.

---

## Build order (suggested, to confirm later)

1. Camera follow — small, self-contained, improves the current shell immediately.
2. Episode list screen — core functionality; unblocks real playback in M4.
3. Search — depends on the episode screen (results lead into it).

---

## Dependency on Milestone 4 (the scraper)

Goal: finish the **entire UI** first against the existing placeholder
`POST /play`, then add the scraper (M4) last so playback "just works" on top of a
done UI. Almost everything below can be fully built and tested **without** M4.

The only thing M4 actually unblocks is turning a play request into a real video
stream. Until then, every `OK`-to-play action hits the existing placeholder
`/play` endpoint (which logs + returns OK) and can show the loading overlay — so
the full UI flow is demoable end-to-end without a working scraper.

| Feature | Buildable before M4? | What (if anything) needs M4 |
|---|---|---|
| **1. Camera follow** | ✅ Fully | Nothing — pure UI/navigation. |
| **2. Episode list screen** | ✅ The whole screen: fade transition, AniList metadata, numbered episodes, D-pad nav, `BACK` | Only the final step — `OK` on an episode resolving to a **real stream** (`<title> episode N` → scraper → mpv). Until M4 it just calls placeholder `/play`. |
| **3. Search** | ✅ Fully: search bar, on-screen keyboard, AniList query, results as cards, opening the episode screen | Nothing search-specific — search uses AniList, not the scraper. (Playback from a result inherits the same single M4 dependency as above.) |

**Bottom line:** all three features can be built now. M4 is a single, isolated
swap at the end — replace the placeholder `/play` with the real scraper + mpv
launch — and nothing in the UI has to change to accommodate it.
