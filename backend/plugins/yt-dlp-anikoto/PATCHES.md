# Vendored: yt-dlp-anikoto (patched)

Source: https://github.com/yt-dlp-plugins/yt-dlp-anikoto (archived 2026-06-04, unmaintained).

This is the upstream anikoto extractor with local fixes. anikoto.cz is reachable
headlessly (Cloudflare is in CDN mode, no JS challenge), unlike animepahe.pw and
AllAnime which now serve interactive Cloudflare challenges that no headless HTTP
client (yt-dlp included) can pass.

## Local patches to `yt_dlp_plugins/extractor/anikoto.py`

`_MegaplayIE` is the embed-host extractor. The player hosts rotated since the
plugin was archived, breaking it. Fixes:

1. **New host + path shape.** Added `vidtube.site` to `_VALID_URL` and made the
   server-prefix segment generic (`s-\d+` instead of the hardcoded `s-2`), since
   episodes now list mirrors like `megaplay.buzz/stream/s-5/...` and
   `vidtube.site/stream/<id>/sub` (no `s-N` at all).
2. **Robust base_url.** `base_url` is now derived from scheme+host via regex
   instead of `url.rsplit('/', 4)`, which broke on the shorter vidtube path.
3. **Skip dead mirrors.** Each episode lists ~8 mirror servers; any one can be
   down or change format. `_entries` now wraps each mirror extraction in
   try/except and skips failures instead of letting one bad mirror abort the
   whole episode.

## Re-verify after a site change
```bash
yt-dlp --plugin-dirs backend/plugins -f "best[height<=1080]/best" \
  --get-url --playlist-items 1 \
  "https://anikoto.cz/watch/<slug>-<id>"
```
The extracted `.m3u8` is referer-gated; playback needs the `Referer`/`User-Agent`
that yt-dlp reports in `http_headers` (scraper.py forwards them to mpv).

Requires yt-dlp ≥ 2025.12.08 and Python ≥ 3.10 (the plugin uses `X | Y` runtime
type hints).
