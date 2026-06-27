/* openAnime — UI shell controller
 *
 * Responsibilities:
 *   1. Pull real anime catalog + art from the AniList GraphQL API (live).
 *   2. Render a hero banner + horizontal card rows.
 *   3. Drive a 2D focus grid from IR commands arriving over WebSocket
 *      (and from the keyboard, as a dev fallback).
 *   4. On OK, fire a placeholder POST /play to the backend.
 *
 * The device is online-only by design, so AniList is the source of truth;
 * there is no local cache. A tiny inline fallback exists only so the screen
 * isn't blank if the AniList request itself fails.
 */

const WS_URL = `ws://${location.hostname || "localhost"}:8765`;
const PLAY_URL = `http://${location.hostname || "localhost"}:8080/play`;
const ANILIST_URL = "https://graphql.anilist.co";

// ---- DOM handles ----
const els = {
  conn: document.getElementById("conn"),
  rows: document.getElementById("rows"),
  heroArt: document.getElementById("hero-art"),
  heroTitle: document.getElementById("hero-title"),
  heroSub: document.getElementById("hero-sub"),
  heroDesc: document.getElementById("hero-desc"),
  overlay: document.getElementById("overlay"),
  overlayText: document.getElementById("overlay-text"),
};

// ---- Focus state ----
// grid[rowIndex] = array of card elements; focus is a (row, col) cursor.
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

  const res = await fetch(ANILIST_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      query,
      variables: { season: currentSeason(), year: new Date().getFullYear() },
    }),
  });
  if (!res.ok) throw new Error(`AniList HTTP ${res.status}`);
  const { data, errors } = await res.json();
  if (errors) throw new Error(errors.map((e) => e.message).join("; "));

  return [
    { title: "Trending Now", media: data.trending.media },
    { title: `Popular This Season`, media: data.season.media },
    { title: "All-Time Popular", media: data.popular.media },
  ];
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
// Rendering
// =====================================================================

function titleOf(media) {
  return media.title.english || media.title.romaji || "Untitled";
}

function makeCard(media, rowIndex, colIndex) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.row = rowIndex;
  card.dataset.col = colIndex;
  // Placeholder play target until M4 wires real stream extraction.
  card.dataset.url = `placeholder://anilist/${media.id}`;
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

  card._media = media; // stash for hero updates
  return card;
}

function render(catalog) {
  els.rows.innerHTML = "";
  grid = [];

  catalog.forEach((row, rowIndex) => {
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

  focusRow = 0;
  focusCol = 0;
  if (grid.length) applyFocus();
}

// =====================================================================
// Focus / navigation
// =====================================================================

function clamp(v, max) {
  return Math.max(0, Math.min(v, max));
}

function applyFocus() {
  const card = grid[focusRow]?.[focusCol];
  if (!card) return;

  document.querySelectorAll(".card.focused").forEach((c) => c.classList.remove("focused"));
  card.classList.add("focused");

  // Slide the row's track so the focused card is pinned to the left content edge.
  const track = card.parentElement;
  const basePad = parseFloat(getComputedStyle(track).paddingLeft) || 0;
  const offset = Math.min(0, -(card.offsetLeft - basePad));
  track.style.transform = `translateX(${offset}px)`;

  updateHero(card._media);
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

function move(dRow, dCol) {
  if (!grid.length) return;
  if (dRow) {
    focusRow = clamp(focusRow + dRow, grid.length - 1);
    focusCol = clamp(focusCol, grid[focusRow].length - 1);
  }
  if (dCol) {
    focusCol = clamp(focusCol + dCol, grid[focusRow].length - 1);
  }
  applyFocus();
}

// =====================================================================
// Command handling (shared by WebSocket + keyboard)
// =====================================================================

function handleCommand(cmd) {
  switch (cmd) {
    case "UP":    move(-1, 0); break;
    case "DOWN":  move(1, 0);  break;
    case "LEFT":  move(0, -1); break;
    case "RIGHT": move(0, 1);  break;
    case "OK":    select();    break;
    case "BACK":  goBack();    break;
    default: console.warn("unknown command:", cmd);
  }
}

async function select() {
  const card = grid[focusRow]?.[focusCol];
  if (!card) return;

  showOverlay(`Loading ${titleOf(card._media)}…`);
  try {
    const res = await fetch(PLAY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: card.dataset.url, id: card.dataset.id }),
    });
    console.log("POST /play ->", res.status, await res.text().catch(() => ""));
  } catch (err) {
    console.error("POST /play failed:", err);
  }
  // Placeholder: no real playback yet (M4). Drop the overlay shortly.
  setTimeout(hideOverlay, 1200);
}

function goBack() {
  // During real playback this will tell the backend to stop mpv. For the
  // shell, just dismiss the overlay if it's up.
  if (!els.overlay.classList.contains("hidden")) hideOverlay();
}

function showOverlay(text) {
  els.overlayText.textContent = text;
  els.overlay.classList.remove("hidden");
}
function hideOverlay() {
  els.overlay.classList.add("hidden");
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
    render(await fetchCatalog());
  } catch (err) {
    console.error("AniList fetch failed, using fallback:", err);
    render(fallbackCatalog());
  }
})();
