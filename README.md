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

For YouTube uploads, download an OAuth "Desktop app" client from Google Cloud
Console as `client_secret.json`. The first run of `publisher.py` opens a
browser to authorize once; after that, `youtube_token.json` is reused and
refreshed automatically — no further manual login needed.

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
Termux:Boot + `termux-job-scheduler` (Android), e.g. once daily. Your
background footage/music files stay local, no upload needed.

```cron
# crontab -e — once a day at 09:00
0 9 * * * cd /path/to/video-scout-pipeline && /usr/bin/python3 pipeline.py >> pipeline.log 2>&1
```

**Option B — GitHub Actions (cloud, no device needs to stay on):** see
`.github/workflows/pipeline.yml`, which runs the same `pipeline.py` on a
daily schedule using GitHub's free runner minutes. It needs three repo
secrets (`GEMINI_API_KEY`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_TOKEN` — the
contents of your local `client_secret.json`/`youtube_token.json` after the
one-time OAuth login) and your background video/music assets available to
the runner — either committed to the repo (simplest, if you have the rights
to redistribute them) or downloaded in the workflow's "Descargar assets"
step from wherever you host them.
