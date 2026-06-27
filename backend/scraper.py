#!/usr/bin/env python3
"""openAnime stream scraper.

Turns "what the user picked in the UI" (a title + episode number) into a direct,
playable HLS URL plus the HTTP headers mpv needs to fetch it.

Why this shape:
  The obvious plan (yt-dlp against animepahe / AllAnime) is dead headlessly:
  those sites now serve Cloudflare *interactive* challenges ("Just a moment…")
  that no plain HTTP client — yt-dlp included — can pass without a real browser.
  anikoto.cz, by contrast, runs Cloudflare in plain CDN mode (no challenge), so
  it's reachable from a headless device.

Pipeline (anikoto provider):
  1. title -> anikoto.cz search -> episode-list ("watch") page URL  [plain HTTP]
  2. watch URL + episode N -> yt-dlp (with the vendored, patched anikoto plugin)
     -> direct .m3u8 URL + http_headers (Referer/User-Agent are required; the
        stream 403s without them)

The extracted stream is referer-gated, so get_stream_url() returns the headers
too and app.py forwards them to mpv.

Providers live in PROVIDER_ORDER; the first that yields a stream wins. anikoto is
the only working one today; reanime/others can be added as more sites are
verified. Sites change constantly — keep yt-dlp current (`yt-dlp -U`).

CLI (Roadmap M4 manual test):
    python3 backend/scraper.py --title "Frieren" --episode 1
    python3 backend/scraper.py "https://anikoto.cz/watch/<slug>-<id>" --episode 1
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# yt-dlp invocation knobs (overridable via env so the device can tune them).
YTDLP = os.environ.get("OPENANIME_YTDLP", "yt-dlp")
# Cap at 1080p, single muxed stream (asking for bestvideo+bestaudio would yield
# two URLs). anikoto serves HLS, so "best" picks one master/variant playlist.
YTDLP_FORMAT = os.environ.get("OPENANIME_FORMAT", "best[height<=1080]/best")
YTDLP_TIMEOUT = int(os.environ.get("OPENANIME_YTDLP_TIMEOUT", "90"))
# Directory holding the vendored yt-dlp plugins (the patched anikoto extractor).
PLUGIN_DIR = os.environ.get(
    "OPENANIME_PLUGIN_DIR", str(Path(__file__).resolve().parent / "plugins")
)

HTTP_TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class ScrapeError(Exception):
    """Raised when no provider could resolve a playable stream."""


class Stream:
    """A resolved, playable stream: direct URL + the headers needed to fetch it."""

    __slots__ = ("url", "headers", "page")

    def __init__(self, url, headers, page):
        self.url = url
        self.headers = headers or {}
        self.page = page

    def __repr__(self):
        return f"Stream(url={self.url!r}, page={self.page!r}, headers={self.headers!r})"


def _is_url(s):
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def get_stream_url(target=None, *, title=None, episode=1):
    """Resolve a target to a Stream. Raises ScrapeError if nothing resolves.

    `target` may be a watch-page URL (anything the plugins recognize) or, when
    omitted, `title` is searched across providers. `episode` selects the entry.
    """
    try:
        episode = int(episode)
    except (TypeError, ValueError):
        episode = 1

    # Direct URL: hand straight to yt-dlp (skips the search step).
    if _is_url(target):
        stream = _ytdlp_extract(target, episode)
        if stream:
            return stream
        raise ScrapeError(f"yt-dlp could not extract a stream from {target}")

    title = title or target
    if not title:
        raise ScrapeError("no URL or title to resolve")

    errors = []
    for name in PROVIDER_ORDER:
        find = PROVIDERS[name]
        try:
            page = find(title)
        except Exception as e:  # network / parse / site-change failures
            errors.append(f"{name}: {e}")
            continue
        if not page:
            errors.append(f"{name}: no match for {title!r}")
            continue
        stream = _ytdlp_extract(page, episode)
        if stream:
            return stream
        errors.append(f"{name}: yt-dlp could not extract ep {episode} from {page}")

    raise ScrapeError("all providers failed -> " + "; ".join(errors))


def _ytdlp_extract(page_url, episode):
    """Run yt-dlp on a watch page; return a Stream for the given episode or None.

    Uses `-j --playlist-items N` so only episode N is extracted (one JSON line),
    and reads `http_headers` so the referer-gated stream stays playable in mpv.
    """
    cmd = [
        YTDLP, "--plugin-dirs", PLUGIN_DIR,
        "--no-warnings", "--ignore-no-formats-error",
        "-f", YTDLP_FORMAT,
        "--playlist-items", str(episode),
        "-j", page_url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
    except FileNotFoundError:
        raise ScrapeError(f"{YTDLP} not found (install yt-dlp >= 2025.12.08)")
    except subprocess.TimeoutExpired:
        print(f"[scraper] yt-dlp timed out on {page_url}", file=sys.stderr)
        return None

    if proc.returncode != 0 and not proc.stdout.strip():
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown error"
        print(f"[scraper] yt-dlp failed on {page_url}: {err}", file=sys.stderr)
        return None

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = info.get("url")
        if url:
            return Stream(url=url, headers=info.get("http_headers"), page=page_url)
    return None


# =====================================================================
# Providers: title -> watch-page URL (or None if not found)
#
# Each provider only needs to find the series' episode-list page; yt-dlp's
# extractor does the heavy lifting from there. To add reanime/others, write a
# search function and register it in PROVIDERS / PROVIDER_ORDER.
# =====================================================================

def _get_text(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


ANIKOTO_BASE = os.environ.get("OPENANIME_ANIKOTO", "https://anikoto.cz").rstrip("/")


def _norm(s):
    """Lowercase, strip punctuation to spaces, collapse — for fuzzy matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def anikoto(title):
    """Search anikoto.cz and return the best-matching /watch/ page URL.

    Results aren't relevance-ranked in the HTML, so pick the watch link whose
    slug is most similar to the requested title (a "Frieren" search otherwise
    returns a mini-anime spin-off before the main series).
    """
    html = _get_text(f"{ANIKOTO_BASE}/search?keyword={urllib.parse.quote(title)}")
    target = _norm(title)
    best_path, best_score = None, -1.0
    seen = set()
    # Watch links look like /watch/<title-slug>-<id>; the id is the last token.
    for path, slug in re.findall(r'(/watch/([a-z0-9-]+)-[a-z0-9]+)', html, re.IGNORECASE):
        if path in seen:
            continue
        seen.add(path)
        score = difflib.SequenceMatcher(None, target, _norm(slug)).ratio()
        if score > best_score:
            best_path, best_score = path, score
    return f"{ANIKOTO_BASE}{best_path}" if best_path else None


PROVIDERS = {
    "anikoto": anikoto,
}
PROVIDER_ORDER = [
    p.strip()
    for p in os.environ.get("OPENANIME_PROVIDERS", "anikoto").split(",")
    if p.strip() in PROVIDERS
]


# =====================================================================
# CLI — for the manual yt-dlp testing the roadmap calls for.
# =====================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve an anime episode to a stream URL")
    parser.add_argument("target", nargs="?", help="a watch-page URL, or a title")
    parser.add_argument("--title", help="anime title to search for")
    parser.add_argument("--episode", type=int, default=1, help="episode number (default 1)")
    args = parser.parse_args(argv)

    if not args.target and not args.title:
        parser.error("give a URL/title positionally or use --title")

    try:
        stream = get_stream_url(args.target, title=args.title, episode=args.episode)
    except ScrapeError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print(f"source:  {stream.page}", file=sys.stderr)
    print(f"headers: {json.dumps(stream.headers)}", file=sys.stderr)
    print(stream.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
