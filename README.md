# Video Scout Pipeline

Personal, small-scale pipeline that adapts publicly-shared Reddit stories into
narrated YouTube Shorts, with a manual review step before anything goes public.
Fully chainable end-to-end with `pipeline.py` so it can run unattended on a
schedule (cron, Termux, or GitHub Actions).

## How it works

1. **`trend_scout.py`** — read-only scan of a fixed list of subreddits, using
   reddit.com's public `top/.json` endpoints (no OAuth/API-key needed — see
   "Reddit access" below). Filters by score/comment thresholds and text
   length, and stores up to ~15-20 candidates/day in
   `pipeline_state/candidatos.json`. Already-seen post IDs are tracked
   locally so nothing is re-fetched. No write access to Reddit at any point
   (no posting, commenting, voting, or messaging).
2. **`script_writer.py`** — sends each candidate's text to Gemini (free tier,
   outside Reddit, no further Reddit access) to adapt it into a first-person
   narration script, preserving the core facts. Each rewritten story keeps a
   `# Fuente:` / `# Autor:` reference back to the original post.
3. **`generar_video_maestro.py`** — renders the narration (edge-tts), karaoke
   subtitles, and background video locally with ffmpeg into a finished video
   file, and writes `resultado_lote.json` describing what was produced.
4. **`publisher.py`** — runs a technical + content quality check (Gemini free
   tier), then uploads the video to YouTube as **private**, scheduled to go
   public only after a manual review window. Every published video's
   description credits the original subreddit and author, with a link back
   to the source Reddit post.
5. **`pipeline.py`** — orchestrates all four stages in one command, so the
   whole thing can be triggered by cron/CI without babysitting it.

## Reddit access

Reddit's official Data API (PRAW / OAuth "script" app) requires app approval
that isn't always granted for personal projects. `trend_scout.py` instead
reads reddit.com's public, unauthenticated `top/.json` endpoints — the same
data a logged-out browser sees. It's still 100% read-only (no posting,
voting, or messaging) and keeps a conservative delay between requests, but
it's not the "official" API path, so: keep run frequency low (once or twice
a day is plenty), and if you start seeing consistent 429/403s, space runs out
further. If you're later approved for the official API, swapping back to
PRAW in `trend_scout.py` is a small, isolated change.

## Publishing beyond YouTube (TikTok)

TikTok's Content Posting API has the same friction as Reddit's: unaudited
apps can only post to non-public accounts until TikTok reviews the app,
which (like Reddit) isn't guaranteed for a personal project. Given that,
this pipeline automates YouTube Shorts end-to-end and leaves TikTok as a
manual upload step for now — `publisher.py`'s generated title/description/
hashtags are reusable as-is when you post the finished file manually. If you
later get TikTok API access approved, a `publisher_tiktok.py` mirroring
`publisher.py`'s YouTube upload call is a small addition.

## Setup

```bash
pip install -r requirements.txt
```

Copy `config_trends.example.json` to `config_trends.json` if you want to
customize the subreddit list or thresholds (optional — sane defaults are
built in). Never commit `config_trends.json`, `config.json`,
`client_secret.json`, or `youtube_token.json` — see `.gitignore`.

Set `GEMINI_API_KEY` (free at https://aistudio.google.com/apikey) as an
environment variable — used by `script_writer.py` and `publisher.py`.

## Background music (`actualizar_musica.py`)

Background video clips (`fondo_vertical*.mp4` / `fondo_horizontal*.mp4`) are
provided by you. Music is fetched automatically from
[Jamendo](https://www.jamendo.com), a royalty-free catalog with a free API:

```bash
export JAMENDO_CLIENT_ID="tu_client_id"  # gratis en https://devportal.jamendo.com/
python actualizar_musica.py
```

This downloads a few tracks per emotion category (`drama`, `venganza`,
`suspenso`, `comedia`) as `musica_<emocion>_<artista>_<id>.mp3`, filtering
for licenses that allow commercial use and don't forbid derivatives (needed
since the track gets mixed with narration). `generar_video_maestro.py`
picks randomly among all tracks available for an emotion, so re-running
`actualizar_musica.py` occasionally (weekly/monthly is plenty — music
doesn't need to change per video) keeps adding variety instead of repeating
the same song. Attribution (artist, license, Jamendo page) is saved to
`pipeline_state/musica_atribucion.json` and automatically credited in the
YouTube description by `publisher.py` when a video uses one of these
tracks.

This isn't part of the daily `pipeline.py` run — run it manually, or set up
its own occasional cron/Action if you want it fully hands-off.

For YouTube uploads, download an OAuth "Desktop app" client from Google Cloud
Console as `client_secret.json`. The first run of `publisher.py` (or
`generar_youtube_token.py`, see below) opens a browser to authorize once;
after that, `youtube_token.json` is reused and refreshed automatically — no
further manual login needed locally.

## Running it

```bash
python pipeline.py                # runs all 4 stages
python pipeline.py --hasta guion  # only scout + script (stop before rendering)
python pipeline.py --desde video  # only render + publish (guion.txt must exist)
```

Every stage is incremental: candidates, scripts, and rendered videos already
on disk are skipped/reused, so re-running after a partial failure just picks
up where it left off.

## Automating it (no manual trigger)

**Option A — local cron / Termux (simplest, uses your own machine/phone):**
run `python pipeline.py` on a schedule with cron (Linux/macOS) or
`cronie` + `termux-services` (Android). Your background footage/music files
stay local, no upload needed.

**Recommended split — generate in batches, publish daily.** Uploading an
entire batch (10-20 videos) at once, a few times a week, floods anyone
following the channel and gives the algorithm a spiky, inconsistent signal.
Shorts channels grow better with **one video going public per day, at a
consistent time** — that's what `publisher.py`'s `max_subidas_por_corrida`
(default: 1) enforces: each run uploads just one video from the backlog and
leaves the rest queued in `resultado_lote.json` for the next run. So split
generation (heavier, less often) from publishing (light, daily):

```cron
# crontab -e
# Generate a backlog: scout + script + render, twice a week
0 6 * * 1,4 bash -lc 'source ~/.pipeline_secrets && cd /path/to/video-scout-pipeline && python pipeline.py --hasta video >> pipeline.log 2>&1'

# Publish one video/day from the backlog — buffer_horas_revision in
# publisher.py's config controls how many hours later it actually goes
# public (tune it so that lands near your audience's peak hours)
0 9 * * * bash -lc 'source ~/.pipeline_secrets && cd /path/to/video-scout-pipeline && python pipeline.py --desde publicar >> pipeline.log 2>&1'

# Refresh background music once a month (optional, doesn't need to be frequent)
0 8 1 * * bash -lc 'source ~/.pipeline_secrets && cd /path/to/video-scout-pipeline && python actualizar_musica.py >> musica.log 2>&1'
```

**Option B — GitHub Actions (cloud, no device needs to stay on):** see
`.github/workflows/pipeline.yml`, which runs the same `pipeline.py` on a
daily schedule using GitHub's free runner minutes. It needs three repo
secrets and your background video/music assets available to the runner —
either committed to the repo (simplest, if you have the rights to
redistribute them) or downloaded in the workflow's "Descargar assets" step
from wherever you host them.

### Generating the `YOUTUBE_TOKEN` secret

`YOUTUBE_TOKEN` is the contents of `youtube_token.json`, produced by a
one-time OAuth login **on your own device** (GitHub Actions has no browser
to do this itself). This works from a phone via Termux just as well as from
a PC — see the Termux note in step 3.

1. In [Google Cloud Console](https://console.cloud.google.com/), create (or
   reuse) a project, enable the **YouTube Data API v3**, then go to
   *APIs & Services → Credentials → Create Credentials → OAuth client ID*,
   type **Desktop app**. Download the JSON and save it as `client_secret.json`
   in the repo folder (it's gitignored — never commit it).
2. In *APIs & Services → OAuth consent screen*, add your own Google account
   as a **test user**, then set **Publishing status to "In production"**
   (you can do this without going through Google's verification review).
   This matters: apps left in "Testing" status get refresh tokens that
   **expire after 7 days**, which would silently break the scheduled
   workflow every week. "In production" (unverified) tokens don't expire on
   a timer — you'll just see a one-time "Google hasn't verified this app"
   warning during step 3, click *Advanced → Go to (app name)* to continue.
3. Run the one-time login:
   ```bash
   pip install -r requirements.txt
   python generar_youtube_token.py
   ```
   It prints an authorization link — open it (Chrome or any browser). On a
   PC it's on the same machine; **on a phone with Termux**, open the link in
   your phone's browser app (not inside Termux) — it still works because the
   callback server listens on `localhost`, which the browser reaches even
   though it's a different app, as long as it's the same device. Sign in,
   grant the YouTube upload permission, and switch back to Termux: it
   detects the callback and writes `youtube_token.json` next to the script.
4. In the GitHub repo, go to *Settings → Secrets and variables → Actions →
   New repository secret* and create:
   - `YOUTUBE_CLIENT_SECRET` — paste the full contents of `client_secret.json`
   - `YOUTUBE_TOKEN` — paste the full contents of `youtube_token.json`
   - `GEMINI_API_KEY` — your Gemini free-tier key

The workflow writes these back to `client_secret.json`/`youtube_token.json`
on the runner before each run. Because `youtube_token.json` contains a
refresh token, it keeps renewing itself automatically — you only repeat
steps 3-4 if you ever revoke access or the secret gets out of sync (e.g.
after running `publisher.py` locally, which rewrites the file — re-copy it
to the secret if you do).
