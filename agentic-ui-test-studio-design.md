# Agentic UI Test Studio

A local-first web UI that wraps the existing `agent_recorder.py` + `script_generator.py` pipeline into a chat-driven product — runs on `localhost:7001`, single-user, loopback-only.

---

## Architecture Overview

```
prd_agent/
├── studio/                        ← NEW: the product
│   ├── app.py                     ← Flask backend (threaded=True, 127.0.0.1 only)
│   ├── api/
│   │   ├── chat.py                ← SSE streaming chat endpoint
│   │   ├── scripts.py             ← CRUD + versioning for generated scripts
│   │   └── testdata.py            ← CRUD + AI-fill for test datasets
│   ├── static/
│   │   ├── index.html             ← Single-page app shell
│   │   ├── chat.js                ← Chat panel + SSE consumer
│   │   ├── editor.js              ← Script viewer/editor (CodeMirror 6 via CDN)
│   │   └── testdata.js            ← Test data table editor
│   └── store/
│       ├── sessions/              ← Per-session dirs: session.json, trace.json,
│       │                              script_v1.py … script_vN.py
│       └── testdata/              ← Named dataset JSON files + schemas
├── agent/recorder.py              ← UNCHANGED (record_scenario)
├── script_generator.py            ← UNCHANGED (generate_script)
└── generated_scripts/             ← UNCHANGED trace output dir
```

**Port:** `127.0.0.1:7001` — loopback-only, never `0.0.0.0` (security: studio executes subprocesses).

---

## Core Features

### 1. Chat Interface

**Flow per session:**

```
User types task
  → POST /api/sessions  (creates session dir, returns session_id)
  → GET  /api/sessions/<id>/stream  (SSE opened by browser)
  → agent thread starts: new event loop per thread, record_scenario() runs
  → SSE events: {type: "progress", text: "..."} streamed during recording
  → on completion: generate_script() runs, script written to store
  → SSE event: {type: "done", script: "..."} — script appears in right panel

User gives feedback via chat
  → system asks: [Re-record] or [Patch]
  → Re-record: updated task (original + feedback) sent to new agent run
  → Patch: LLM returns FULL revised script (not a diff); studio writes as new version
  → old version preserved for revert
```

**Error states are explicit SSE events:**
```
{type: "error", message: "Agent failed: <reason>", partial_trace_saved: true/false}
```
The UI surfaces errors in the chat panel. Partial traces (if any actions were recorded before failure) are saved and shown.

**Chat panel left, script panel right** — side-by-side layout.

Each session stored under `store/sessions/<id>/`:

- `session.json` — task history, message log, current version pointer, status
- `trace.json` — latest agent trace (overwritten on re-record)
- `script_v1.py`, `script_v2.py`, … — one file per version; never deleted

### 2. Script Management

- **Script viewer** — syntax-highlighted via CodeMirror 6 (CDN, no build step), read-only by default
- **Edit toggle** — unlocks inline editing; save writes a new version
- **Version history** — dropdown listing `v1 … vN` with timestamps; selecting one loads it; "Revert to this" writes it as a new version (non-destructive)
- **Run button** — checks target app is reachable first (HEAD request to configured URL), then runs `pytest <script_path> -v` as a subprocess; stdout/stderr streamed via SSE into a collapsible output panel
- **Export** — copies current script to `tests/generated/<scenario>.py`

### 3. Test Data Manager

Separate tab. Two panels:

#### Dataset Library

- Lists all datasets in `store/testdata/`
- Each dataset has: `name`, `schema_ref` (optional), `created_at`, key count
- Actions: Create, Rename, Duplicate, Delete

#### Dataset Editor

- Table view: key | value | type columns, all cells editable inline
- **Schema** — each dataset can declare expected keys + types (loaded from `store/testdata/<name>.schema.json`). New datasets created from an existing schema pre-populate all keys with empty values. AI Fill uses the schema to constrain generated values.
- **"AI Fill" button** — user types a description → LLM is given the schema (key names + types + any enum values) and the description → returns a JSON object matching the schema exactly → pre-populated for review before saving. No schema = LLM infers keys from description only (clearly labelled as "schema-free mode").
- **Dataset reference in chat** — user can type `@dataset:<name>` in the chat input; the system injects the dataset values as context into the task sent to the agent (e.g., "fill borrower fields with: {name: 张三, id: ...}")

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve SPA shell |
| GET | `/api/sessions` | List sessions (name, status, created_at, version_count) |
| POST | `/api/sessions` | Create session, returns `{session_id}` |
| GET | `/api/sessions/<id>/stream` | SSE: progress, error, done events |
| POST | `/api/sessions/<id>/feedback` | `{message, action: "rerecord"|"patch"}` |
| GET | `/api/sessions/<id>/script` | `{version, versions[], content}` |
| PUT | `/api/sessions/<id>/script` | Manual save → writes new version |
| POST | `/api/sessions/<id>/run` | Run pytest; streams output via SSE |
| GET | `/api/sessions/<id>/versions` | List all script versions |
| GET | `/api/sessions/<id>/versions/<n>` | Get specific version content |
| GET | `/api/testdata` | List datasets |
| GET | `/api/testdata/<name>` | Get dataset + schema |
| PUT | `/api/testdata/<name>` | Save dataset |
| POST | `/api/testdata` | Create dataset |
| DELETE | `/api/testdata/<name>` | Delete dataset |
| POST | `/api/testdata/<name>/ai-fill` | `{description}` → LLM-generated values |

---

## Key Technical Decisions

### Async-in-Flask threading

The agent (`record_scenario`) is `async`. Flask is sync. The bridge:

```python
import asyncio, threading

def run_agent_in_thread(task, session_id, progress_queue):
    loop = asyncio.new_event_loop()   # new loop per thread, no shared state
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(record_scenario(task, session_id, progress_queue))
    finally:
        loop.close()

thread = threading.Thread(target=run_agent_in_thread, args=(...), daemon=True)
thread.start()
```

`asyncio.run()` is **not used** — it creates/destroys a loop and conflicts if any outer loop exists. A `Queue` passes progress strings from the agent thread to the SSE generator on the Flask thread.

Flask started with `app.run(threaded=True)` to handle concurrent SSE + API requests.

### SSE streaming

```python
def stream(session_id):
    q = session_queues[session_id]
    def generate():
        while True:
            msg = q.get()           # blocks until progress or sentinel
            if msg is None: break   # sentinel = agent done or failed
            yield f"data: {json.dumps(msg)}\n\n"
    return Response(stream_with_context(generate()), mimetype="text/event-stream")
```

### LLM Patch strategy

LLM is given the full current script + the user's feedback and instructed to **return the complete revised script** — not a diff. Rationale: LLMs do not produce reliable unified diffs (line numbers drift, context lines mismatch). The studio then:
1. Writes the returned script as `script_vN+1.py`
2. Renders a simple line-count diff summary in the chat ("3 lines changed")

### File concurrency

Single-user tool. Each session has its own directory; writes use `tempfile` + atomic rename (`os.replace`) to prevent partial-write corruption. No cross-session locking needed.

### Target app readiness

Before running pytest, the studio performs a `HEAD` (or `GET /health`) request to the URL embedded in the script's `page.goto()` call. If unreachable, the Run button shows a warning: _"Target app at http://localhost:5001 is not responding — start it before running."_

### Browser headless mode

Default: **headless=True** when launched from studio (no unexpected Chromium window). A **[Show browser]** toggle in the chat UI passes `headless=False` — useful for debugging a failing re-record.

### Security scope

- Bound to `127.0.0.1` only — never `0.0.0.0`
- No auth (loopback-only is the security boundary)
- Subprocess commands are allowlisted: only `pytest <path>` with path validated to be inside the project directory

---

## UI Layout

```
┌──────────────────────────────────────────────────────────┐
│  UI Test Studio    [Sessions ▾]  [Test Data]  [⚙ Config] │
├──────────────────────┬───────────────────────────────────┤
│  CHAT                │  SCRIPT  v3 ▾  [Edit] [Run ▶] [↓] │
│                      │  ┌─────────────────────────────┐  │
│  > Transfer $100...  │  │ def test_bank_transfer(..): │  │
│  ⏳ Recording...     │  │   page.goto(...)            │  │
│  ⏳ Generating...    │  │   page.get_by_test_id(…)    │  │
│  ✅ Script ready     │  └─────────────────────────────┘  │
│                      │                                   │
│  > login step wrong  │  ── Run output ────────────────   │
│                      │  ✅ PASSED test_bank_transfer      │
│  [Re-record] [Patch] │                                   │
│  [Show browser] □    │                                   │
│  ____________________│                                   │
│  > @dataset:std_bor… │                                   │
└──────────────────────┴───────────────────────────────────┘
```

---

## Build Order (spike-first)

**Step 0 — Integration spike (before any UI)**
Validate the async-in-Flask-SSE bridge in isolation: minimal `app.py` with one POST + one SSE endpoint that runs a fake 3-step async task. Confirm SSE events arrive in the browser. Only proceed once this works.

1. `studio/app.py` — Flask skeleton, `127.0.0.1:7001`, threaded mode, serve static
2. `api/chat.py` — session store (JSON files), POST create, SSE stream, agent thread
3. `static/index.html + chat.js` — chat panel, SSE consumer, progress display
4. `static/editor.js` — script panel with CodeMirror 6, version dropdown
5. Re-record feedback path — simpler (reuse step 2's agent thread)
6. Patch feedback path — LLM full-script rewrite, version bump
7. Run button — subprocess + SSE output, target URL readiness check
8. `api/testdata.py` + `static/testdata.js` — dataset CRUD + table editor
9. AI Fill — schema-aware LLM generation
10. `@dataset:` reference parsing in chat input

---

## What stays unchanged

- `agent/recorder.py` — `record_scenario()` called as-is
- `script_generator.py` — `generate_script()` called as-is
- `generated_scripts/` — traces still saved here
- `tests/generated/` — scripts exported here on demand
- The loan calculator app at port `5001`
