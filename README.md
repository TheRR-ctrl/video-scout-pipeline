# Video Scout Pipeline

Personal, small-scale pipeline that adapts publicly-shared Reddit stories into
narrated YouTube Shorts, with a manual review step before anything goes public.
Fully chainable end-to-end with `pipeline.py` so it can run unattended on a
schedule (cron, Termux, or GitHub Actions).

## How it works

1. **`trend_scout.py`** — read-only scan of a fixed list of subreddits via
   each subreddit's public RSS feed (no OAuth/API-key needed — see "Reddit
   access" below). Filters by feed rank and text length, and appends
   candidates to a queue in `pipeline_state/candidatos.json`. No write access
   to Reddit at any point (no posting, commenting, voting, or messaging).
   `--diagnostico` explains a scan that came back empty; `--estado` shows the
   queue. The default list is 36 subreddits across drama, difficult family,
   revenge, customer-facing work, unexplained encounters and comedy; a
   subreddit that is gone or misspelled doesn't break the run — see below.

   Subreddits are read in small groups, not one at a time: Reddit's public
   syntax for a combined feed (`r/sub1+sub2+.../top/.rss`) puts several in a
   single request, and each `<entry>` still carries its real subreddit in
   `<category>`, so per-post attribution doesn't get lost. Reddit's rate
   limit is per IP, not per subreddit, so this is what actually avoids 429s
   at 36 subreddits — verified live: 36/36 read with 0 failures in ~2
   minutes, versus ~7 minutes with occasional 429s one-request-per-subreddit.
   Group size is `subreddits_por_tanda` (4 by default); a request's failure
   is charged to every subreddit in its group, since none of them could be
   checked that run. Larger groups mean fewer requests but a worse floor per
   subreddit (Reddit returns the group's best posts overall, not N per
   subreddit — bundling all 36 into one request left 15 of them with zero
   results), so 4 is a deliberate balance, documented with the numbers in
   `docs/repos_revisados.md`.
2. **`youtube_scout.py`** — second source feeding the *same* queue. With a
   free `YOUTUBE_API_KEY` it searches by view count, so a three-year-old video
   with two million views is found (age is never a filter — only views are);
   without a key it falls back to channel RSS feeds, which only expose the
   ~15 newest uploads. Public captions supply the text; audio and video are
   never downloaded or reused — see "YouTube sources" below.
3. **`script_writer.py`** — sends each candidate's text to Gemini (free tier)
   to adapt it into a first-person narration script, preserving the core
   facts. A YouTube episode is first split into the separate anecdotes it
   contains, each becoming its own story. Every rewritten story keeps a
   `# Fuente:` / `# Autor:` reference back to the original. A candidate is
   only marked as consumed once its script is on disk, so a failed run never
   burns the story.

   This is a separate step on purpose: searching leaves candidates in
   `candidatos.json`, and nothing reaches the render queue until Gemini has
   written them. The panel says how many are waiting in the **Cola** tab, with
   the button that writes them — searching without this step looks like
   searching found nothing.

   The queue is bounded, because the scouts add on every run and this stage
   only drains what the daily free Gemini quota allows — left alone it grows
   faster than it empties. `cola.TOPE_CANDIDATOS` (60) caps it and
   `cola.FRESCURA_MAXIMA_DIAS` (14) expires the rest; what is dropped is
   marked as seen so the next scan doesn't bring it straight back — and its
   text is kept in `pipeline_state/candidatos_archivados.json` (last 300),
   since the scouts don't store the posts they read anywhere else. Candidates
   are written newest first, and each run writes at most
   `MAX_POR_CORRIDA` (12, `--max N`, `--max 0` for no cap) — the rest of the
   day's quota is for `calidad_ia` and the publisher's metadata, which call
   Gemini later in the same pipeline.

   Most Reddit posts are one dry paragraph, so the prompt asks Gemini to
   *develop* them — scene, inner monologue, dialogue spelled out from what
   the post summarizes, tension held before the turn — aiming past ~200
   words. That adds telling, not events: inventing facts is off limits,
   since the video credits the post and its author by name, and the `tema`
   label that gates publishing has to stay honest. When the original stops
   without a resolution, the ending comes from the narrator's present
   ("I still don't know what became of her"), never from a made-up twist.
   Rendering never removes anything from `guion.txt`, so the **Cola** tab
   lists only the stories that don't have a video yet (matched the same way
   `limpiar_cola.py` matches them); the rest are one click away behind
   "verlas". `limpiar_cola.py` is what takes them out for good, and it now
   appends them to `guion_historial.txt` first — one file with every script
   ever written, instead of having to dig through dated backups.

   Gemini reports back in `se_sostiene` whether the story it just wrote
   holds up; the ones that don't are dropped before rendering, along with
   anything under `PALABRAS_MINIMAS_CUERPO` words as a backstop.
4. **`generar_video_maestro.py`** — renders the narration (edge-tts), karaoke
   subtitles, and background video locally with ffmpeg into a finished video
   file, and writes `resultado_lote.json` describing what was produced. Shorts
   vs. long-form isn't decided up front: it falls out of the finished
   narration's real duration (`duracion_max_short_sec`, default 180 s), so the
   script is never padded or trimmed to hit a format.

   Long-form is gated, though (`formato.py`: until the channel reaches 500
   subscribers), and a story that doesn't fit in a Short just waits in
   `guion.txt`. **`partir_historias.py`** can split it into 2–4 consecutive
   Shorts titled "Parte N de M — …", each ending on "Sigue en la parte N+1" —
   but only when you decide: each long story in the panel's **Cola** tab gets
   a «✂ Partir en N» button. Turning on *Partir solas las historias largas*
   (Ajustes → Subida, `"partir_automatico": true`) makes `script_writer.py`
   split new stories as it writes them and adds the step to "Todo de una
   pasada"; it's off by default. The text is never rewritten: Gemini
   only picks *between which sentences* to cut, aiming for a cliffhanger, and
   without it the cut falls at equal lengths. The part marker goes at the
   *front* of the title, because the video's filename is the title cut at 120
   characters and `limpiar_cola.py` matches by filename — at the end, the cut
   would eat it and the three parts would look like one. For the same reason
   `publisher.py` forces "(Parte N/M)" onto the YouTube title instead of
   leaving it to Gemini: it skips any upload whose title is already on the
   channel. It also puts each series back in order before uploading, since
   the queue number a part was rendered under shifts with every cleanup, and
   `relanzar.py` never re-queues a single part on its own.
5. **`publisher.py`** — runs a technical + content quality check (Gemini free
   tier, with an automatic fallback description/hashtags if that check
   fails), then uploads the video to YouTube as **private**, scheduled to go
   public only after a manual review window. Hashtags are placed at the
   start of the description so YouTube renders them as clickable chips.
   Every published video's description credits the original subreddit and
   author, with a link back to the source Reddit post. Uploads only run
   while connected to WiFi, and local video files are kept for 7 days after
   upload (so you can still cross-post them to TikTok manually) before
   being deleted automatically.
6. **`pipeline.py`** — orchestrates every stage in one command, so the
   whole thing can be triggered by cron/CI without babysitting it.

## Reddit access

Reddit's official Data API (PRAW / OAuth "script" app) requires app approval
that isn't always granted for personal projects, and the unauthenticated
`top/.json` endpoints are blocked by Reddit's anti-bot filter. `trend_scout.py`
instead reads each subreddit's public RSS feed — the same read-only access any
news aggregator uses. It's still 100% read-only (no posting,
voting, or messaging) and keeps a conservative delay between requests, but
it's not the "official" API path, so: keep run frequency low (once or twice
a day is plenty), and if you start seeing consistent 429/403s, space runs out
further. If you're later approved for the official API, swapping back to
PRAW in `trend_scout.py` is a small, isolated change.

## YouTube sources

`youtube_scout.py` treats other people's videos more carefully than Reddit
posts, not less: a Reddit post is text its own author published, while a
podcast episode is a creator's edited recording.

- Only public captions are read. The audio and video are never downloaded,
  clipped, or reused in any form.
- Captions are raw material, never output: `script_writer.py` retells the
  anecdote from scratch in its own words rather than polishing the
  transcript, and the prompt says so explicitly.
- Channel name and video URL travel with the candidate into `# Fuente:` /
  `# Autor:`, and `publisher.py` credits the channel with a link in the
  published description.
- Defaults point at channels built on audience-submitted anecdotes, which
  have the clearest provenance. Keep that criterion when adding channels.

### Finding the viral ones

Virality lives in a channel's back catalogue, not its latest upload, and the
two discovery paths differ sharply on that:

- **With `YOUTUBE_API_KEY`** (free, from Google Cloud Console — enable
  "YouTube Data API v3"): `search.list` with `order=viewCount` returns the
  most-viewed videos ever, plus keyword searches across all of YouTube via
  `youtube_busquedas`, so the channel list stops being a bottleneck.
- **Without a key**: only each channel's RSS feed, which carries the ~15 most
  recent uploads. Those rarely have views yet, so a whole scan getting
  discarded as "pocas vistas" is the expected outcome, not a misconfiguration.
  `--diagnostico` says so explicitly rather than leaving you guessing.

Configure channels, searches and thresholds in `config_trends.json` (see
`config_trends.ejemplo.json`), or set `"youtube_activo": false` to turn the
source off. Verify any `@handle` you add by opening it in a browser first — a
handle that 404s silently wastes a run.

Channels and subreddits can also be added or removed from the panel, in
**Ajustes → Más opciones → Fuentes** — the panel checks a new channel against YouTube before
saving it (same 404-protection as above), and a new subreddit against Reddit
the same way (one request per add; a 429 is let through unverified rather
than blocking the add, since Reddit's RSS rate limit is stricter than a
single request warrants blocking on). Removing an entry doesn't need either
check. With `YOUTUBE_API_KEY` set, the same section also has a channel
search box (`search().list(type="channel")`, same API `youtube_busquedas`
already uses) so you don't have to copy the exact `@handle` from a browser —
without a key that box doesn't render, same "pegar el @handle a mano"
fallback as always. Adding or removing anything from that section, in either source,
writes the *full* current list to `config_trends.json` (not just the one
change), which means: the first time you touch either list from the panel,
that list stops tracking future changes to the built-in defaults in
`trend_scout.py`/`youtube_scout.py` — from then on it's entirely what's in
your `config_trends.json`, same as if you'd edited the file by hand.

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

On a phone (Termux), `instalar.sh` does the whole thing in one command —
system packages, Python deps, storage permission, the panel, and a report of
which credentials and media are still missing. See **[INSTALAR.md](INSTALAR.md)**
(Spanish, since that is where it runs).

On a PC:

```bash
pip install -r requirements.txt
```

Copy `config_trends.ejemplo.json` to `config_trends.json` if you want to
customize the subreddit list or thresholds (optional — sane defaults are
built in). Never commit `config_trends.json`, `config.json`,
`client_secret.json`, or `youtube_token.json` — see `.gitignore`.

Set `GEMINI_API_KEY` (free at https://aistudio.google.com/apikey) as an
environment variable — used by `script_writer.py` and `publisher.py`.

The easier way, on the phone: **Ajustes → Conectar servicios** in the panel.
Each service (Gemini, YouTube search, Pexels, Pixabay, Jamendo) has a card
saying what it's for, a link to the page where you get the key and the
steps there; back in the panel, **📋 Pegar** reads the clipboard and the key
is tested against the service *before* it's saved (`conectar.py`), so a
wrong key says so right away — and says whether it's the key or an API
that isn't enabled — instead of failing on the next render. If the service
can't be reached it's saved anyway, with a warning. Keys go to
`secretos.env` (mode 600), never to the command line. Upload permissions
(`youtube_token.json`) are OAuth grants that can't be given from the panel;
the card shows the one Termux command to run.

## The panel as an app

The review panel (`servidor.py`, served at `http://127.0.0.1:8770`) can live
on the home screen in two ways. They stack — the second one includes the
first.

**Installed from Chrome (nothing to build).** The panel is a real PWA: a
manifest plus a service worker with a `fetch` handler, which is what Chrome
requires before it offers to install a page rather than just bookmark it.
`127.0.0.1` counts as a secure context, so no HTTPS is needed. Open the panel
and use **⋮ → Install app**. It gets its own icon, no browser chrome, and —
because the service worker caches a small shell — a proper "the panel isn't
running" screen instead of Chrome's dinosaur when `servidor.py` is off. That
screen polls and walks you straight in the moment the server answers.

The service worker deliberately caches almost nothing: only that offline
screen and the icons. `index.html` is 150 KB and changes with every `git
pull`, and `/api/`, `/video/`, `/audio/` and `/miniatura/` are live data —
a cached panel talking to a newer API is worse than no app at all. See
`web/sw.js`.

**The APK (`android/`).** Same panel, one thing more, and it's the thing no
web page can ever do: it starts the server for you. A page can't launch
Python on your phone — no browser allows it, which is exactly what stops any
website from running things on your device. An installed app can ask Termux
to do it, through the `RUN_COMMAND` intent. So tapping the icon is the whole
procedure, instead of "open Termux, type `panel`, switch apps".

It's an `Activity` and a `WebView` with no dependencies — 19 KB. If the port
already answers it just goes in (no matter who started the server); otherwise
it asks Termux and waits; and if that fails it says which of the three things
failed — Termux missing, permission missing, Termux refused — rather than
showing a blank screen.

Building it needs the Android SDK, which doesn't exist for Termux, so the
**Compilar la app de Android** workflow builds it on the runner and leaves
the `.apk` as an artifact (or as a release, which is far easier to grab from
a phone). It's debug-signed: fine for an app you sideload onto your own
phone, not something for a store.

On the phone it needs one command, once: `bash instalar_panel.sh --app`. That
creates the `panel-servidor` entry point the app calls and sets
`allow-external-apps=true` in Termux. That line applies to *any* app holding
the `RUN_COMMAND` permission, not just this one, which is why the installer
won't set it without being asked. Details in
**[android/README.md](android/README.md)**.

## Background video (`motor_fondo` in `config.json`)

- `"cortes"` (default) — the original behaviour: random 6-12 s cuts from your
  own `fondo_vertical*.mp4` / `fondo_horizontal*.mp4` files, concatenated to
  cover the narration. You supply the source footage.
- `"hyperframes"` — `hyperframes_broll.py` asks Gemini for an **HTML
  composition** (HTML + CSS + GSAP) and renders it to MP4 with the
  [HyperFrames](https://hyperframes.heygen.com) CLI (Apache-2.0, by HeyGen).
  **No source footage needed at all** — the pipeline runs from an empty
  folder. Free and local: rendering doesn't use HeyGen credits or need an
  account.

  HyperFrames doesn't *play* the page, it asks headless Chrome for one frame
  at a time (`seek(0)`, `seek(1/30)`, …) in deterministic mode and stitches
  the frames with ffmpeg, so the same HTML always yields the same MP4.
  Compositions are cached in `pipeline_state/hyperframes_cache/`.

  Rendering runs at roughly 3x real time, so a full 3-minute story would be
  slow. Instead a composition of `duracion_composicion_seg` (default 20 s) is
  generated and looped to cover the story — the visual profile asks for a
  cyclic animation whose last frame matches its first, so the loop seam
  doesn't show. Because that length is the same for every story, and the
  composition is asked for by tone and aspect ratio only (never by story
  title — the background is forbidden from illustrating the story anyway),
  the cache is a closed set of a few clips that get reused instead of one
  fresh multi-minute render per video. The cache is capped at
  `CACHE_MAX_MB` (600 MB); past that the least recently used clips are
  dropped.

  **PC only.** Rendering drives headless Chrome, which ships linked against
  glibc and will not start on Android's bionic — and would want Node ≥ 22
  plus sustained CPU besides. On Termux the engine declines up front and the
  run falls back to `"cortes"` with a warning; it never aborts the batch. Set
  `HYPERFRAMES_FORZAR=1` to try anyway (e.g. under a glibc proot). To use
  generated backgrounds from the phone, run the *Fabricar fondos con IA*
  workflow on a GitHub runner and download the result as ordinary footage.

  Requires **Node.js ≥ 22** on the PATH (the CLI downloads itself via `npx` on
  first use), `GEMINI_API_KEY`, and outbound internet during the render
  (the composition loads GSAP from jsDelivr).

If the generated background fails for any reason, the pipeline falls back to
`"cortes"` and then to a single background file, so switching engines can't
break a run.

The engine is split in two. `hyperframes_nucleo.py` is shared **verbatim**
with the sibling repo
[`video_generation`](https://github.com/TheRR-ctrl/Video_Generation) — edit it
in one, copy it to the other. It holds everything that does not depend on
*what* is being drawn: the PC-only platform gate, the CLI invocation, the
linter, and the on-disk cache (atomic writes, LRU pruning).
`hyperframes_broll.py` sits on top and is deliberately different in each repo:
this one composes **one background per story** and loops it, the sibling
composes **per scene, in batches**. What varies between uses here is the
`PerfilVisual` — what is being illustrated, what gets overlaid on top, and
which parts of the frame must stay clear. See `.claude/skills/hyperframes-broll/SKILL.md` for the
composition contract and how to debug a failed render.

The CLI version is pinned in `hyperframes_nucleo.py` (`VERSION_CLI`), so an
unattended run never silently changes render engine. To bump it, use
`subir_version_hyperframes.py` rather than editing by hand — it takes both
repos at once, keeping that file byte-identical, and rewrites the comment above
the constant so the stated reason never contradicts the pinned number:

```bash
python3 subir_version_hyperframes.py 0.8.29 \
  --nota "why this version, ideally with the run that proved it" \
  ~/video-scout-pipeline ~/video_generation
md5sum ~/video-scout-pipeline/hyperframes_nucleo.py ~/video_generation/hyperframes_nucleo.py
```

Test a new version on a branch with the *Fabricar fondos con IA* workflow
before it reaches `main`: this engine is PC-only, so it cannot be verified from
the phone. And note the clip cache key does **not** include the CLI version —
if you are bumping to fix a bad render, clear
`pipeline_state/hyperframes_cache/` too, or the old clips keep being served.

## Rendering: encoder and resolution

Two independent knobs live under `"video"` in `config.json` (see
`config.example.json`), both **off/at-default unless the material actually
supports them** — flipping neither changes today's output at all.

- **`usar_chip_android`** (default `false`) — on Android, try the phone's
  hardware video encoder (`h264_mediacodec`, exposed because Termux's ffmpeg
  is built with `--enable-mediacodec`) before falling back to software
  `libx264`. Faster and easier on the battery when it works. Toggle it from
  the panel (Ajustes → Más opciones → Render, only shown when the server detects Android)
  rather than editing the file by hand — that's the fix if a phone's chip
  renders badly. The automatic CPU fallback only catches a hard failure
  (ffmpeg exits non-zero, or the file is empty); a chip that finishes fine
  but produces wrong colors or broken frames won't be caught by the
  pipeline — watch the first render with the switch on and flip it back off
  if it looks wrong. `bitrate_chip_android` (default `4M`, with matching
  `-maxrate`/`-bufsize`) scales with whatever resolution ends up being used
  (see below), to keep quality consistent without a fixed bitrate silently
  turning too low or too high.

- **Adaptive resolution** — the render used to be hardcoded at
  1080x1920/1920x1080. Now `elegir_resolucion_render()` probes the actual
  background file(s) a given video will use with `ffprobe` and renders at
  that native resolution instead, capped at "2K" (2560 px on the long edge).
  It never upscales: when a video's background pool mixes a high-res clip
  with a 1080p one, it targets the weaker of the two, since
  `crear_fondo_multi_corte` may cut from either for variety. In practice
  this is a no-op today — the project's own background sources (HyperFrames'
  fixed canvas, and `descargar_fondos.py`, which explicitly asks Pexels for
  the file closest to 1080x1920 **without exceeding it**) all top out at
  1080p on purpose. It only engages once a genuinely higher-resolution
  background file is added to the folder. There's no panel switch for this
  one — it's a pure function of what's on disk, so there's nothing to
  toggle.

Both changes touch shared render code (background scaling, the intro card
canvas, ASS subtitle sizing, the encoder flags) that can't be verified
without an actual phone — the same reasoning `CLAUDE.md` applies to
HyperFrames' `VERSION_CLI`. See `CLAUDE.md` for the failure modes worth
remembering before touching this again.

## Subtitle style and fonts

Subtitles are configured under `subtitulos` in `config.json` — nothing else
needs touching to change how they look:

```json
{
  "subtitulos": {
    "estilo": "frase_activa",
    "fuente": "Anton",
    "color_activo": "#3BF07A",
    "tamano_short": 84,
    "escala_activa": 112
  }
}
```

Keys you omit keep their defaults, so the snippet above is a complete,
valid config. Three styles are available:

| `estilo` | What it looks like |
|---|---|
| `frase_activa` (default) | The whole phrase stays readable in white and only the word being spoken turns green and grows — the current TikTok/CapCut look. |
| `relleno` | Classic karaoke fill: words already spoken keep the accent color, upcoming ones stay white. |
| `pop` | One word at a time, entering with a scale bounce. |

### Named styles, including your own

A style can be saved under a name and selected by it, from `config.json`
(`"subtitulos": {"preset": "sobrio"}`), per render (`--estilo sobrio`), or
from the panel's Estilo tab. The repo ships seven; your own go under
`presets_propios` and count exactly the same:

```json
{
  "subtitulos": {"preset": "mi_estilo"},
  "presets_propios": {
    "mi_estilo": {"estilo": "pop", "fuente": "Bebas Neue", "tamano_short": 130}
  }
}
```

Resolution is defaults → preset → whatever you put loose in `subtitulos`, so
a named style only lists what it changes. A name that collides with one of the
repo's resolves to the repo's, and the panel refuses to save it.

The panel writes that block for you: **Estilo → Crear estilo** copies the
active one into a form (type, font, colors, palette, sizes, toggles) and saves
it with a name. `previsualizar_estilos.py` renders every style — yours
included — onto the same frame, and the resulting PNGs now show up in the
Estilo tab itself rather than only as files in the video folder.

Fonts ship in `fuentes/` because libass silently substitutes a missing family;
the four bundled ones are listed in `FUENTES_INCLUIDAS`, and the panel also
accepts a family name typed by hand if you have it installed.

### Word timing, and the fallback

Word-level karaoke timing comes from the TTS itself: `edge-tts` is driven with
`boundary="WordBoundary"`, so each word's highlight starts when that word is
actually spoken. Nothing estimates anything on the normal path.

If that capture fails — or the audio comes back suspiciously short — the
renderer falls back to an SRT split by sentence, and there the per-word timing
has to be guessed. `reparto_respaldo` picks how:

| value | how the sentence's time is split |
|---|---|
| `"igual"` (default) | Every word gets the same slice. `a` and `extraordinariamente` last exactly as long. |
| `"proporcional"` | Each word gets a slice weighted by its letter count. In a 6-second sentence that is 0.07 s vs 1.37 s for those two words. |

The proportional split is borrowed from the sibling repo
[`video_generation`](https://github.com/TheRR-ctrl/video_generation), where
Gemini TTS returns no word timings at all and estimating is the only option.
It only ever applies to this fallback: replacing real `WordBoundary` timings
with an estimate would be a downgrade, so the normal path never uses it.

The panel shows which one is active under **Estilo → Qué usa el activo**.

**Fonts ship in `fuentes/`** (Anton, Montserrat Black, Archivo Black, Bebas
Neue — all SIL OFL, redistributable and fine for monetized video) and ffmpeg
is pointed at that directory with `fontsdir`. This matters: libass silently
*substitutes* a font that isn't installed rather than failing, so a style
asking for `Montserrat Black` on a phone that doesn't have it was rendering
as DejaVu Sans at regular weight — a font nobody chose. Bundling the files
makes the output identical on the phone and on a PC. To add another font,
drop its `.ttf` into `fuentes/` and set `fuente` to the font's internal
family name (`fc-query -f '%{family}\n' file.ttf` prints it).

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
picks randomly among all tracks available for an emotion.

Note that the plain run only *tops up*: once an emotion has its three
tracks it downloads nothing, however often you run it. To actually rotate
the library, use:

```bash
python actualizar_musica.py --rotar
```

That looks up which tracks already play in a rendered video (each render
records its track in `resultado_lote.json`), downloads that many new ones,
and only then moves the spent ones to `musica_usadas/` — one out per one
in, so a Jamendo outage leaves the library as it was and never empty. The
files are moved, not deleted: dropping one back into the repo root puts it
back in rotation.

This rotation runs by itself after every batch of renders — from
`pipeline.py` (a stage attached to the video stage, like the quality
checks) and from the panel (queued right behind the render job). It skips
itself without WiFi, without `JAMENDO_CLIENT_ID`, or with
`"musica_rotacion_automatica": false` in `config.json` (there's a switch
for it in the panel, under Ajustes → Subida). The monthly cron entry still
does the plain top-up.

Attribution (artist, license, Jamendo page) is saved to
`pipeline_state/musica_atribucion.json` and automatically credited in the
YouTube description by `publisher.py` when a video uses one of these
tracks.

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

**Recommended split — generate in batches, publish daily.** So split
generation (heavier, less often) from publishing (light, daily). Each
publish run drains as much of the backlog as YouTube's real daily upload
quota allows that day (`max_subidas_por_corrida` is `None` by default, so
it only stops on an actual `uploadLimitExceeded` from YouTube, counting
whatever was already uploaded earlier that same day) — before uploading, it
also checks the channel for a video with the same title already there, so a
connection drop mid-run never causes a duplicate or a lost upload; whatever
doesn't fit in a day's quota just stays queued in `resultado_lote.json` for
the next run:

```cron
# crontab -e
# Scout daily, at a different time each day. Cron has no notion of a random
# schedule and a long `sleep` gets killed on Android, so this line fires every
# half hour and buscar_diario.py checks whether today's drawn slot has arrived
# (it usually exits immediately). The slot is drawn once a day inside a window
# (09:00-22:30 by default, `busqueda_diaria_desde`/`_hasta`), and the plan
# lives in pipeline_state/busqueda_diaria.json, so a phone that was off at
# that hour still catches the day's scout on its first run after booting.
# By default it only runs on WiFi (`busqueda_diaria_solo_wifi`): the full scan
# is dozens of requests and mobile data costs money. Detecting WiFi on Android
# is the hard part: Termux:API does not exist in the Google Play build of
# Termux, and Android 11 closed both `ip route` (netlink) and /sys/class/net
# to ordinary apps. So it opens a UDP socket — which sends nothing — and reads
# the local IP the system picked: 192.168.x / 172.16-31.x is a home router,
# 100.64-127.x is a carrier CGNAT, and 10.x is used by both, so it does not
# guess there. Mark those once with `--soy-wifi` / `--soy-datos`, which store
# just the first three octets in pipeline_state/redes_conocidas.json.
# `--ver` shows today's slot and what it concluded, `--ahora` scouts now.
#
# What the random time buys you: no fixed daily fingerprint, and the load is
# spread out. What it does not buy you: it is not a way around rate limits.
# The scout still reads public RSS with the same pause between requests, and
# a 429 from reddit.com is a 429 at any hour — the fix is asking for less.
*/30 * * * * bash -lc 'cd /path/to/video-scout-pipeline && python buscar_diario.py >> buscar.log 2>&1'

# Generate a backlog from whatever the daily scout queued: script + render,
# twice a week (--desde guion, since scouting now happens on its own)
0 6 * * 1,4 bash -lc 'source ~/.pipeline_secrets && cd /path/to/video-scout-pipeline && python pipeline.py --desde guion --hasta video >> pipeline.log 2>&1'

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
