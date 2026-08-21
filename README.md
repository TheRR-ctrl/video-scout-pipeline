# Video Scout Pipeline

Personal, small-scale pipeline that adapts publicly-shared Reddit stories into
narrated YouTube Shorts, with a manual review step before anything goes public.

## How it works

1. **`trend_scout.py`** — read-only scan (PRAW / Reddit's official Data API,
   OAuth "script" app) of a fixed list of subreddits. Fetches the daily "top"
   posts, filters by score/comment thresholds and text length, and stores up
   to ~15-20 candidates/day in `pipeline_state/candidatos.json`. Already-seen
   post IDs are tracked locally so nothing is re-fetched. No write access to
   Reddit at any point (no posting, commenting, voting, or messaging).
2. **`script_writer.py`** — sends each candidate's text to a language model
   (outside Reddit, no further Reddit API calls) to adapt it into a
   first-person narration script, preserving the core facts. Each rewritten
   story keeps a `# Fuente:` / `# Autor:` reference back to the original post.
3. **`generar_video_maestro.py`** — renders the narration (TTS), subtitles,
   and background video locally with ffmpeg into a finished video file.
4. **`publisher.py`** — runs a technical + content quality check, then
   uploads the video to YouTube as **private**, scheduled to go public only
   after a manual review window. Every published video's description
   credits the original subreddit and author, with a link back to the
   source Reddit post.

## Why this needs to live outside Devvit

Devvit apps run inside Reddit's own hosted platform. This pipeline needs to
call local system tools (ffmpeg, a TTS engine, image generation) to produce
downloadable video files, and publish the result to a different platform
(YouTube) via YouTube's own API — none of which Devvit supports. The only
interaction with Reddit is the read-only fetch in `trend_scout.py`.

## Setup

```bash
pip install praw google-genai edge-tts pillow google-api-python-client google-auth-oauthlib
```

Copy `config_trends.example.json` to `config_trends.json` and fill in your
own Reddit API credentials (never commit this file — see `.gitignore`).
