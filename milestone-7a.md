# Milestone 7A — Episode Progress Tracking

**Goal:** Whenever an episode plays, the backend records how far you got. The
detail screen then shows a thin progress bar along the bottom of each episode
tile, and re-playing an episode resumes near where you left off.

This is the foundation milestone. It only adds **persistence + display of
per-episode progress**; the "Continue Watching" home row is built on top of it
in [Milestone 7B](milestone-7b.md).

---

## Why this works with the current architecture (read first)

The backend already owns the whole viewing session: `POST /play` in
[backend/app.py](backend/app.py) is a **long-lived blocking request** — it
launches mpv and doesn't return until mpv exits (naturally or via `/stop`). mpv
also already exposes a **JSON IPC socket** (`--input-ipc-server`,
`OPENANIME_MPV_IPC`, added in M6) that the backend writes to for pause/seek.

So we already know, on the backend, exactly:
- **which** episode is playing (`title`, `episode`, `audio` come in the `/play` body),
- **when** it starts (`play_blocking` begins) and **when** it ends (`proc.wait()` returns).

What we're missing is **position**. The M6 helper `_mpv_ipc()` is write-only
(`cycle pause`, `seek`, `show-progress`). To capture progress we need to *read*
mpv properties (`time-pos`, `duration`), which means parsing IPC responses.

### Capturing position — chosen approach

A **background poller thread** started inside `play_blocking`, right after mpv
launches:
- every few seconds, query mpv over IPC for `time-pos` and `duration`,
- keep the latest `(position, duration)` in memory for this session,
- when `proc.wait()` returns (mpv exited), persist the final snapshot.

This handles all three exit paths uniformly: BACK→`/stop`, the remote quitting
mpv, and natural end-of-file. We do **not** try to read position *after* mpv
exits — the socket is already gone by then; the poller's last reading is what we
persist.

> Rejected alternative: mpv's built-in `--save-position-on-quit` /
> `watch_later` resume files. They're keyed by a hash of the media path, but our
> URLs are tokenized `.m3u8` links that change every extraction, so the key
> wouldn't match across sessions. We key by **AniList id + episode** instead.

### Reading mpv IPC responses (new vs. M6)

M6's `_mpv_ipc()` fires-and-forgets. For polling we need a request/response
helper that:
- sends `{"command":["get_property","time-pos"], "request_id":N}`,
- reads newline-delimited JSON back until it sees the matching `request_id`
  (mpv interleaves unsolicited `event` messages on the same socket — skip those),
- returns the value, or `None` if idle/unavailable (e.g. `time-pos` is null
  before the first frame decodes).

Keep the same idle-guard discipline as M6: no-op cleanly when `_mpv_proc` is
`None`/not running.

---

## Data model

### Store location & format
- A single JSON file: `progress.json`, under a data dir resolved from a new
  `OPENANIME_DATA_DIR` env (default `~/.local/share/openanime/`). **Not** in the
  repo working tree — OTA is `git pull` (M5 stretch), so repo files must stay
  clean. The systemd service runs as `user`, so the default resolves to
  `/home/user/.local/share/openanime/progress.json`.
- JSON over SQLite: single-user, a few hundred entries at most, read in full on
  each request. SQLite stays available as a later swap if the file ever grows,
  but it's overkill here.

### Record shape
Keyed by `"<anilistId>:<episode>"`. Each record:

```json
{
  "12345:3": {
    "anilistId": 12345,
    "episode": 3,
    "position": 742.5,
    "duration": 1421.0,
    "percent": 0.52,
    "completed": false,
    "updatedAt": 1730000000,
    "title": "Frieren",
    "audio": "sub",
    "media": { "...": "lightweight AniList snapshot, see 7B" }
  }
}
```

- `percent` is precomputed (`position/duration`) so the frontend doesn't divide
  by a possibly-null duration.
- `completed`: set `true` when `percent >= COMPLETE_THRESHOLD` (default **0.90**,
  env `OPENANIME_COMPLETE_PCT`). A completed episode shows a *full* bar in 7A and
  is *excluded* from Continue Watching in 7B.
- `media` snapshot is included now (cheap) so 7B doesn't need a schema change.
  The frontend passes it in the `/play` body (see below).

### Concurrency
Only one episode plays at a time (`_play_lock` already serializes playback), and
the HTTP server is threaded. Guard all reads/writes of `progress.json` with a
dedicated lock and write atomically (write a temp file, then `os.replace`) so a
crash mid-write can't corrupt the store.

---

## Backend changes — [backend/app.py](backend/app.py)

1. **Progress store module/helpers**: `load_progress()`, `save_progress_record(record)`
   (atomic write under a lock), `get_progress()` (whole map). Resolve
   `OPENANIME_DATA_DIR`, create it on first write.

2. **IPC read helper**: `_mpv_get(prop)` — request/response over the existing
   socket, skipping `event` messages, matching `request_id`. Idle-safe.

3. **Poller**: inside `play_blocking`, after mpv starts, spin a daemon thread
   that loops every `OPENANIME_POLL_SECS` (default 5s) calling `_mpv_get` for
   `time-pos`/`duration`, updating an in-session snapshot. Stop when
   `_mpv_proc` is no longer this process. On `proc.wait()` return, persist the
   final snapshot via `save_progress_record` (compute `percent`/`completed`).

4. **Resume on replay**: in `play_blocking`, before building the mpv command,
   look up the saved record for this `(anilistId, episode)`. If it exists and is
   **not** completed and `position` is past a small floor (e.g. > 15s) and not
   within the last ~30s, append `--start=<position>` to the mpv args so playback
   resumes where it stopped. (This is the natural payoff of tracking; flagged as
   a decision below.)

5. **`/play` body gains `anilistId` and `media`**: `_handle_play` currently
   reads `title`, `episode`, `audio`, `url`. Add `anilistId` (already sent as
   `id` from the frontend — standardize the key) and the `media` snapshot, and
   thread them into `play_blocking` so the persisted record carries them.

6. **New endpoint `GET /progress`**: `do_GET` is currently the inherited static
   file server. Intercept `path == "/progress"` and return the full progress map
   as JSON (CORS headers already applied via `end_headers`). Everything else
   falls through to `SimpleHTTPRequestHandler` static serving.

### Env knobs (follow existing convention)
- `OPENANIME_DATA_DIR` — store directory (default `~/.local/share/openanime`)
- `OPENANIME_POLL_SECS` — position poll interval (default `5`)
- `OPENANIME_COMPLETE_PCT` — completion threshold (default `0.90`)

### Don't break
- The blocking `/play` contract and its `wmctrl` hide/restore are untouched —
  the poller is a side thread; persistence happens after `proc.wait()` returns,
  inside the existing post-playback section.
- `_play_lock`/`_mpv_proc` invariants stay as-is. The poller reads liveness
  under the lock exactly like `_mpv_ipc`.
- Pause (`time-pos` stops advancing) is fine — we just keep recording the same
  position.

---

## Frontend changes — [frontend/app.js](frontend/app.js) + [frontend/style.css](frontend/style.css)

1. **Fetch progress on boot** (and after returning from playback): `GET /progress`
   into a module-level `progressByKey` map. Refresh it in `endPlayback()` so the
   bar reflects the session that just ended.

2. **Send richer `/play` body**: `playEpisode` already posts `id, title,
   episode, audio`. Add a compact `media` snapshot (the fields the detail/home
   render uses: `id`, `title`, `coverImage`, `bannerImage`, `description`,
   `episodes`, `averageScore`, `genres`) and send `anilistId: media.id`.

3. **Render the bar in the detail list**: in `makeEpisode` (or `openDetail`'s
   loop), look up `progressByKey["<media.id>:<num>"]`. If present, append a
   `<div class="ep-progress">` with an inner fill whose width is `percent*100%`.
   Completed episodes render a full (or differently-colored "watched") bar.

4. **CSS**: a thin bar pinned to the bottom edge of `.episode` (absolute,
   `height: ~4px`, accent fill, muted track). Make sure `.episode` is
   `position: relative` so the bar anchors to the tile.

---

## Tasks
- [x] Progress store: load/save with atomic write + lock; resolve `OPENANIME_DATA_DIR`
- [x] `_mpv_get(prop)` IPC request/response helper (skips events, matches request_id, idle-safe)
- [x] Position poller thread in `play_blocking`; persist final snapshot on mpv exit
- [x] Completion threshold → `completed`/`percent` on the record
- [x] `--start=<position>` resume on replay of an unfinished episode
- [x] `/play` body: accept + persist `anilistId` and `media` snapshot
- [x] `GET /progress` endpoint returning the full map
- [x] Frontend: fetch `/progress` on boot + after playback; send richer `/play` body
- [x] Frontend: progress bar under each episode tile; CSS
- [x] Dev-machine test with a local file (point scraper/`url` at a seekable file): play partway, BACK, confirm `progress.json` updates; reopen detail → bar shows; replay → resumes near the saved spot; play to >90% → marked completed (full bar)
- [x] On-hardware test via remote: watch part of an episode, exit, confirm the bar and resume behave

**Done when:** Playing any episode records its position; the detail screen draws
a progress bar at the bottom of each watched episode tile (full/"watched" once
past the completion threshold); and replaying an unfinished episode resumes near
where you stopped — all persisted across reboots, no keyboard.

---

## Open decisions (confirm before building)
1. **Auto-resume in mpv** via `--start=<position>` — included above. If you'd
   rather the bar be purely informational and always start episodes from 0, drop
   task 5. *Recommendation: keep it; it's the obvious payoff and low-risk.*
2. **Completion threshold** — default 90%. Reasonable for skipping outros.
3. **Store format** — JSON. SQLite only if it ever outgrows a single file.
