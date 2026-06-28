/* openAnime — UI shell controller
 *
 * Responsibilities:
 *   1. Pull real anime catalog + art from the AniList GraphQL API (live).
 *   2. Render a hero banner + horizontal card rows + a persistent search bar.
 *   3. Drive navigation from IR commands arriving over WebSocket
 *      (and from the keyboard, as a dev fallback).
 *   4. Open a Netflix-style episode-list detail screen on OK.
 *   5. Provide an on-screen d-pad keyboard for search.
 *   6. On play, fire a placeholder POST /play to the backend.
 *
 * The device is online-only by design, so AniList is the source of truth;
 * there is no local cache. A tiny inline fallback exists only so the screen
 * isn't blank if the AniList request itself fails.
 *
 * Navigation is a small screen state machine: each screen owns how it handles
 * the six remote commands (UP/DOWN/LEFT/RIGHT/OK/BACK).
 */

const WS_URL = `ws://${location.hostname || "localhost"}:8765`;
const PLAY_URL = `http://${location.hostname || "localhost"}:8080/play`;
const STOP_URL = `http://${location.hostname || "localhost"}:8080/stop`;
const ANILIST_URL = "https://graphql.anilist.co";

// ---- DOM handles ----
const els = {
  conn: document.getElementById("conn"),
  home: document.getElementById("home"),
  rows: document.getElementById("rows"),
  searchbar: document.getElementById("searchbar"),
  searchLabel: document.getElementById("search-label"),
  heroArt: document.getElementById("hero-art"),
  heroTitle: document.getElementById("hero-title"),
  heroSub: document.getElementById("hero-sub"),
  heroDesc: document.getElementById("hero-desc"),
  overlay: document.getElementById("overlay"),
  overlayText: document.getElementById("overlay-text"),
  detail: document.getElementById("detail"),
  detailArt: document.getElementById("detail-art"),
  detailTitle: document.getElementById("detail-title"),
  detailSub: document.getElementById("detail-sub"),
  detailDesc: document.getElementById("detail-desc"),
  episodes: document.getElementById("episodes"),
  audioToggle: document.getElementById("audio-toggle"),
  keyboard: document.getElementById("keyboard"),
  kbQuery: document.getElementById("kb-query"),
  kbGrid: document.getElementById("kb-grid"),
};

// ---- Screen state ----
// "home" | "detail" | "keyboard" | "playing"
let screen = "home";

// The original catalog, so BACK can restore it after a search replaces the
// home rows with results. `showingResults` tracks whether results are up.
let homeCatalog = null;
let showingResults = false;

// =====================================================================
// Home focus state
//   grid[rowIndex] = array of card elements; focus is a (row, col) cursor.
//   focusRow === SEARCH_ROW (-1) means the search bar is focused.
// =====================================================================
const SEARCH_ROW = -1;
let grid = [];
let focusRow = 0;
let focusCol = 0;

// =====================================================================
// Catalog fetch
// =====================================================================

function currentSeason() {
  const m = new Date().getMonth(); // 0-11
  if (m <= 1 || m === 11) return "WINTER";
  if (m <= 4) return "SPRING";
  if (m <= 7) return "SUMMER";
  return "FALL";
}

const MEDIA_FIELDS = `
  id
  title { english romaji }
  coverImage { extraLarge large color }
  bannerImage
  description(asHtml: false)
  episodes
  averageScore
  genres
`;

async function fetchCatalog() {
  const query = `
    query ($season: MediaSeason, $year: Int) {
      trending: Page(perPage: 16) {
        media(sort: TRENDING_DESC, type: ANIME) { ${MEDIA_FIELDS} }
      }
      season: Page(perPage: 16) {
        media(season: $season, seasonYear: $year, sort: POPULARITY_DESC, type: ANIME) { ${MEDIA_FIELDS} }
      }
      popular: Page(perPage: 16) {
        media(sort: POPULARITY_DESC, type: ANIME) { ${MEDIA_FIELDS} }
      }
    }`;

  const data = await anilist(query, {
    season: currentSeason(),
    year: new Date().getFullYear(),
  });

  return [
    { title: "Trending Now", media: data.trending.media },
    { title: `Popular This Season`, media: data.season.media },
    { title: "All-Time Popular", media: data.popular.media },
  ];
}

async function searchAnime(term) {
  const query = `
    query ($search: String) {
      Page(perPage: 24) {
        media(search: $search, type: ANIME, sort: SEARCH_MATCH) { ${MEDIA_FIELDS} }
      }
    }`;
  const data = await anilist(query, { search: term });
  return data.Page.media;
}

async function anilist(query, variables) {
  const res = await fetch(ANILIST_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!res.ok) throw new Error(`AniList HTTP ${res.status}`);
  const { data, errors } = await res.json();
  if (errors) throw new Error(errors.map((e) => e.message).join("; "));
  return data;
}

// Minimal fallback so navigation is still demoable if AniList is unreachable.
function fallbackCatalog() {
  const make = (n) =>
    Array.from({ length: 8 }, (_, i) => ({
      id: `${n}-${i}`,
      title: { romaji: `Sample ${n} ${i + 1}` },
      coverImage: { color: ["#3a4a8a", "#7a3a6a", "#3a7a5a"][i % 3] },
      description: "AniList unreachable — placeholder card.",
      episodes: 12,
      averageScore: 80,
      genres: ["Action", "Adventure"],
    }));
  return [
    { title: "Trending Now", media: make("Trending") },
    { title: "Popular This Season", media: make("Season") },
    { title: "All-Time Popular", media: make("Popular") },
  ];
}

// =====================================================================
// Rendering — home rows
// =====================================================================

function titleOf(media) {
  return media.title.english || media.title.romaji || "Untitled";
}

function makeCard(media, rowIndex, colIndex) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.row = rowIndex;
  card.dataset.col = colIndex;
  card.dataset.id = media.id;

  const art = document.createElement("div");
  art.className = "card-art";
  const img = media.coverImage?.extraLarge || media.coverImage?.large;
  if (img) {
    art.style.background = `#0a0c14 url("${img}") center/cover no-repeat`;
  } else {
    art.style.background = `linear-gradient(135deg, ${media.coverImage?.color || "#2a2f55"}, #0a0c14)`;
  }

  const scrim = document.createElement("div");
  scrim.className = "card-scrim";

  const title = document.createElement("div");
  title.className = "card-title";
  title.textContent = titleOf(media);

  card.append(art, scrim, title);

  if (media.episodes) {
    const badge = document.createElement("div");
    badge.className = "card-badge";
    badge.textContent = `${media.episodes} ep`;
    card.append(badge);
  }

  card._media = media; // stash for hero updates / detail screen
  return card;
}

function render(catalog) {
  els.rows.innerHTML = "";
  grid = [];

  catalog.forEach((row) => {
    if (!row.media || !row.media.length) return;

    const section = document.createElement("section");
    section.className = "row";

    const heading = document.createElement("h2");
    heading.className = "row-title";
    heading.textContent = row.title;

    const track = document.createElement("div");
    track.className = "row-track";

    const rowCards = [];
    row.media.forEach((media, colIndex) => {
      const card = makeCard(media, grid.length, colIndex);
      track.append(card);
      rowCards.push(card);
    });

    section.append(heading, track);
    els.rows.append(section);
    grid.push(rowCards);
  });

  focusRow = grid.length ? 0 : SEARCH_ROW;
  focusCol = 0;
  applyFocus();
}

// =====================================================================
// Home focus / navigation
// =====================================================================

function clamp(v, max) {
  return Math.max(0, Math.min(v, max));
}

function applyFocus() {
  document.querySelectorAll(".card.focused").forEach((c) => c.classList.remove("focused"));
  els.searchbar.classList.remove("focused");

  if (focusRow === SEARCH_ROW) {
    els.searchbar.classList.add("focused");
    scrollToTop();
    return;
  }

  const card = grid[focusRow]?.[focusCol];
  if (!card) return;
  card.classList.add("focused");

  // Slide the row's track so the focused card is pinned to the left content edge.
  const track = card.parentElement;
  const basePad = parseFloat(getComputedStyle(track).paddingLeft) || 0;
  const offset = Math.min(0, -(card.offsetLeft - basePad));
  track.style.transform = `translateX(${offset}px)`;

  centerRowInView(card.closest(".row"));
  updateHero(card._media);
}

// ---- Camera follow (section 1): keep the focused row vertically centered. ----
function centerRowInView(rowEl) {
  if (!rowEl) return;
  const rect = rowEl.getBoundingClientRect();
  const rowCenter = window.scrollY + rect.top + rect.height / 2;
  // Clamp so the page never scrolls past its natural top/bottom.
  const target = clamp(
    rowCenter - window.innerHeight / 2,
    document.documentElement.scrollHeight - window.innerHeight
  );
  window.scrollTo({ top: target, behavior: "smooth" });
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateHero(media) {
  if (!media) return;
  const banner = media.bannerImage || media.coverImage?.extraLarge || media.coverImage?.large;
  els.heroArt.style.background = banner
    ? `#0a0c14 url("${banner}") center/cover no-repeat`
    : `linear-gradient(135deg, ${media.coverImage?.color || "#2a2f55"}, #0a0c14)`;

  els.heroTitle.textContent = titleOf(media);

  const bits = [];
  if (media.averageScore) bits.push(`★ ${(media.averageScore / 10).toFixed(1)}`);
  if (media.episodes) bits.push(`${media.episodes} episodes`);
  if (media.genres?.length) bits.push(media.genres.slice(0, 3).join(" · "));
  els.heroSub.textContent = bits.join("    ");

  // AniList descriptions can contain <br> / <i>; strip tags for plain text.
  els.heroDesc.textContent = (media.description || "").replace(/<[^>]*>/g, " ").trim();
}

function homeMove(dRow, dCol) {
  // Vertical movement, including hopping to/from the search bar.
  if (dRow < 0) {
    if (focusRow === 0 || focusRow === SEARCH_ROW) {
      focusRow = SEARCH_ROW; // UP from the first row → search bar
    } else {
      focusRow = clamp(focusRow + dRow, grid.length - 1);
      focusCol = clamp(focusCol, grid[focusRow].length - 1);
    }
  } else if (dRow > 0) {
    if (focusRow === SEARCH_ROW) {
      focusRow = grid.length ? 0 : SEARCH_ROW; // DOWN from search bar → first row
    } else {
      focusRow = clamp(focusRow + dRow, grid.length - 1);
      focusCol = clamp(focusCol, grid[focusRow].length - 1);
    }
  }

  if (dCol && focusRow !== SEARCH_ROW) {
    focusCol = clamp(focusCol + dCol, grid[focusRow].length - 1);
  }
  applyFocus();
}

function homeSelect() {
  if (focusRow === SEARCH_ROW) {
    openKeyboard();
    return;
  }
  const card = grid[focusRow]?.[focusCol];
  if (card) openDetail(card._media);
}

// =====================================================================
// Detail (episode list) screen — section 2
// =====================================================================

let detailMedia = null;
let epFocus = 0;
let epCols = 1; // columns per row in the episode grid, for d-pad math
let audioPref = "sub"; // "sub" | "dub" — sticky across shows
let detailZone = "episodes"; // "toggle" (sub/dub pills) | "episodes"

function openDetail(media) {
  detailMedia = media;

  els.detailTitle.textContent = titleOf(media);

  const bits = [];
  if (media.averageScore) bits.push(`★ ${(media.averageScore / 10).toFixed(1)}`);
  if (media.episodes) bits.push(`${media.episodes} episodes`);
  if (media.genres?.length) bits.push(media.genres.slice(0, 3).join(" · "));
  els.detailSub.textContent = bits.join("    ");
  els.detailDesc.textContent = (media.description || "").replace(/<[^>]*>/g, " ").trim();

  const art = media.bannerImage || media.coverImage?.extraLarge || media.coverImage?.large;
  els.detailArt.style.background = art
    ? `#0a0c14 url("${art}") center/cover no-repeat`
    : `linear-gradient(135deg, ${media.coverImage?.color || "#2a2f55"}, #0a0c14)`;

  // Build episode entries: 1..N, or a single "Play" entry when count is unknown.
  els.episodes.innerHTML = "";
  const n = media.episodes;
  if (n && n > 0) {
    for (let i = 1; i <= n; i++) {
      els.episodes.append(makeEpisode(`Episode ${i}`, i));
    }
    els.episodes.classList.remove("single");
  } else {
    els.episodes.append(makeEpisode("Play", 1));
    els.episodes.classList.add("single");
  }

  epFocus = 0;
  detailZone = "episodes";

  // Cross-fade from home to detail first, so the episode grid is laid out
  // (visible) before we measure column count / scroll the focused entry.
  crossFade(els.home, els.detail);
  screen = "detail";
  updateDetailFocus(true);
}

function makeEpisode(label, num) {
  const el = document.createElement("button");
  el.className = "episode";
  el.textContent = label;
  el.dataset.episode = num;
  return el;
}

function updateDetailFocus(recomputeCols) {
  // Sub/Dub pills: mark the chosen one active, and (when the toggle zone holds
  // focus) ring it.
  for (const btn of els.audioToggle.children) {
    const isPref = btn.dataset.audio === audioPref;
    btn.classList.toggle("active", isPref);
    btn.classList.toggle("focused", detailZone === "toggle" && isPref);
  }

  const items = els.episodes.children;
  for (const it of items) it.classList.remove("focused");
  if (detailZone !== "episodes" || !items.length) return;

  // Infer how many columns the flex grid wrapped into, for UP/DOWN math.
  if (recomputeCols) {
    const firstTop = items[0].offsetTop;
    epCols = 0;
    for (const it of items) {
      if (it.offsetTop === firstTop) epCols++;
      else break;
    }
    epCols = Math.max(1, epCols);
  }

  epFocus = clamp(epFocus, items.length - 1);
  const cur = items[epFocus];
  cur.classList.add("focused");
  cur.scrollIntoView({ block: "center", behavior: "smooth" });
}

function detailMove(dRow, dCol) {
  if (detailZone === "toggle") {
    // LEFT/RIGHT pick the track directly; DOWN drops into the episode grid.
    if (dCol < 0) audioPref = "sub";
    else if (dCol > 0) audioPref = "dub";
    else if (dRow > 0) detailZone = "episodes";
    updateDetailFocus(false);
    return;
  }

  const n = els.episodes.children.length;
  if (!n) return;
  // UP from the top row jumps to the Sub/Dub toggle.
  if (dRow < 0 && epFocus < epCols) {
    detailZone = "toggle";
    updateDetailFocus(false);
    return;
  }
  if (dCol) epFocus = clamp(epFocus + dCol, n - 1);
  if (dRow) epFocus = clamp(epFocus + dRow * epCols, n - 1);
  updateDetailFocus(false);
}

function detailSelect() {
  if (detailZone === "toggle") {
    audioPref = audioPref === "sub" ? "dub" : "sub"; // OK flips the pill
    updateDetailFocus(false);
    return;
  }
  const cur = els.episodes.children[epFocus];
  if (!cur) return;
  const episode = parseInt(cur.dataset.episode, 10);
  playEpisode(detailMedia, episode);
}

function closeDetail() {
  crossFade(els.detail, els.home);
  screen = "home";
  // Restore the previously focused card; applyFocus() re-centers it.
  applyFocus();
}

// =====================================================================
// On-screen keyboard + search — section 3
// =====================================================================

// Alphabetical grid. Each cell: { label, type }, where type drives the action.
// type: "char" inserts label; "space"/"del"/"search" are actions.
const KB_LAYOUT = [
  ["A", "B", "C", "D", "E", "F", "G"],
  ["H", "I", "J", "K", "L", "M", "N"],
  ["O", "P", "Q", "R", "S", "T", "U"],
  ["V", "W", "X", "Y", "Z", "0", "1"],
  ["2", "3", "4", "5", "6", "7", "8"],
  ["9", "␣ space", "⌫ del", "⏎ search"],
];

let kbRow = 0;
let kbCol = 0;
let query = "";
let searchTimer = null;
let kbBuilt = false;

function buildKeyboard() {
  els.kbGrid.innerHTML = "";
  KB_LAYOUT.forEach((row, r) => {
    const rowEl = document.createElement("div");
    rowEl.className = "kb-row";
    row.forEach((label, c) => {
      const key = document.createElement("div");
      key.className = "kb-key";
      const type =
        label.includes("space") ? "space" :
        label.includes("del")   ? "del"   :
        label.includes("search")? "search": "char";
      if (type !== "char") key.classList.add("kb-wide", `kb-${type}`);
      key.dataset.type = type;
      key.dataset.value = type === "char" ? label : "";
      key.textContent = label;
      key.dataset.row = r;
      key.dataset.col = c;
      rowEl.append(key);
    });
    els.kbGrid.append(rowEl);
  });
  kbBuilt = true;
}

function openKeyboard() {
  if (!kbBuilt) buildKeyboard();
  query = "";
  kbRow = 0;
  kbCol = 0;
  updateQueryDisplay();
  applyKeyFocus();
  els.keyboard.classList.remove("hidden");
  screen = "keyboard";
}

function closeKeyboard() {
  els.keyboard.classList.add("hidden");
  screen = "home";
  applyFocus();
}

function updateQueryDisplay() {
  els.kbQuery.textContent = query;
  els.searchLabel.textContent = query || "Search";
}

function applyKeyFocus() {
  const rows = els.kbGrid.children;
  kbRow = clamp(kbRow, rows.length - 1);
  kbCol = clamp(kbCol, rows[kbRow].children.length - 1);
  els.kbGrid.querySelectorAll(".kb-key.focused").forEach((k) => k.classList.remove("focused"));
  rows[kbRow].children[kbCol].classList.add("focused");
}

function kbMove(dRow, dCol) {
  const rows = els.kbGrid.children;
  if (dRow) kbRow = clamp(kbRow + dRow, rows.length - 1);
  if (dCol) kbCol = clamp(kbCol + dCol, rows[kbRow].children.length - 1);
  applyKeyFocus();
}

function kbSelect() {
  const key = els.kbGrid.children[kbRow].children[kbCol];
  switch (key.dataset.type) {
    case "char":   query += key.dataset.value; break;
    case "space":  query += " "; break;
    case "del":    query = query.slice(0, -1); break;
    case "search": runSearch(); return; // close keyboard, show results
  }
  updateQueryDisplay();
  scheduleLiveSearch();
}

// Live-as-you-type: debounce so each keystroke doesn't hammer AniList.
function scheduleLiveSearch() {
  clearTimeout(searchTimer);
  const term = query.trim();
  if (term.length < 2) return;
  searchTimer = setTimeout(() => runSearch({ keepKeyboard: true }), 350);
}

async function runSearch({ keepKeyboard = false } = {}) {
  const term = query.trim();
  if (!term) return;
  try {
    const media = await searchAnime(term);
    render([{ title: `Results for “${term}”`, media }]);
  } catch (err) {
    console.error("AniList search failed:", err);
    render([{ title: `Results for “${term}”`, media: [] }]);
  }
  showingResults = true;
  if (!keepKeyboard) closeKeyboard();
}

// Drop the search results and restore the original home catalog.
function restoreHome() {
  showingResults = false;
  render(homeCatalog || fallbackCatalog());
}

// =====================================================================
// Playback
//
// POST /play is a long-lived request: the backend resolves the stream, launches
// mpv over the browser, and only responds once mpv exits. So while we're
// awaiting that fetch we're in the "playing" screen; BACK posts /stop, which
// makes the backend quit mpv and the /play fetch resolve. The overlay sits
// under mpv the whole time, so it's just there for the brief loading window and
// for surfacing errors.
// =====================================================================

function playEpisode(media, episode) {
  const title = titleOf(media);
  screen = "playing";
  showOverlay(`Loading ${title} — Episode ${episode} (${audioPref.toUpperCase()})…`);

  fetch(PLAY_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // The backend scraper resolves the title + episode to a real stream URL.
    body: JSON.stringify({ id: media.id, title, episode, audio: audioPref }),
  })
    .then(async (res) => {
      const data = await res.json().catch(() => ({}));
      console.log("POST /play ->", res.status, data);
      if (!res.ok || data.status === "error") {
        const msg = data.error || `playback failed (${res.status})`;
        showOverlay(`Couldn't play ${title} — ${msg}`);
        setTimeout(endPlayback, 3000);
      } else {
        endPlayback(); // mpv exited or was stopped
      }
    })
    .catch((err) => {
      console.error("POST /play failed:", err);
      showOverlay("Couldn't reach the backend.");
      setTimeout(endPlayback, 3000);
    });
}

// BACK during playback: ask the backend to quit mpv. The /play fetch above then
// resolves and calls endPlayback() — we don't tear down the screen here.
function stopPlayback() {
  showOverlay("Stopping…");
  fetch(STOP_URL, { method: "POST" }).catch((err) =>
    console.error("POST /stop failed:", err)
  );
}

function endPlayback() {
  hideOverlay();
  screen = "detail";
}

function showOverlay(text) {
  els.overlayText.textContent = text;
  els.overlay.classList.remove("hidden");
}
function hideOverlay() {
  els.overlay.classList.add("hidden");
}

// Soft cross-fade between two full-screen screens.
function crossFade(from, to) {
  to.classList.remove("hidden");
  // Force reflow so the fade-in transition actually runs.
  void to.offsetWidth;
  from.classList.add("fade-out");
  to.classList.remove("fade-out");
  setTimeout(() => from.classList.add("hidden"), 300);
}

// =====================================================================
// Command handling (shared by WebSocket + keyboard) — screen state machine
// =====================================================================

function handleCommand(cmd) {
  // During playback the only input that matters is BACK -> stop mpv. Everything
  // else (including the overlay-dismiss shortcut below) is ignored.
  if (screen === "playing") {
    if (cmd === "BACK") stopPlayback();
    return;
  }

  // Outside playback, BACK dismisses a lingering overlay (e.g. an error) first.
  if (cmd === "BACK" && !els.overlay.classList.contains("hidden")) {
    hideOverlay();
    return;
  }

  switch (screen) {
    case "home":
      if (cmd === "UP")    homeMove(-1, 0);
      else if (cmd === "DOWN")  homeMove(1, 0);
      else if (cmd === "LEFT")  homeMove(0, -1);
      else if (cmd === "RIGHT") homeMove(0, 1);
      else if (cmd === "OK")    homeSelect();
      else if (cmd === "BACK") {
        // BACK: restore the catalog if we're viewing search results; otherwise
        // drop from the search bar back down into the rows. At the catalog
        // root with a row focused, BACK is a no-op.
        if (showingResults) restoreHome();
        else if (focusRow === SEARCH_ROW) homeMove(1, 0);
      }
      break;

    case "detail":
      if (cmd === "UP")    detailMove(-1, 0);
      else if (cmd === "DOWN")  detailMove(1, 0);
      else if (cmd === "LEFT")  detailMove(0, -1);
      else if (cmd === "RIGHT") detailMove(0, 1);
      else if (cmd === "OK")    detailSelect();
      else if (cmd === "BACK")  closeDetail();
      break;

    case "keyboard":
      if (cmd === "UP")    kbMove(-1, 0);
      else if (cmd === "DOWN")  kbMove(1, 0);
      else if (cmd === "LEFT")  kbMove(0, -1);
      else if (cmd === "RIGHT") kbMove(0, 1);
      else if (cmd === "OK")    kbSelect();
      // BACK exits the keyboard back to the home search bar. Use the on-screen
      // "del" key to delete a single character.
      else if (cmd === "BACK")  closeKeyboard();
      break;
  }
}

// ---- Keyboard fallback (dev convenience) ----
const KEYMAP = {
  ArrowUp: "UP", ArrowDown: "DOWN", ArrowLeft: "LEFT", ArrowRight: "RIGHT",
  Enter: "OK", Escape: "BACK", Backspace: "BACK",
};
window.addEventListener("keydown", (e) => {
  const cmd = KEYMAP[e.key];
  if (cmd) {
    e.preventDefault();
    handleCommand(cmd);
  }
});

// =====================================================================
// WebSocket link to the backend
// =====================================================================

let wsBackoff = 500;
function connectWS() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    wsBackoff = 500;
    els.conn.textContent = "online";
    els.conn.className = "conn up";
  };
  ws.onmessage = (e) => handleCommand(e.data.trim()); // backend sends raw strings
  ws.onclose = () => {
    els.conn.textContent = "offline";
    els.conn.className = "conn down";
    setTimeout(connectWS, wsBackoff);
    wsBackoff = Math.min(wsBackoff * 2, 8000); // exponential backoff
  };
  ws.onerror = () => ws.close();
}

// =====================================================================
// Boot
// =====================================================================

(async function init() {
  connectWS();
  try {
    homeCatalog = await fetchCatalog();
  } catch (err) {
    console.error("AniList fetch failed, using fallback:", err);
    homeCatalog = fallbackCatalog();
  }
  render(homeCatalog);
})();
