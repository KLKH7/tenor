# Granola schema notes: VERIFIED vs ASSUMED

Docs read on **2026-10-29**. Sources:

- [intro] https://docs.granola.ai/introduction
- [spec] https://docs.granola.ai/api-reference/openapi.json (`info.version` 1.0.0). Only the first ~39 KB of 102 KB was read verbatim. Items marked *(spec summary)* rest on a tool summary of the rest of the file, not a verbatim read.
- [list-notes] https://docs.granola.ai/api-reference (serves the List Notes page)
- [get-note] https://docs.granola.ai/api-reference/get-note.md
- [get-transcript] https://docs.granola.ai/api-reference/get-transcript.md
- [list-folders] https://docs.granola.ai/api-reference/list-folders.md
- [changelog] https://docs.granola.ai/api-reference/changelog.md (latest: **v1.6.0, Oct 2026**)
- [api-help] https://docs.granola.ai/help-center/sharing/integrations/granola-api.md
- [webhooks] https://docs.granola.ai/webhooks.md
- [mcp] https://docs.granola.ai/help-center/sharing/integrations/mcp

Tags:

- **VERIFIED**: read in the docs, with the source given.
- **ASSUMED**: the docs are silent, so we picked a behaviour. The behaviour is stated, and so is where in the code it lives.
- **CORRECTED**: the project brief said something the docs contradict. We follow the docs.

## Corrections to the project brief

| Brief said | Docs say | Status |
|---|---|---|
| Transcript `source: "microphone" \| "system"` | `speaker.source: "microphone" \| "speaker"`, plus optional `attribution: "me" \| "them"`, `diarization_label` (iOS only) and `name` (only when identified) | CORRECTED [spec] |
| No webhooks; freshness = polling | Webhooks exist (Business/Enterprise): `note.generated`, `note.edited`, `note.access_granted`, Standard Webhooks signing | CORRECTED [webhooks]. We still poll (see DESIGN.md "Sync") |
| API ~1.2.0 | Changelog is at v1.6.0 (Oct 2026); the spec's `info.version` still says 1.0.0 | CORRECTED [changelog] |
| Transcript only via `?include=transcript` | Also `GET /v1/notes/{id}/transcript` (v1.4.0), paginated up to 100 per page. Inline include can fail with `413 TRANSCRIPT_TOO_LARGE` | CORRECTED [get-note][get-transcript] |
| Note = typed notes + summary + … | Typed notes are `private_notes_markdown` / `private_notes_text`, **populated only when the key belongs to the note's creator** | CORRECTED [get-note] |

## REST API

| Item | Value | Status |
|---|---|---|
| Base URL | `https://public-api.granola.ai`, paths under `/v1/` | VERIFIED [spec] |
| Auth | `Authorization: Bearer grn_…` | VERIFIED [intro] |
| Plans | Business / Enterprise. Scopes: Personal notes, Public notes; workspace keys for admins | VERIFIED [api-help] |
| Sandbox | None | VERIFIED [api-help] |
| Read/write | All note endpoints are GET. Search is POST but read-only | VERIFIED [spec] |
| Rate limit | Burst 25 per 5 s window; sustained 5 req/s; 429 when exceeded; per user or workspace | VERIFIED [intro] |
| `Retry-After` on 429 | Not documented. We honour it if present (seconds form), else use exponential backoff with full jitter | ASSUMED (`granola_api._retry_after`) |
| Error body shape | Not documented (descriptions only). We never parse error bodies | ASSUMED |
| 5xx / transport errors | Not documented. Retried with backoff, bounded by `max_retries` | ASSUMED |
| Processing notes | Excluded from List; Get returns 404. "API only returns notes that have a generated AI summary and transcript" | VERIFIED [intro] |
| 404 ambiguity | A 404 can mean still processing, deleted or no access. Docs give no way to tell them apart | ASSUMED: retry, then mark `pending` and retry on later syncs |

### Pagination (all list endpoints)

| Item | Value | Status |
|---|---|---|
| Request | `cursor` query param (search: JSON body) | VERIFIED [list-notes] |
| Response | `hasMore: bool` (camelCase), `cursor: string \| null` | VERIFIED [list-notes] |
| `page_size` | 1–30, default 10 (notes, folders, spaces, search); 1–100, default 50 (transcript) | VERIFIED |
| Order of List Notes | Not documented. Sync never depends on order | ASSUMED |
| Order of List Folders | "sorted alphabetically" | VERIFIED [list-folders] |

### `GET /v1/notes` filters

| Param | Type | Status |
|---|---|---|
| `created_before`, `created_after`, `updated_after` | date or date-time string | VERIFIED [list-notes] |
| Inclusive or exclusive bounds | Not documented. FixtureSource and the fake server treat `*_after` as exclusive (`>`) and `created_before` as exclusive (`<`). Sync re-scans a 10-min overlap window, so correctness doesn't depend on this | ASSUMED (`fixtures.py`, `poller.py`) |
| `folder_id` | `fol_` + 14 alnum; includes child folders | VERIFIED [list-notes] |
| `space_id` | `spa_` + 14 alnum | VERIFIED [list-notes]; not used yet |
| Whether `updated_at` changes when a processing note finishes | Not documented. If it doesn't, a note that finished processing could have `updated_at` older than our watermark and be missed by `updated_after` polling. Mitigations: overlap window, `sync --full`, webhooks (`note.generated`) in future | ASSUMED: **open risk** |

### Note object (`NoteSummary` ⊂ `Note`)

| Field | Type | Status |
|---|---|---|
| `id` | `^not_[a-zA-Z0-9]{14}$` (path param case-insensitive) | VERIFIED [spec][get-note] |
| `object` | `"note"` | VERIFIED |
| `title` | string \| null | VERIFIED |
| `owner` | `User {name: string\|null, email}` | VERIFIED |
| `created_at`, `updated_at` | date-time | VERIFIED |
| `deleted_at` | date-time \| null; only visible to keys with access to all workspace notes | VERIFIED [list-notes] |
| `web_url` | uri | VERIFIED [get-note] |
| `calendar_event` | `CalendarEvent \| null` | VERIFIED |
| `attachments` | `NoteAttachment[]` (`att_…`, `type` e.g. `photo`, `url`, `width`, `height`) | VERIFIED |
| `attendees` | `User[]` | VERIFIED |
| `folder_membership` | `Folder[]`; includes ancestor folders since v1.1.0 | VERIFIED [changelog] |
| `space_membership` | `Space[]`; not in the spec's `required` list, so we default it to `[]` | VERIFIED (optionality) |
| `summary_text` | string, **non-null** | VERIFIED [get-note] |
| `summary_markdown` | string \| null | VERIFIED |
| `private_notes_text` / `private_notes_markdown` | string \| null; only for the creator's key | VERIFIED |
| `transcript` | `Transcript[] \| null`; null unless `include=transcript` | VERIFIED |
| `summary` (seen in the intro quick-start example) | **Not in the schema**; the example is out of date. We don't model it | VERIFIED discrepancy |

### `CalendarEvent`

`event_title` (string\|null), `invitees` (`[{email}]`), `organiser` (email\|null, British spelling), `calendar_event_id` (string\|null), `scheduled_start_time` / `scheduled_end_time` (date-time\|null). All VERIFIED [get-note].

### `Transcript` item

| Field | Status |
|---|---|
| `speaker.source` ∈ {`microphone`, `speaker`}, required | VERIFIED [spec] |
| `speaker.attribution` ∈ {`me`, `them`}, omitted when unknown | VERIFIED |
| `speaker.diarization_label` (e.g. "Speaker A"), iOS only | VERIFIED |
| `speaker.name`, only when identified | VERIFIED |
| `text`, `start_time`, `end_time` (date-time) | VERIFIED |
| No per-utterance ID | VERIFIED (absent from schema). We synthesize `{note_id}:{index:04d}` |
| Items are returned in chronological order and the order is stable across fetches | ASSUMED. Our utterance IDs depend on it |
| Channel mapping when `attribution` is absent: `microphone`→Me, `speaker`→Them | ASSUMED. Matches the product: mic = owner, system audio = others |
| In-person meetings: everyone is on the microphone, so everything maps to "Me" unless diarized | ASSUMED from the product's capture model. The sample fixture `not_SmplInPerson01` models it |

### `Folder` / `Space`

`Folder {id: fol_…, object: "folder", name, parent_folder_id: fol_…|null, space_id?: spa_…|null}` and `Space {id: spa_…, object: "space", name}`. Both VERIFIED [spec][list-folders].

### Endpoints we don't use yet

These exist [spec summary]: `GET /v1/spaces`, `POST /v1/notes/search` (keywords, participant and date filters; cursors expire after 15 min; `completion_status`), `GET /v1/audit`, `/v1/webhook-endpoints`, `/v1/legal-holds`.

## MCP server

| Item | Value | Status |
|---|---|---|
| URL / transport | `https://mcp.granola.ai/mcp`, Streamable HTTP | VERIFIED [mcp] |
| Auth | Browser OAuth 2.0 with discovery + DCR; Enterprise-Managed Authorization; **no API-key access** | VERIFIED [mcp] |
| Rate limit | About 100 req/min across all tools | VERIFIED [mcp] |
| Plans | Free: last 30 days only. Business: personal + public. Enterprise: admin-configured (can disable transcripts, read-only) | VERIFIED [mcp] |
| Tools | `query_granola_meetings`, `list_meetings`, `get_meetings`, `get_meeting_transcript` (paid), `list_meeting_folders` (paid), `get_meeting_attachment`, `get_account_info`, `switch_workspace`, `sign_out` | VERIFIED [mcp] |
| Tool parameter schemas | **Not published** on the docs page | ASSUMED when we mirror them in `concentrate-mcp` (Phase 6) |
| Tools listed in the Claude connector registry (2026-11-02) | `query_granola_meetings`, `search_meetings`, `get_meetings`, `list_meetings`, `search_transcripts`, `get_meeting_transcript`, `get_meeting_attachment`, `list_meeting_folders`. **`search_meetings` and `search_transcripts` are new** since the docs were read on 2026-10-29, so retrieval tools are evolving. To be checked live once connected | VERIFIED (registry listing) |
| MCP sees only the user's private notes; the API reaches team spaces with the right scope | Partly: MCP is "personal and public notes" on Business. The brief's "only My notes" is narrower than the docs | VERIFIED [mcp], brief adjusted |

## Assumptions specific to the synthetic dataset (Phase 2)

| Behaviour we simulate | Status |
|---|---|
| Owner's turns are `microphone`; other call participants are `speaker`; in-person meetings are all `microphone` | ASSUMED from the product's capture model |
| `attribution` omitted everywhere | VERIFIED as allowed ("omitted when unknown"); simplest faithful choice |
| `diarization_label` only on one in-person meeting recorded on iOS | VERIFIED as iOS-only; which meetings get it is our choice |
| `speaker.name` on most "Them" passages (95% of remote meetings identify 90% of speakers; 10% of their passages stay bare `System audio`); the owner's microphone is never named | Calibrated on real MCP-synced meetings: 0.82 named, synthetic 0.80 (`docs/archive/PROBE_2026-11-02.md` §6). Small sample (4 meetings) |
| Remote passages are bimodal: 45% of turns kept whole, the rest split into sentences and fragments, plus one-word lead-ins; in-person meetings merge into long single-microphone blocks (5-140 words) | Calibrated on real data (§6). The remote long tail is under-reproduced (known gap) |
| `summary_markdown` uses `###` headings and `-` bullets, and omits some content | ASSUMED from the product UI, not the API docs |
| `summary_markdown` (northwind-2) has 4-6 `###` sections with two-level `-` bullets (~25-30), lossy on facts, and writes numbers as digits/`$`/`%` | ASSUMED: structure calibrated on MCP-delivered summaries of real meetings (`#` headings there, 4.5 sections, 34 bullets, 15 nested) and one public shared note (6 × `###`, 30 + 41 nested bullets). That the REST API returns the same markdown as the MCP `<summary>` is unverified; the API doc example shows `##` and flat bullets |
| Transcript numbers: ~75% of turns use digits/`$`/`%`/`312K`, the rest spelled out | ASSUMED: real transcripts show 2.4 digit tokens vs 4.4 number words per 1k words (n = 3); exact ASR formatting rules unknown |
| `attendees` empty and `calendar_event` null for an ad-hoc meeting | ASSUMED (attendees come from the calendar) |
| ~25% of non-owner attendees have `name: null` (invite without a display name) | Shape VERIFIED live: the MCP shows such people by email handle, e.g. `Az1234`. Rate ASSUMED |
| `organiser` is the note owner for internal meetings and the account owner for customer calls | ASSUMED |
| `created_at` ≈ meeting start; `updated_at` = end + 1–15 min (summary generation) | ASSUMED |
| v2.1 `--extras`, hybrid room: everyone in a conference room shares one system-audio channel; when Granola names it, the label is the room's name ("Room 4B"), never an in-room person. The room is a calendar resource among `invitees`, not in `attendees` | ASSUMED. Channel sharing follows from the capture model (one far-end audio stream). How Granola labels a room endpoint is unverified: it might stay bare `System audio` or take the name of one in-room invitee. Both alternatives keep attribution as hard or make it harder |
| v2.1 display-name change: invites and speaker labels show the name and email the person had on the meeting date; the directory export shows only the current one | ASSUMED (calendar invites snapshot the attendee; HRIS exports hold current names) |
| v2.1 contractor on a personal email address: no display name on invites, so Granola never names their passages; absent from the directory | ASSUMED (follows from naming coming from calendar display names) |

## Live MCP probe (2026-11-02): see `docs/archive/PROBE_2026-11-02.md`

| Item | Value | Status |
|---|---|---|
| `query_granola_meetings` | An agent that searches and reads notes; answers are summary-first with `[[n]](https://notes.granola.ai/d/<id>)` citations | VERIFIED (live) |
| `query_granola_meetings` `document_ids` | Limits the agent to the given notes (max 10). 57 AMI answers scoped to one note each (2026-11-09) cited only that note | VERIFIED (tool schema; live, 57 of 57) |
| Black-box track (`tenor.blackbox`): `query_granola_meetings` changes nothing in the account, so it is the only tool the Granola adapter allowlists | ASSUMED from its name and docs ("query"); Granola's tool annotations (`readOnlyHint`) are not checked, because the OAuth caller doesn't list tools. The allowlist still refuses every other tool before sending |
| Black-box track: `query_granola_meetings` returns no abstain flag and no structured citations, so abstention is judged from the text and citations are parsed from the `notes.granola.ai/d/<id>` links | VERIFIED (live, 39 recorded answers) for the format; how stable it is over time is ASSUMED, so each recording keeps `asked_at` |
| Transcript speaker labels via MCP | `Microphone`, `System audio`, or `System audio (<Full Name>)` when identified; plus `recording_context` (recorder, `microphone_sharing`) | VERIFIED (live). **Corrects** our assumption that names are rare |
| `get_meeting_transcript(chunk_ids)` chunks: until 2026-11-04 a `passages` list (`speaker`, absolute `time`, `text`); **from 2026-11-05 a single rendered `transcript` string** of `[m:ss] Label: text` lines, offsets relative to the earliest timed block. No announcement; our sync silently read 0 lines until fixed. Both formats parsed; rendered offsets anchored at the note's `created_at` (approximate) | VERIFIED (live, 2026-11-05) |
| Rendered transcript timing, as synced (7 AMI meetings, 2026-11-05) | Start offsets are whole seconds; there is no end time (`end_time` is our estimate: next start or 2.6 words/s). Offset 0 is the first block, not the recording start. Against AMI's word timings each meeting needs one constant shift (−1.1 to 37.6 s) with drift under 1 s per hour; 67–83% of blocks contain a word triple unique in the meeting. Used by the capture-health detector (`ami/detector.py`) | VERIFIED (AMI sync, aggregate checks only) |
| `search_transcripts` | Ranked 240-character passages with `chunk_id`, `transcript_version`, **no speaker**; read neighbouring chunks via `get_meeting_transcript(chunk_ids)` | VERIFIED (live) |
| MCP write tools | `update_private_notes`, `create_folder`, `update_folder`, `add_note_to_folder`, `remove_note_from_folder`, `generate_summary`, `list_templates`, `give_feedback` | VERIFIED (tool list); **CORRECTS** "read-only" |
| Account plan seen by the MCP | `mcp_plan: "plus"` | VERIFIED (live) |
| Unofficial internal API (`api.granola.ai/v1/get-document-transcript`) | utterances have `source`, `start_timestamp`, `end_timestamp`, `confidence` | UNOFFICIAL: reverse-engineered, archived 2026-02-05; **not used** |

## MCP source mapping (`tenor.sources.granola_mcp`)

How `GranolaMCPSource` turns MCP tool output into our wire models (`McpNote`/`McpNoteSummary`, which are the API models with UUID IDs):

| Wire field | From the MCP | Status |
|---|---|---|
| `id` | meeting UUID (the MCP never returns `not_` IDs) | VERIFIED live; stored with `raw_format = 'mcp'` |
| `title`, `web_url`, `updated_at` | `<meeting title url updated_at>`; `updated_at` is only present when `updated_after` is sent, so we always send it | VERIFIED live |
| `created_at` | `get_meetings` `created_at`; list rows have none, so summaries use `meeting_at` | VERIFIED (get), ASSUMED (list) |
| `owner` | participant marked `(note creator)`, else the connected account (`get_account_info`) | VERIFIED live |
| `attendees` | `known_participants` lines `Name [(note creator)] [from Org] (email)` | VERIFIED live format; the parser is lenient |
| `calendar_event` | synthesized when `meeting_at != created_at`: title, invitees = participants, start = `meeting_at`; `organiser`, `calendar_event_id`, end = null | ASSUMED (the MCP exposes no event object) |
| `summary_markdown` / `summary_text` | `<summary>`, HTML entities unescaped; text = same markdown | VERIFIED live |
| No AI summary | `<summary status="transcript_only">` (transcript, no summary) or `status="empty"` holds a placeholder sentence ("No AI summary has been generated…"). Mapped to `summary_markdown` null, `summary_text` "". Before 2026-11-09 the placeholder was stored as the summary (8 AMI-account notes, re-fetched). Seen on recordings that stopped on inactivity or were never opened after stopping: Granola had not generated notes | VERIFIED live (2026-11-09) |
| `private_notes_*` | `<private_notes>` content; `status="empty"` or `"not_included"` → null | VERIFIED live (empty case) |
| `folder_membership` | `<folder id title>` elements in a meeting; `list_meeting_folders` JSON `folders[]` with `id`, `title`/`name`, `parent_folder_id` | ASSUMED: the account has no folders |
| `transcript` | `get_meeting_transcript` read in batches of 10 `chunk_ids` with a pinned `transcript_version`; speaker `Microphone` → microphone, `System audio [(Name)]` → speaker (+name), other labels → microphone + `diarization_label` | VERIFIED live format |
| transcript `end_time` | not provided: next passage's start, capped by a length estimate | ASSUMED |
| `deleted_at` | always null (the MCP doesn't return deleted notes) | VERIFIED (documented behaviour) |
| Rate limit | paced at 100 req/min, burst 10; `retry_after_seconds` honoured on rate-limit errors | VERIFIED (docs: ~100/min); error format ASSUMED |

## Wispr Flow MCP (`tenor.sources.wispr_mcp`)

Wispr Flow is the second real product (see [PRODUCTS.md](../method/HARNESS.md)). Sources, read **2026-11-04**:

- [wispr-help] Wispr help centre, "Connect Claude, ChatGPT or another MCP client to Wispr Flow": https://docs.wisprflow.ai/articles/9551372685-connect-an-mcp-client-to-wispr-flow-remote-mcp-server
- [wispr-search] help-centre pages surfaced by web search (Cursor/VS Code config examples) that give the server URL
- [wispr-tools] the server's published tool list (names, descriptions, input schemas), read through a connected client. **No tool was called and no meeting was read** to write this section.

### Connection

| Item | Value | Status |
|---|---|---|
| Server URL | `https://api.wisprflow.ai/connect/mcp`. The page we read verbatim does not print it: it says to copy it from Wispr Flow → Settings → MCP → "All other apps", exactly as shown, no trailing slash. The URL comes from other help-centre pages via search | ASSUMED [wispr-search]; overridable with `WISPR_MCP_URL` |
| Transport | Streamable HTTP. The help page says only "remote MCP server" | ASSUMED |
| Auth | Browser sign-in and authorization; "no API key or token"; expired access prompts reconnection; SSO sign-in must finish within 5 minutes | VERIFIED [wispr-help] |
| OAuth with dynamic client registration and a loopback redirect (`http://127.0.0.1:33418/callback`) | Not documented. We reuse Granola's OAuth client (`mcp_transport.LiveMcpCaller`, its own token file `~/.config/concentrate/wispr-mcp.json`). If Wispr only allows pre-registered clients (Claude, ChatGPT, Cursor), sign-in fails at registration | ASSUMED |
| Read-only | Clients "cannot create, edit, share or delete" content or change sharing; no dictation or Notetaker chat history | VERIFIED [wispr-help] |
| Requirements | Mac/Windows account with Notetaker and MCP access; Cloud Sync may need to be on; a HIPAA BAA or an org policy turns MCP off | VERIFIED [wispr-help] |
| Rate limit | No numbers. "Too many requests": wait about a minute and retry. We pace at 60/min (burst 5) and wait 60 s on that error | VERIFIED (behaviour) / ASSUMED (our pacing) |
| Timestamps | "Every timestamp in the response is UTC (ISO 8601, ending in 'Z')" | VERIFIED [wispr-tools] |

### Tools (all read; VERIFIED [wispr-tools])

`search_meetings`, `get_meeting`, `get_meeting_participants_enriched`, `list_folders`, `list_folder_meetings`, `list_meeting_series`, `get_meeting_by_calendar_id`, `search_calendar_events`, `get_calendar_event`, `list_upcoming_meetings`, `get_upcoming_meeting`, `get_scratchpad_note`, `search_scratchpad_notes`, `resolve_share_link`, `resolve_calendar_link`. There is **no question-answering agent tool**. The source allowlists only the first three (`wispr_mcp.SOURCE_TOOLS`).

| Tool / argument | Behaviour | Status |
|---|---|---|
| `search_meetings` | `query` (titles, summary, owned notes, speaker names; **not transcripts**), `since`/`until` (calendar start, else creation; half-open), `attendee_emails` + `attendee_match`, `contact_ids`, `field`, `limit` (default 25, max 200), `cursor`. Newest first; `has_more` + `next_cursor`; capped at 1000 results per query (`truncated`) | VERIFIED [wispr-tools] |
| `search_meetings` rows | id, title, excerpt, `finalized`, `has_transcript`, up to 5 attendees, start/end, `share_link`, `shared`, `owner.name`, `folders` | VERIFIED (field list); exact key names ASSUMED |
| `get_meeting` | `meeting_id`; `view_content` and `view_transcript` `{start_char, char_limit}` (default 12,000, max 40,000). Content: bounded markdown notes, first range by default. Summary: complete, with a next-steps section. Attendees without emails. Transcript: bounded **plaintext**, only when `view_transcript` is passed. Truncated ranges carry a continuation offset "in the appended marker" | VERIFIED [wispr-tools] |
| `get_meeting_participants_enriched` | Owned meetings only. The user's own name/email/domain, plus every participant with name, `contact_id`, `emails`, spoke, organizer, `role` (self, organizer, attendee, speaker-only), relationship | VERIFIED [wispr-tools]; key names ASSUMED |
| Updated-since filter | None. Every sync re-lists everything; unchanged rows are skipped on `updated_at` | VERIFIED (no such argument) |
| Shared meetings | Summary and permitted transcript only; owner's private notes withheld. We skip `shared=true` rows by default | VERIFIED [wispr-tools] |

### Mapping to our wire models (`WisprNote`, `WisprNoteSummary`: the API models with free-form IDs, `raw_format = 'wispr'`)

| Wire field | From Wispr | Status |
|---|---|---|
| Response encoding | JSON objects; a `{"meeting": {...}}` wrapper is unwrapped; list key `meetings` (or `results`/`items`/`data`) | ASSUMED (not yet observed) |
| `id` | meeting id, verbatim | VERIFIED (opaque string) |
| `created_at` | meeting start (`start`/`start_time`/`started_at`/...). Wispr exposes no creation time we know of | ASSUMED |
| `updated_at` | `updated_at`/`modified_at`, else end, else start. Without a real modified time, edits to a meeting after its first sync are missed; run `sync --full` to re-read | ASSUMED |
| `owner` | `get_meeting_participants_enriched` top-level user; else `owner.name` with email `""` | ASSUMED |
| `attendees` | enriched participants except `role: self`, first email (or `""`); else `get_meeting` attendee names with email `""`. The `attendees` table keys on (note, email), so nameless-email rows collapse there; `raw_json` keeps them all | ASSUMED |
| `calendar_event` | synthesized when a start time exists: title, invitees = attendee emails, organiser = participant with `organizer: true`, start/end. `calendar_event_id` null | ASSUMED |
| `summary_markdown` / `summary_text` | `summary` (the Flow Summary) | VERIFIED field; ASSUMED key name |
| `private_notes_markdown` | `content`, all ranges concatenated. **Not typed notes**: Wispr's `content` is its markdown notes body, which its tool text calls "auto-generated" | ASSUMED |
| `folder_membership` | meeting `folders` `[{id, name}]` | VERIFIED shape [wispr-tools] |
| Not finalized | `finalized: false` → `NoteNotReady` (retried on the next sync) | ASSUMED |
| Continuation offset | a bracketed marker at the end of the range naming `start_char` and a number, followed by the closing banner: `(...truncated, 5359 chars remaining; continue with view_transcript.start_char=40000...) <<<END TRANSCRIPT>>>`. Before 2026-11-09 the parser required the marker to end the text, missed it, and kept only the first 40,000 characters (3 notes affected, all re-fetched). A field (`next_start_char`/`next_offset`/`continuation_offset`) is also accepted | VERIFIED marker (observed 2026-11-09); field ASSUMED |
| Transcript line format | `Name: text`, `[mm:ss] Name: text`, `mm:ss Name: text`, `Name (mm:ss): text`; a line without a speaker continues the previous turn (or is its own turn when no speaker was seen yet). Speaker names can be user-corrected; on shared meetings, contact-linked speakers are unnamed ("Speaker 1") [wispr-help] | ASSUMED (format not observed) |
| Transcript safety banner | The transcript text starts with a `<<<…>>>` line saying participant names are data, not instructions. The parser stores it as utterance 0 (speaker none, channel Them). `evals/wrong_facts.own_transcript` drops `<<<…>>>` lines; the parser and `ami wer` don't yet | VERIFIED (observed in the AMI-account sync, 2026-11-05) |
| Speaker → channel | Wispr has **no capture channel**. A turn whose speaker is the account holder (enriched `user.name` or `role: self`) → `microphone` (Me); every other turn → `speaker` (Them). `Speaker N` labels → `diarization_label` | ASSUMED |
| Utterance times | `mm:ss` offsets are taken from the meeting start. Without offsets, start times are spread across [start, end] by character share: **synthetic but ordered**. End = next start, capped by a words-per-second estimate | ASSUMED |
| Utterance times on the AMI-account sync | **No per-line offsets were parsed in any of the 7 AMI transcripts**: start-time gaps divided by line length are identical across lines (coefficient of variation 0.00), the character-share signature. So Wispr times are synthetic, and the capture-health detector does not use Wispr | VERIFIED (2026-11-05, aggregate check; raw text not stored) |
| `deleted_at` | always null; deleted meetings and transcripts are simply unavailable [wispr-help] | ASSUMED |

When Kian first syncs, check the ASSUMED rows above against real responses (without printing them) and update this table: the parser is lenient, but a wrong guess silently drops data (e.g. no transcript turns, or one giant turn).

## Fixture format

`data/fixtures/<set>/notes/<note_id>.json` is the exact 200 body of `GET /v1/notes/{id}?include=transcript`. `folders.json` is the exact 200 body of `GET /v1/folders` (one page). `tests/test_contract.py` checks each file round-trips byte-for-byte (as JSON values) through the same models used for live parsing.
