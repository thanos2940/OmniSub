# MorpiceHub × OmbiSub: requesting subtitles

This is the integration contract between **MorpiceHub** (the Plex front-end) and **OmbiSub** (the subtitle
translator). It is written for whoever adds the feature to MorpiceHub: everything needed is here, and every
response shape below was produced by OmbiSub's own code.

**What the feature does.** A MorpiceHub user looks at a movie or an episode in Plex and sees whether Greek
subtitles exist. If they don't, the user asks for them, MorpiceHub passes the request to OmbiSub, and shows
its progress until OmbiSub has written the subtitle file next to the video and asked Plex to rescan. The user
then picks the new subtitle in Plex like any other.

OmbiSub's side is built and tested (`backend/routers/on_demand.py`, `backend/services/on_demand.py`,
`backend/tests/test_on_demand.py`). MorpiceHub only needs an HTTP client, a small table of requests, and UI.

---

## 1. How a request flows

```
MorpiceHub (server side)                         OmbiSub                                   Sonarr / Radarr / Plex
────────────────────────                         ───────                                   ──────────────────────
GET  /api/on-demand/status          ───────────▶ capabilities, defaults, is it running?
POST /api/on-demand/subtitles/lookup ──────────▶ finds the title in Sonarr/Radarr ───────▶ series/episodes/files
                                                 inspects each video: Greek file there?
                                                 English sidecar? text track inside?
                                     ◀────────── per episode: available | requestable | ...
POST /api/on-demand/subtitles        ──────────▶ imports only the asked episodes
                                                 routes each one:
                                                   Greek already there      → "available"
                                                   English sidecar          → translation queue
                                                   text track in the video  → extraction → translation
                                                   nothing to translate     → "unavailable" (+ reason)
                                     ◀────────── request id + per-episode state
GET  /api/on-demand/subtitles/{id}   ──────────▶ live state, derived from the queue each time
   (poll until a final status)                   agy translates (episode by episode)
                                                 writes <video>.el.srt next to the video ──▶ Plex folder rescan
                                     ◀────────── state "completed", detail = file name
```

Key properties:

- **Nothing to download.** OmbiSub writes the subtitle into the video's own folder (`Show.S01E02.el.srt`,
  or `.el.ass` plus an `.el.srt` companion for styled subtitles) and asks Plex for a rescan of that folder.
  Plex shows it as an external Greek subtitle stream.
- **Progress is computed live.** OmbiSub stores only who asked for what; every `GET` derives the state from
  its translation queue and episode files, so it never drifts from what really happened.
- **Requests are cheap to repeat.** Work is keyed by (title, episode, language) inside OmbiSub. Asking again
  for an episode that is already waiting or translating does not start a second translation.

---

## 2. What has to be set up in OmbiSub

| Setting (OmbiSub UI) | Why it matters |
|---|---|
| **Settings › Connections › Sonarr / Radarr** connected (Test passes) | OmbiSub finds titles, episodes and video paths through them. Without Sonarr, series requests answer `503 sonarr_not_configured`; same for Radarr and movies. |
| **Settings › Connections › path mappings** | When Sonarr/Radarr report paths OmbiSub sees differently (another machine, a share), the mapping lets OmbiSub reach the files. Otherwise episodes come back `unavailable` / `media_unreachable`. |
| **Settings › Connections › Plex** (optional, recommended) | OmbiSub asks Plex to rescan the folder after writing a subtitle. Without it Plex finds the file at its next scheduled scan. |
| **Settings › Subtitles › embedded extraction** on, and ffmpeg found | Needed for videos whose English subtitle is only inside the container. With extraction off, the translation step extracts inline (slower, but still works). `status.ffmpeg` / `status.extraction_enabled` report this. |
| **Settings › Security** login set | Turns on the API key check. The **API key** shown there is what MorpiceHub sends (section 3). |
| **Settings › General › Most episodes in one request** (default 30) | Cap per request; larger requests are refused with `400 too_many_episodes`. Read it from `status.max_episodes`. |
| **Settings › General › Translate into** (default Greek) | The language used when a request does not name one. |

The translation itself is done by OmbiSub's agy runner (Antigravity CLI), or by the Gemini worker when
OmbiSub's engine is set to the Gemini API. MorpiceHub does not need to know which; `status` reports it.

---

## 3. Connecting

### Base URL

- Same network: `http://<ombisub-host>:2945` (2945 is the port OmbiSub was installed with on the server).
- Behind Caddy: `https://<domain>` or `https://<domain>/<url_base>` when OmbiSub is served under a path
  (Settings › General › URL base, e.g. `/ombisub`). OmbiSub accepts requests with or without that prefix.

All endpoints below are relative to that base, e.g. `GET {BASE}/api/on-demand/status`.

### Authentication

```
X-Api-Key: <the API key from OmbiSub › Settings › Security>
```

- When OmbiSub has a login set (it should), every `/api/...` call without the right key answers
  `401 {"detail": "Unauthorized"}`. Without a login, calls work without a key; send it anyway so nothing
  breaks the day a login is set.
- **Call OmbiSub from MorpiceHub's server, never from the browser.** The key grants full control of OmbiSub.
  Keep it in MorpiceHub's server configuration (environment variable or secrets store), not in client code.
  This also avoids CORS entirely.
- If the key is regenerated in OmbiSub (Settings › Security › "Generate a new API key"), MorpiceHub must be
  given the new one; until then every call is a 401.

### Timeouts

| Call | Typical time | Suggested client timeout |
|---|---|---|
| `GET /api/on-demand/status` | < 1 s | 10 s |
| `GET /api/on-demand/subtitles/{id}`, list, cancel | < 1 s | 15 s |
| `POST /api/on-demand/subtitles/lookup` | 1–30 s (asks Sonarr/Radarr, reads each video's header, 4 at a time, up to 30 files) | 60 s |
| `POST /api/on-demand/subtitles` | 2–60 s (imports the asked episodes, inspects each video) | 120 s |

A timed-out `POST /subtitles` may still have been carried out. Retrying it is safe (section 7.4).

### Content type and errors

Requests and responses are JSON (`Content-Type: application/json`). Errors are:

```json
{ "detail": "Human-readable sentence.", "code": "stable_machine_code", "...": "extra fields for some codes" }
```

Branch on `code`, show your own wording to users (section 6). A few errors come from the web framework and
carry only `detail` (401, 422 validation, 404 unknown request id).

---

## 4. Telling OmbiSub which title you mean

Every lookup and request body identifies one title:

| Field | Type | Use |
|---|---|---|
| `media_type` | `"movie"` \| `"series"` | Required. `"show"`, `"tv"` and `"episode"` are accepted as `"series"`. |
| `tmdb_id` | integer | **Movies: preferred.** Radarr is searched by TMDb id. Also matched for series (Sonarr's `tmdbId`). |
| `tvdb_id` | integer | **Series: preferred.** Sonarr is searched by TVDB id. |
| `imdb_id` | string (`"tt0816692"`) | Fallback for both, matched against the Radarr/Sonarr library. |
| `radarr_id` / `sonarr_id` | integer | If MorpiceHub knows the *arr's own id. |
| `title` (+ `year`) | string (+ integer) | Last resort. Matches the library title or an alternate title, accents and punctuation ignored. Two titles with the same name and no `year` give `409 ambiguous` with `candidates`. |

At least one of `tmdb_id`, `tvdb_id`, `imdb_id`, `radarr_id`, `sonarr_id`, `title` is required (else `422`).

### Getting the ids from Plex

MorpiceHub reads Plex metadata; ask for the external ids with `includeGuids=1`:

```
GET {PLEX}/library/metadata/{ratingKey}?includeGuids=1     (X-Plex-Token header)
```

The item carries `Guid: [{"id": "imdb://tt11280740"}, {"id": "tmdb://95396"}, {"id": "tvdb://371980"}]`.

- **Movie:** send `tmdb_id` (and `imdb_id` as well if you have it).
- **Episode:** use the episode's `grandparentRatingKey` to read the **show's** guids and send the show's
  `tvdb_id`; the episode is `season` = the episode's `parentIndex`, `episodes` = `[index]`.
- **Season:** the season item's `index` is the season number; the show's guids as above.

OmbiSub uses Sonarr's season/episode numbering. Plex and Sonarr agree for almost every show; for shows where
the Plex library uses a different episode order (absolute anime numbering, a TMDB order), map the episode by
its file instead, or ask the user to confirm.

### What to translate

| Field | Type | Meaning |
|---|---|---|
| `season` | integer | Series only. Omit for the whole show. |
| `episodes` | integer[] | Series only, needs `season`. Omit for the whole season. |
| `language` | string | ISO 639-1 code (`"el"`) or name (`"Greek"`). Omit for OmbiSub's default (`status.default_language`). |

| Body | Covers |
|---|---|
| `{media_type: "movie", tmdb_id}` | the movie |
| `{media_type: "series", tvdb_id, season: 1, episodes: [2]}` | one episode (or several) |
| `{media_type: "series", tvdb_id, season: 1}` | every **downloaded** episode of season 1 |
| `{media_type: "series", tvdb_id}` | every downloaded episode of every regular season (specials only when asked by `season: 0`) |
| `episodes` without `season` | `400 season_required` |

A request that covers more episodes than `status.max_episodes` is refused before anything is done
(`400 too_many_episodes`, with `limit` and `requested`). Ask season by season instead.

### Who asked

| Field | Type | Meaning |
|---|---|---|
| `requested_by` | string | The MorpiceHub user's display name. Stored with the request, shown in OmbiSub's logs. |
| `client` | string | Always `"morpicehub"`. |
| `client_ref` | string | MorpiceHub's own id for this request (row id in its table). Returned on every read, so a response can always be tied back. |

---

## 5. Endpoints

### 5.1 `GET /api/on-demand/status`: what OmbiSub can do right now

Use it as the connection test in MorpiceHub's settings (a 401 means a wrong key), and to explain waits.

```json
{
  "sonarr": true,
  "radarr": true,
  "extraction_enabled": true,
  "ffmpeg": true,
  "default_language": "el",
  "default_language_name": "Greek",
  "max_episodes": 30,
  "engine": "agent",
  "paused": false,
  "runner": "idle"
}
```

| Field | Meaning |
|---|---|
| `sonarr`, `radarr` | Connected in OmbiSub. Hide "request" for series/movies when `false`. |
| `extraction_enabled`, `ffmpeg` | Subtitles inside videos can be extracted. |
| `default_language`, `default_language_name` | What a request without `language` translates into. |
| `max_episodes` | Cap per request. |
| `engine` | `"agent"` (agy runner) or `"model"` (Gemini API worker). |
| `paused` | An OmbiSub admin paused translations: requests are accepted and wait. Tell the user. |
| `runner` | With the agent engine: `running`, `idle` (nothing to do), `waiting` (quota pause, or paused), `stopping`, `stopped`, `done`, `error`. `null` with the model engine. `stopped`/`error` means requests will wait until an admin starts it. |

### 5.2 `POST /api/on-demand/subtitles/lookup`: what exists, what a request would do

Read-only: no project is created, nothing is queued. Body: the identity fields plus `season`/`episodes`/
`language` (section 4).

**Episodes asked about** (`season`, optionally `episodes`), or a movie:

```json
{
  "found": true,
  "media_type": "series",
  "title": "Severance",
  "year": 2022,
  "language": "el",
  "language_name": "Greek",
  "project": { "name": "Severance", "exists": false, "has_glossary": false, "has_context": false },
  "extraction_enabled": true,
  "episodes": [
    { "episode_name": "S01E01", "season": 1, "episode": 1, "title": "Good News About Hell", "has_file": true,
      "state": "requestable", "detail": null, "source": "sidecar" },
    { "episode_name": "S01E02", "season": 1, "episode": 2, "title": "Half Loop", "has_file": true,
      "state": "requestable", "detail": null, "source": "embedded" },
    { "episode_name": "S01E03", "season": 1, "episode": 3, "title": "In Perpetuity", "has_file": true,
      "state": "available", "detail": "Severance.S01E03.el.srt", "source": null },
    { "episode_name": "S01E04", "season": 1, "episode": 4, "title": "The You You Are", "has_file": false,
      "state": "not_downloaded", "detail": null, "source": null },
    { "episode_name": "S01E05", "season": 1, "episode": 5, "title": "The Grim Barbarity...", "has_file": true,
      "state": "unavailable", "detail": "image_subtitles_only", "source": null }
  ],
  "summary": { "requestable": 2, "available": 1, "not_downloaded": 1, "unavailable": 1 }
}
```

A movie answers the same shape with one entry whose `season`/`episode` are `null` and whose
`episode_name` is OmbiSub's project name for the movie (e.g. `"Dune (2021)"`).

**The whole show** (no `season`, no `episodes`): only counts per season, read from the folders (no video
is opened). Use it to let the user pick a season.

```json
{
  "found": true, "media_type": "series", "title": "Severance", "year": 2022,
  "language": "el", "language_name": "Greek",
  "project": { "name": "Severance", "exists": false, "has_glossary": false, "has_context": false },
  "extraction_enabled": true,
  "seasons": [ { "season": 1, "episodes": 9, "downloaded": 9, "with_subtitle": 3 } ],
  "episodes": []
}
```

**Lookup episode states**

| `state` | Meaning | `detail` | Offer a request? |
|---|---|---|---|
| `available` | A Greek subtitle already exists: a file next to the video, or a track inside it. | file name, or `embedded_text` / `embedded_image` | No: "Greek subtitles available" |
| `requestable` | Can be translated. `source` says from what: `sidecar` (English file), `embedded` (track inside the video), `unknown` (not inspected because more than 30 episodes were asked; decided at request time). | `null` | **Yes** |
| `in_progress` | Already being worked on (by anyone). | `queued` / `extracting` / `translating` / `paused` | No: show progress (`progress` 0–100 or `null`) |
| `not_downloaded` | Sonarr has no file for it yet. | `null` | No |
| `unavailable` | Nothing to translate from. | reason, see 6.2 | No: show the reason |

### 5.3 `POST /api/on-demand/subtitles`: request subtitles

Body: identity + scope + `language` + `requested_by`, `client`, `client_ref`:

```json
{
  "media_type": "series",
  "tvdb_id": 371980,
  "season": 1,
  "episodes": [1, 2, 3, 4, 5],
  "language": "el",
  "requested_by": "nikos",
  "client": "morpicehub",
  "client_ref": "req-1842"
}
```

Answer `200`, the request with each episode's state:

```json
{
  "id": "15c22360328745d2bb13ff3419d54aad",
  "created_at": "2026-10-31T11:06:35.037297+00:00",
  "requested_by": "nikos",
  "client": "morpicehub",
  "client_ref": "req-1842",
  "media_type": "series",
  "title": "Severance",
  "year": 2022,
  "project_name": "Severance",
  "language": "el",
  "status": "in_progress",
  "summary": { "queued": 1, "extracting": 1, "available": 1, "not_downloaded": 1, "unavailable": 1 },
  "episodes": [
    { "episode_name": "S01E01", "season": 1, "episode": 1, "title": "Good News About Hell",
      "state": "queued", "detail": null, "progress": null, "ahead": 0 },
    { "episode_name": "S01E02", "season": 1, "episode": 2, "title": "Half Loop",
      "state": "extracting", "detail": null, "progress": null },
    { "episode_name": "S01E03", "season": 1, "episode": 3, "title": "In Perpetuity",
      "state": "available", "detail": "Severance.S01E03.el.srt", "progress": null },
    { "episode_name": "S01E04", "season": 1, "episode": 4, "title": "The You You Are",
      "state": "not_downloaded", "detail": null, "progress": null },
    { "episode_name": "S01E05", "season": 1, "episode": 5, "title": "The Grim Barbarity...",
      "state": "unavailable", "detail": "image_subtitles_only", "progress": null }
  ]
}
```

Store `id` with your own request row: it is the handle for every later call.

What OmbiSub does on this call, per episode: imports just that episode from Sonarr/Radarr (never the whole
show), then looks at its video:

1. A Greek subtitle already there → `available`.
2. An English subtitle file beside it → queued for translation (`queued`).
3. An English text track inside the video → queued for extraction, chained into translation (`extracting`).
4. Nothing usable → `unavailable` with the reason.

The first translation of a show OmbiSub has never translated also builds the show's context guide and
glossary first (names, genders, tone), which adds a few minutes to that first episode only.

Translations are queued with **Manual** priority: ahead of scheduled sync and backlog work, behind "Run now",
titles set to **High** priority and freshly downloaded episodes. A title an OmbiSub admin set to **Low**
waits behind everything; a title set to **Ignored** is still translated for an explicit request like this
one, but nothing else of it is.

### 5.4 `GET /api/on-demand/subtitles/{id}`: follow a request

Same shape as the create answer, with every episode's state computed now. `404 {"detail": "Unknown request
id."}` for an id OmbiSub does not know.

While translating, `progress` is the percentage of lines done:

```json
{ "episode_name": "S01E01", "season": 1, "episode": 1, "title": "Good News About Hell",
  "state": "translating", "detail": null, "progress": 25 }
```

When done:

```json
{ "episode_name": "S01E01", "season": 1, "episode": 1, "title": "Good News About Hell",
  "state": "completed", "detail": "Severance.S01E01.el.srt", "progress": 100 }
```

### 5.5 `POST /api/on-demand/subtitles/{id}/cancel`: withdraw a request

Stops the unfinished work this request queued: its waiting translations and extractions are cancelled, and
one being translated right now stops at once (nothing is saved for it). Left alone: episodes already
`available`/`completed`, work queued by someone else, and an episode a **later** request asked for too
(that request keeps it). Answers the request as it stands afterwards (`status` usually becomes `cancelled`,
or `partial` when some episodes were already done, or stays `in_progress` while another request keeps an
episode going). `404` for an unknown id.

### 5.6 `GET /api/on-demand/subtitles?limit=50`: recent requests

`{"requests": [ ...request objects, newest first... ]}`, at most 200. Requests from every client are listed;
filter on `client == "morpicehub"`. Useful for an admin page or to recover after MorpiceHub lost its own
records; normal operation reads single requests by id.

---

## 6. States, reasons and errors

### 6.1 Episode state in a request (`episodes[].state`)

| `state` | Final? | Meaning | Extra fields | Suggested text |
|---|---|---|---|---|
| `queued` | no | Waiting for its turn. | `ahead`: translations before it (approximate). `detail` may hold the last error when it is waiting for a retry. | "Waiting (3 ahead)" |
| `extracting` | no | The English subtitle is being pulled out of the video. | `detail`: last error while it waits for a retry | "Preparing the subtitle" |
| `translating` | no | Being translated. | `progress` 0–100, or `null` when unknown | "Translating 42%" |
| `paused` | no | Gemini worker only: the daily API quota is used up; it resumes by itself. | `detail`: `daily_quota` | "Paused until the daily quota resets" |
| `completed` | yes | Translated by this request; the file is next to the video. | `detail`: file name; `progress`: 100 | "Greek subtitles ready" |
| `available` | yes | Already existed when the request was made. | `detail`: file name, or `embedded_text` / `embedded_image` | "Greek subtitles available" |
| `not_downloaded` | yes | No video file yet. | | "Not downloaded yet" |
| `unavailable` | yes | Nothing to translate from. | `detail`: reason (6.2) | see 6.2 |
| `failed` | yes | Gave up. An OmbiSub admin can retry it; a new request also retries it. | `detail`: first line of the error | "Could not translate this one" |
| `cancelled` | yes | Cancelled (by this client, or an OmbiSub admin). | | "Cancelled" |

Treat any state you do not know as "not final".

### 6.2 `unavailable` reasons (`detail`)

| `detail` | Meaning | Suggested text |
|---|---|---|
| `no_subtitles` | The video has no subtitle tracks and there is no English subtitle file. | "No English subtitles to translate from" |
| `image_subtitles_only` | Only image subtitles (PGS/VobSub); they would need OCR. | "Only picture subtitles, which can't be translated" |
| `no_source_language` | Subtitle tracks exist, none in English. | "No English subtitles to translate from" |
| `media_unreachable` | OmbiSub cannot reach the video file (share offline, wrong path mapping). | "The video can't be reached right now" |
| `probe_failed` | Reading the video's tracks failed. | "The video couldn't be read" |
| `ffmpeg_unavailable` | ffmpeg is not installed/configured in OmbiSub. | "OmbiSub can't read video files (ffmpeg missing)" |

### 6.3 Request status (`status`)

| `status` | Final? | Meaning |
|---|---|---|
| `in_progress` | no | At least one episode is `queued`, `extracting`, `translating` or `paused`. |
| `completed` | yes | Every episode is `completed` or `available`. |
| `partial` | yes | Some are done; the rest cannot be (unavailable, not downloaded, failed or cancelled). |
| `cancelled` | yes | Nothing done, and what was in progress was cancelled. |
| `failed` | yes | Nothing done, at least one episode failed. |
| `unavailable` | yes | Nothing could be done: every episode is unavailable or not downloaded. |

`summary` counts the episodes per state, e.g. `{"translating": 1, "queued": 3, "available": 2}`.

### 6.4 Errors

| HTTP | `code` | When | What MorpiceHub should do |
|---|---|---|---|
| 401 | (none) | Missing or wrong `X-Api-Key`. | Mark the integration as misconfigured; show admins "OmbiSub rejected the API key". |
| 422 | (none) | Bad body: unknown `media_type`, no identity field, wrong types. | A bug in MorpiceHub; log it. |
| 400 | `season_required` | `episodes` without `season`. | Bug; always send the season. |
| 400 | `too_many_episodes` | More than `limit` episodes. Extra: `limit`, `requested`. | Split by season, or tell the user the limit. |
| 404 | `not_in_library` | Sonarr/Radarr do not have this title. | "This title isn't managed by Sonarr/Radarr, so subtitles can't be requested." |
| 404 | `no_episodes` | No downloaded episode matches the season/episodes asked. | "None of these episodes are downloaded yet." |
| 409 | `ambiguous` | A `title` matched several library titles. Extra: `candidates: [{title, year}]`. | Retry with `year` or an id. |
| 502 | `arr_error` | Sonarr/Radarr answered with an error. | Retry later; "OmbiSub couldn't reach Sonarr/Radarr". |
| 503 | `sonarr_not_configured` / `radarr_not_configured` | Not connected in OmbiSub. | Hide requests for that media type (see `status`). |
| 404 | (none) | Unknown request id on read/cancel. | The request is gone (OmbiSub data reset); mark it lost. |
| 5xx / network | | OmbiSub down or restarting (updates restart it). | Retry with backoff; keep the request as pending on MorpiceHub's side. |

---

## 7. Implementing it in MorpiceHub

### 7.1 Server-side client

One small module on MorpiceHub's server, configured with `OMBISUB_URL` and `OMBISUB_API_KEY`:

```ts
// Illustration only: adapt to MorpiceHub's language and HTTP library.
type MediaType = "movie" | "series";

interface TitleRef {
  media_type: MediaType;
  tmdb_id?: number; tvdb_id?: number; imdb_id?: string;
  radarr_id?: number; sonarr_id?: number;
  title?: string; year?: number;
}

interface Scope { season?: number; episodes?: number[]; language?: string; }

interface RequestBody extends TitleRef, Scope {
  requested_by?: string; client?: "morpicehub"; client_ref?: string;
}

type EpisodeState = "queued" | "extracting" | "translating" | "paused" | "completed" | "available"
  | "not_downloaded" | "unavailable" | "failed" | "cancelled";

interface RequestEpisode {
  episode_name: string; season: number | null; episode: number | null; title: string;
  state: EpisodeState; detail: string | null; progress: number | null; ahead?: number | null;
}

interface SubtitleRequest {
  id: string; created_at: string; requested_by: string | null; client: string | null; client_ref: string | null;
  media_type: MediaType; title: string; year: number | null; project_name: string; language: string;
  status: "in_progress" | "completed" | "partial" | "cancelled" | "failed" | "unavailable";
  summary: Record<string, number>; episodes: RequestEpisode[];
}

class OmbiSub {
  constructor(private base: string, private key: string) {}
  private async call<T>(method: string, path: string, body?: unknown, timeoutMs = 15000): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method,
      headers: { "X-Api-Key": this.key, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.detail ?? res.statusText), { status: res.status, code: data.code, data });
    return data as T;
  }
  status() { return this.call("GET", "/api/on-demand/status", undefined, 10000); }
  lookup(q: TitleRef & Scope) { return this.call("POST", "/api/on-demand/subtitles/lookup", q, 60000); }
  request(b: RequestBody) { return this.call<SubtitleRequest>("POST", "/api/on-demand/subtitles", { client: "morpicehub", ...b }, 120000); }
  get(id: string) { return this.call<SubtitleRequest>("GET", `/api/on-demand/subtitles/${encodeURIComponent(id)}`); }
  cancel(id: string) { return this.call<SubtitleRequest>("POST", `/api/on-demand/subtitles/${encodeURIComponent(id)}/cancel`); }
}
```

### 7.2 What to store

A `subtitle_requests` table (or equivalent):

| Column | Notes |
|---|---|
| `id` | MorpiceHub's own id; send it as `client_ref`. |
| `ombisub_id` | `id` from the create answer. |
| `user_id` | Who asked (for "my requests" and per-user limits). |
| `plex_rating_key` | The movie, show, season or episode it was asked from, to show progress there. |
| `media_type`, `title`, `season`, `episodes`, `language` | What was asked. |
| `status`, `summary`, `episodes_json` | Last answer from OmbiSub, for rendering without a call. |
| `created_at`, `last_polled_at`, `finished_at` | Polling bookkeeping. |

### 7.3 Polling

- Poll `GET /api/on-demand/subtitles/{ombisub_id}` from MorpiceHub's server, not from each browser: one
  poller for all open requests, results written to the table, browsers read MorpiceHub.
- Interval: every 10 s for the first 2 minutes, then every 30 s, then every 60 s after 15 minutes. Stop
  when `status` is final (6.3). A "refresh" action may poll once on demand.
- Notify the user when an episode reaches `completed` (and once more when the whole request is final).
- Expect minutes, not seconds: an episode usually takes a few minutes once it starts; a show's first
  episode adds a few minutes for the context guide; extraction adds the time to read the video file;
  everything waits for the work queued before it (`ahead`). If `status.paused` is true or `status.runner`
  is `stopped`/`error`, say that OmbiSub is not translating right now.

### 7.4 Retries and duplicates

- Create is safe to retry after a timeout or network error: OmbiSub keeps one piece of work per (title,
  episode, language). A retry may create a second request record; keep the newest `id` (match on
  `client_ref` via the list endpoint if the first answer was lost).
- Before offering "Request" on a title, a **lookup** tells you whether it is already `available` or
  `in_progress`; do not offer a request for those.
- Two users asking for the same episode: both requests follow the same work, and both see it complete.

### 7.5 Caching lookups

A lookup may open video files on a network share. Cache its answer per (title, scope, language) for
about 5 minutes, refresh it after the user's own request, and never run lookups for every item of a long
list: use the whole-show lookup (season counts, no files opened) for show pages and per-episode lookups
only when a season or an episode is opened.

### 7.6 Suggested UI

- **Movie / episode page:** a "Greek subtitles" line: *Available* · *Request Greek subtitles* button
  (`requestable`) · *Translating 42%* / *Waiting (3 ahead)* · *Not downloaded yet* · the unavailable reason.
- **Season page:** "3 of 9 episodes have Greek subtitles · Request the other 6" (counts from the whole-show
  lookup, the button sends `season` only).
- **Show page:** per-season counts from the whole-show lookup; request per season (respecting
  `max_episodes`).
- **My requests:** the user's requests with status, per-episode progress, Cancel while `in_progress`.
- **After completion:** "Ready: pick *Greek* in Plex's subtitle menu". If Plex does not show it yet,
  MorpiceHub can ask Plex to refresh the item (`PUT {PLEX}/library/metadata/{ratingKey}/refresh`); OmbiSub
  already asks Plex to rescan the folder when Plex is connected in OmbiSub.
- **Admin settings:** OmbiSub URL, API key, a "Test connection" button that calls `status` and shows Sonarr,
  Radarr, ffmpeg, engine and runner state.

### 7.7 Limits worth adding on MorpiceHub's side

Every episode is a full translation. A per-user cap (for example 3 open requests, or N episodes a day) and
an admin-only "whole show" request keep one enthusiastic user from filling OmbiSub's queue.

---

## 8. Try it with curl

```bash
export OMBISUB_URL="http://ombisub-host:2945"
export OMBISUB_KEY="paste the key from OmbiSub › Settings › Security"
```

```bash
curl -s -H "X-Api-Key: $OMBISUB_KEY" "$OMBISUB_URL/api/on-demand/status"
```

```bash
curl -s -H "X-Api-Key: $OMBISUB_KEY" -H "Content-Type: application/json" \
  -d '{"media_type": "series", "tvdb_id": 371980, "season": 1}' \
  "$OMBISUB_URL/api/on-demand/subtitles/lookup"
```

```bash
curl -s -H "X-Api-Key: $OMBISUB_KEY" -H "Content-Type: application/json" \
  -d '{"media_type": "series", "tvdb_id": 371980, "season": 1, "episodes": [2], "requested_by": "test", "client": "morpicehub", "client_ref": "t1"}' \
  "$OMBISUB_URL/api/on-demand/subtitles"
```

```bash
curl -s -H "X-Api-Key: $OMBISUB_KEY" "$OMBISUB_URL/api/on-demand/subtitles/<id from the answer above>"
```

```bash
curl -s -X POST -H "X-Api-Key: $OMBISUB_KEY" "$OMBISUB_URL/api/on-demand/subtitles/<id>/cancel"
```

```bash
curl -s -H "X-Api-Key: $OMBISUB_KEY" -H "Content-Type: application/json" \
  -d '{"media_type": "movie", "tmdb_id": 438631}' \
  "$OMBISUB_URL/api/on-demand/subtitles/lookup"
```

### Acceptance checklist for the MorpiceHub work

- [ ] Settings: URL + key saved server-side; "Test connection" shows the `status` fields; a wrong key shows
      a clear error.
- [ ] A movie with a Greek file shows *Available*; no request button.
- [ ] An episode with an English subtitle file shows *Request*; requesting it shows *Waiting*, then
      *Translating n%*, then *Ready*, and the subtitle appears in Plex.
- [ ] An episode whose English subtitle is only inside the video goes *Preparing* → *Translating* → *Ready*.
- [ ] An episode with only picture subtitles shows the reason and no request button.
- [ ] A season request covers only downloaded episodes; more than `max_episodes` is handled (split or
      explained).
- [ ] Cancel during *Waiting* and during *Translating* both end as *Cancelled*; OmbiSub's Activity page shows
      the item cancelled.
- [ ] Two users requesting the same episode both see it complete; one cancelling does not stop the other's
      (the later request keeps it).
- [ ] OmbiSub restarting during a request (updates restart it): MorpiceHub keeps polling and the request
      finishes.
- [ ] OmbiSub with translations paused: the request is accepted and the user is told it waits.
- [ ] The API key never reaches the browser (check the network tab).

---

## 9. Compatibility

- Fields may be added to any answer; ignore what you don't know.
- New episode states or reasons may appear; treat an unknown state as "not final" and show unknown reasons
  generically.
- Endpoint paths and the fields listed here are kept stable; changes to them are announced in this file.

Questions about OmbiSub's behaviour are answered by its code: `backend/services/on_demand.py` (routing,
states), `backend/routers/on_demand.py` (HTTP), `backend/tests/test_on_demand.py` (the cases above).
