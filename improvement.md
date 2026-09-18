# Improvement Plan — AI Meeting Intelligence Platform

> A detailed review of the current codebase (`main` @ `145cbf0`) with concrete bugs,
> architectural gaps, and a prioritised feature roadmap.
>
> Every item in **Part 1** was verified by running the code, not inferred from reading it.
> Line references are `file:line` and point at the current source.

---

## 0. Where the project stands today

| Area | State | Verdict |
|---|---|---|
| Domain modelling (`models.py`) | Clean Pydantic models, good typing | Strong |
| Pipeline (`pipeline.py`) | Clean → chunk → map → reduce → extract → graph | Good shape, no resilience |
| Providers (`providers/`) | ABC + 3 implementations, lazy clients | Good abstraction, no retries/streaming |
| Persistence (`database.py`) | Normalised SQLite schema, WAL, cascades | Good schema, **search is broken** |
| API (`api/`) | FastAPI, 7 endpoints | Works, but synchronous and unsecured |
| Frontend (`frontend/`) | React 18 + Vite + TS + 3d-force-graph | Nice 3D graph, thin state layer |
| Streamlit app (`app.py`) | 640-line parallel UI | Duplicated surface, maintenance debt |
| Tests | 71 tests, 5 files, backend only | No API tests, no provider tests, no CI |
| Ops | Dockerfile + compose | Dev-mode compose, no CI, repo bloat |

**Headline problems, in order of impact:**

1. **Search is 100% broken** — every non-empty query raises a SQLite error (verified).
2. **`frontend/node_modules` is committed** — 6,501 of 6,558 tracked files; 27 MB `.git`.
3. **Chunking math is wrong** — asking for 1000-token chunks produces ~1563-token chunks.
4. **No resilience** — one transient 429 from the LLM silently degrades a whole meeting.
5. **No auth, wildcard CORS, error details leaked** — cannot be deployed publicly as-is.
6. **Two frontends** (React + Streamlit) implementing the same product.

---

# PART 1 — Verified bugs (fix these first)

## 1.1 🔴 CRITICAL: Full-text search is completely broken

**`database.py:338`**

```python
sql = f"SELECT DISTINCT meeting_id FROM {table} WHERE {alias}.{column} LIKE ?"
```

The table is queried with no alias, but the `WHERE` clause references one (`ai.task`,
`dec.decision`, `dl.description`). SQLite rejects it.

Reproduced live:

```
SEARCH ERROR: OperationalError no such column: ai.task
```

Because the exception is uncaught, `GET /api/meetings?search=anything` returns **500**, and the
search box in both the React and Streamlit UIs is dead for every query. `test_search_by_task`
in `tests/test_database.py` covers this path — meaning **the test suite is currently red** (or
was never run since the regression landed).

**Minimal fix** — drop the phantom alias:

```python
sql = f"SELECT DISTINCT meeting_id FROM {table} WHERE {column} LIKE ?"
if table == "meetings":
    sql = f"SELECT id FROM meetings WHERE {column} LIKE ?"
```

**Proper fix** — replace the 6-query `LIKE` fan-out with an FTS5 virtual table (see §3.2).
`LIKE '%term%'` cannot use an index and scans every row of every table on every keystroke
(the React search box fires a request per character — `MeetingHistory.tsx:52`).

---

## 1.2 🔴 Re-processing a meeting duplicates its children

**`database.py:154` vs `database.py:176-188`**

The parent row uses `INSERT OR REPLACE INTO meetings`, but action items, deadlines and decisions
use plain `INSERT`. Saving the same `meeting.id` twice leaves one meeting row with **two copies**
of every action item, deadline and decision.

Today `process_transcript` always mints a fresh UUID so this is latent — but it fires the moment
you add re-processing, an edit endpoint, or an import. Fix:

```python
conn.execute("DELETE FROM action_items WHERE meeting_id = ?", (meeting.id,))
conn.execute("DELETE FROM deadlines   WHERE meeting_id = ?", (meeting.id,))
conn.execute("DELETE FROM decisions   WHERE meeting_id = ?", (meeting.id,))
# ... then the INSERT loops
```

---

## 1.3 🟠 Chunking produces chunks 56% larger than requested

**`utils.py:113-128`**

```python
tokens_per_word = _CHARS_PER_TOKEN / 5.0          # 0.8
word_budget = max(1, int(chunk_token_size / tokens_per_word))   # 1000 / 0.8 = 1250 words
```

The ratio is inverted. A "word" of ~5 characters is ~1.25 tokens, not 0.8. Measured:

```
chunk_transcript(text, chunk_size=1000)  ->  est tokens per chunk: [1563, 1563, 1563]
```

Consequences: context-window overflows on providers with tight limits, higher per-call cost,
and the user's chunk-size setting means nothing.

Two bugs compound it:

```python
chunk_size = chunk_size or settings.default_chunk_size   # utils.py:187
overlap    = overlap    or settings.default_chunk_overlap # utils.py:188
```

`overlap=0` is falsy, so **it is silently replaced by 200** — you cannot disable overlap.
Verified: `chunk_size=1000, overlap=0` and `overlap=200` both yield 10 chunks.

**Fix:**

```python
chunk_size = settings.default_chunk_size if chunk_size is None else chunk_size
overlap    = settings.default_chunk_overlap if overlap is None else overlap
```

**Better fix:** delete the heuristic entirely and use real tokenisation (`tiktoken`, or the
provider's own `count_tokens`). `estimate_tokens`'s "4 chars ≈ 1 token" is also wrong for
non-English text and for speaker-labelled transcripts.

---

## 1.4 🟠 The chunk cache is unsafe and unbounded

**`pipeline.py:71` + `utils.py:258-268`**

```python
def _hash_chunk(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()
```

The cache key is the chunk text **only**. It ignores the provider, the model, the temperature,
and the prompt version. So:

- Process a transcript with Groq, then re-process it with Gemini → you silently get the **Groq**
  summaries back, while the meeting is recorded as `provider="gemini"`.
- Edit `prompts.chunk_summary` → cached results from the old prompt keep being served.

The store is also a plain module-level `dict` that is never evicted (`utils.py:258`) — a
long-running API process leaks memory proportional to everything it has ever summarised.
`clear_chunk_cache` is imported into `pipeline.py:40` and never called.

**Fix:** key on `sha256(f"{provider}:{model}:{temperature}:{PROMPT_VERSION}:{text}")`, bound the
cache (`functools.lru_cache` or `cachetools.LRUCache(maxsize=512)`), and persist it in SQLite so
it survives restarts and is shared across workers.

---

## 1.5 🟠 Connection retry helper can raise a confusing `RuntimeError`

**`database.py:40-55`**

`get_connection` is a `@contextmanager` that `yield`s inside a `for`-loop with a retry `continue`.
If the *caller's* body raises `sqlite3.OperationalError("database is locked")`, the exception is
thrown into the generator at the `yield`, caught by the `except`, and the loop `continue`s to a
second `yield` — which Python rejects with
`RuntimeError: generator didn't stop after throw()`, masking the real error.

Retry logic belongs around the *operation*, not around the `yield`:

```python
def with_retry(fn, attempts=3, delay=0.05):
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))
```

Also: `get_connection` and `get_transaction` duplicate the same four lines of setup and both
open a **fresh connection per call** — `get_full_meeting` opens 2 connections and runs 7 queries
for a single meeting. Use one module-level connection factory with `check_same_thread=False`
plus a `threading.local` pool, and set `PRAGMA synchronous=NORMAL` alongside WAL.

---

## 1.6 🟡 Directed relationships are stored in an undirected graph

**`graph.py:55`**

```python
G = nx.Graph()
```

But `build_agraph_config()` sets `directed=True` (`graph.py:180`) and the relationship
vocabulary is inherently directional (`owns`, `assigns`, `depends_on`, `due_on`).
With `nx.Graph`, `A owns B` and `B owns A` collapse to one edge, and any future centrality /
dependency-chain analysis will be wrong. Use `nx.MultiDiGraph` (multi- because two entities can
have more than one relationship).

Related: `build_graph` silently **drops** relationships whose endpoints are missing
(`graph.py:70`). That hides a real LLM failure mode — hallucinated entity ids. Log the dropped
edges and surface the count in `graph_statistics`.

---

## 1.7 🟡 `GraphData` re-parses its JSON on every property access

**`models.py:58-72`**

`entities` and `relationships` each call `json.loads` on every access. `build_agraph_nodes_edges`
touches them repeatedly, and `graph_statistics` calls `build_graph` which touches them again.
Use `functools.cached_property`, or parse once in a validator and keep the structured form as
the source of truth with `graph_json` as a serialisation detail.

---

## 1.8 🟡 Frontend: missing React `key`, fake progress bar

- **`frontend/src/components/Layout.tsx:22`** — the `NAV.map` produces `<NavLink>` elements with
  no `key` prop. React logs a warning on every render and reconciliation is degraded.
- **`frontend/src/pages/NewMeeting.tsx:34,46`** — `setProgress(10)` then `setProgress(100)`.
  The progress bar is decorative; on a long transcript it sits at 10% for minutes. Either remove
  it or wire it to real pipeline progress (see §5.1).
- **`frontend/src/api/client.ts:60`** — `checkHealth` calls `/config`, which is not a health
  endpoint. Add a real `GET /api/health` that checks the DB and reports provider readiness.
- **`MeetingHistory.tsx:52`** — search fires an API request per keystroke, with no debounce and no
  request cancellation, so responses can land out of order. Add a 300 ms debounce and an
  `AbortController`.
- **`MeetingHistory.tsx:24`** — `useEffect(() => { fetchList() }, [search])` omits `fetchList`
  from the dependency array; it works only because the closure is recreated each render.
- Errors are swallowed with `.catch(console.error)` in Dashboard, KnowledgeGraph and Settings —
  the user sees a permanently empty page with no explanation.

---

## 1.9 🟡 `requirements.txt` cannot run the application

`requirements.txt` lists `streamlit` but **omits `fastapi` and `uvicorn`** — the actual runtime.
It has drifted from `pyproject.toml`. Either delete it and document `uv sync`, or generate it
with `uv export --no-hashes -o requirements.txt` in CI so it can never drift.

Similarly, **`pyproject.toml`**:

```toml
[tool.setuptools.packages.find]
include = ["meeting_intelligence*"]
```

No such package exists. `pip install .` in the Dockerfile therefore installs the dependencies but
**none of the application code** — it only works because `COPY . .` puts the modules on the
working directory. Either restructure into a real `src/meeting_intelligence/` package
(recommended — see §2.1) or drop the build-system config and stop pretending it's installable.

---

## 1.10 🔴 Quoted values in `.env` break every Docker run *(found while fixing Phase 0)*

`.env.example` shipped quoted values (`DEFAULT_PROVIDER="groq"`). `python-dotenv` strips the
quotes, so running locally works — but Docker's `--env-file` and compose's `env_file:` pass them
through **verbatim**. The container therefore received the 6-character string `"groq"` and died on
startup:

```
pydantic_core.ValidationError: 1 validation error for Settings
DEFAULT_PROVIDER
  Input should be 'gemini', 'groq' or 'openrouter' [input_value='"groq"']
```

The documented `docker-compose up -d` path had never worked for anyone who copied
`.env.example` as instructed. Fixed two ways: `.env.example` no longer quotes values, and
`Settings` now strips surrounding quotes in a `mode="before"` validator so existing `.env`
files keep working.

---

# PART 2 — Architecture & code quality

## 2.1 Restructure into a proper package

Right now 11 modules sit at the repo root and import each other with bare `import config`.
This breaks if the project is ever installed, makes `sys.path` fragile (`validate.py:71` has to
patch it manually), and lets tests silently import the wrong thing.

```
src/meeting_intelligence/
    __init__.py
    config.py  logger.py  models.py  utils.py
    db/          repository.py  schema.py  migrations/
    pipeline/    orchestrator.py  chunking.py  extraction.py  graph.py
    providers/   base.py  gemini.py  groq.py  openrouter.py  registry.py
    api/         main.py  routes/  schemas.py  deps.py
tests/
frontend/
```

Then `pip install -e .` works, imports become absolute and unambiguous, and the Dockerfile
becomes a real two-stage build.

## 2.2 Retire the Streamlit app (or demote it honestly)

`app.py` is 640 lines reimplementing every page the React app already has, against the same
functions. Two UIs means every feature is built twice and one of them rots. The README already
calls it an "optional fallback".

**Recommendation:** delete `app.py`, `graph.py`'s `build_agraph_*` helpers, the `streamlit`
optional-dependency group, and the empty `.streamlit/` directory. If you want to keep a
zero-install demo, make it a thin Streamlit client that calls the HTTP API rather than the
internal modules — then there's exactly one implementation of the business logic.

This alone removes ~700 lines and the `streamlit-agraph` dependency.

## 2.3 The pipeline should be a resumable state machine, not a straight-line function

`process_transcript` (`pipeline.py:91-286`) is a 200-line function that does nine things and
persists only at the very end. Failure modes today:

- A 429 on chunk 7 of 20 → that chunk's content is **permanently lost** from the summary, with
  only a log line. The user sees a plausible but incomplete summary and never knows.
- A crash at step 8 → all the LLM work done in steps 4-7 is thrown away and must be paid for again.
- No way to re-run just the graph step against an existing summary.

Model it as discrete, persisted stages:

```python
class Stage(StrEnum):
    CLEANED = "cleaned"; CHUNKED = "chunked"; SUMMARISED = "summarised"
    MERGED = "merged"; EXTRACTED = "extracted"; GRAPHED = "graphed"; DONE = "done"
```

Persist a `processing_runs` row after each stage. Then you get: resume-after-failure, per-stage
retry, a real progress feed for the UI, and per-stage cost accounting for free.

Also surface partial failure in the data model — add `chunk_failures: int` and
`degraded: bool` to `Meeting` so the UI can show "⚠️ 3 of 20 segments failed to summarise"
instead of quietly presenting an incomplete summary as complete.

## 2.4 Providers need retries, timeouts and model selection

`providers/base_provider.py` is a good abstraction, but every implementation has the same gaps:

- **No retry/backoff.** A single 429 or 503 fails the call. Add `tenacity` with exponential
  backoff + jitter, retrying on 429/500/502/503/504 and honouring `Retry-After`.
- **No timeout** on Gemini and Groq (OpenRouter has one at `openrouter_provider.py:66`).
- **Hardcoded models.** `gemini-2.0-flash`, `llama-3.3-70b-versatile`, `openai/gpt-4o-mini` are
  baked into `__init__` defaults and the registry calls `cls()` with no arguments
  (`pipeline.py:68`), so **the model can never be chosen** from the API or the UI.
  Add `model` to `ProcessRequest`, thread it through `get_provider(name, model=...)`, and expose
  a `GET /api/providers` endpoint listing available models per provider.
- **No cost tracking.** `ProviderResponse` already carries `input_tokens`/`output_tokens` and
  they are thrown away. Add a price table, compute per-meeting cost, store it, and show it.
- **No async.** Everything is blocking `requests`-style I/O. `async def generate` + `httpx.AsyncClient`
  unlocks §4.1.
- **`except Exception` → `RuntimeError`** in all three providers flattens auth errors, rate limits
  and network errors into one type, so callers cannot react differently. Define
  `ProviderAuthError`, `ProviderRateLimitError`, `ProviderTimeoutError`, `ProviderError`.
- **No fallback chain.** If Groq is down, the run dies. Add
  `provider_chain = ["groq", "gemini", "openrouter"]` with automatic failover.

## 2.5 Structured output instead of JSON-repair-by-prompt

`_safe_json_parse` (`pipeline.py:75-88`) does brace-matching on LLM output, and `prompts.py:120`
has a `repair_json` helper that is **never called**. This is a whole class of bug you can delete:

- Gemini: pass `response_schema=` alongside `response_mime_type="application/json"`.
- Groq/OpenRouter: use tool/function calling, or JSON-schema mode where supported.
- Validate with the Pydantic model itself and retry once with the validation error appended to
  the prompt.

Also note `ActionItem(**item)` (`pipeline.py:232`) will raise `TypeError` if the LLM emits an
unexpected key, and that exception is **not** caught by the surrounding handler — one stray field
kills the request. Use `ActionItem.model_validate(item)` inside a per-item try/except so one bad
item doesn't discard the other nineteen.

## 2.6 Prompt engineering gaps (`prompts.py`)

- **No timestamps or speaker attribution requested.** Action items lose "who said this and when",
  which is the single most useful piece of provenance in a meeting tool.
- **No date grounding.** `deadlines` asks for `YYYY-MM-DD or relative text` but never tells the
  model today's date, so "next Thursday" can never be resolved. Inject the meeting date into the
  system prompt and demand absolute dates.
- **No few-shot examples.** A single worked example per extraction task typically cuts schema
  violations sharply.
- **No prompt versioning.** Add `PROMPT_VERSION = "2026-09-01"`, store it on the meeting, and
  include it in the cache key (§1.4) so you can tell which prompts produced which output.
- **The merge step doesn't scale.** `merge_summaries` concatenates *every* chunk summary into one
  prompt (`prompts.py:38`). A 3-hour transcript overflows the context window at the merge step.
  Make the reduce recursive: merge in batches of ~5 until one summary remains.
- **No language handling.** Nothing detects or preserves the transcript language; a French
  transcript gets an English summary.

## 2.7 Configuration and logging

- **`config.py:7`** imports `os` and never uses it.
- **Settings are load-once at import** (`config.py:61`). The Settings page says "restart the app
  for changes to take effect" — use `@lru_cache` + a FastAPI dependency so config is reloadable.
- **No validation bounds.** `default_temperature`, `default_chunk_size` and `default_chunk_overlap`
  accept any value. Add `Field(ge=0.0, le=2.0)`, `Field(ge=100, le=100_000)`, and a validator
  asserting `overlap < chunk_size`.
- **`logger.py:68`** sets every logger to `DEBUG` and attaches handlers to the **root** logger,
  so third-party libraries' debug output floods `data/logs/app.log`. Make the level configurable
  via `LOG_LEVEL`, and don't touch the root logger from a library module.
- **No log rotation.** `FileHandler` (`logger.py:78`) grows without bound. Use
  `RotatingFileHandler(maxBytes=10_000_000, backupCount=5)`.
- **No request correlation.** Add a `request_id` / `meeting_id` contextvar so you can trace one
  meeting's processing through the log file.
- **API keys could leak into logs.** Provider errors are logged verbatim (`pipeline.py:183`);
  some SDKs include the request in the exception string. Add a redacting filter.

---

# PART 3 — Data layer

## 3.1 Participants should be a table, not a JSON blob

`meetings.participants` is a JSON array in a TEXT column (`database.py:86`). That means:

- You cannot query "all meetings Alice attended" without scanning and deserialising every row —
  `get_all_participants()` (`database.py:385`) literally loads every participants blob into Python.
- No entity resolution: "Alice", "Alice Chen" and "alice@corp.com" are three different people.
- No per-person view, which is the most obvious feature this product is missing (§6.3).

```sql
CREATE TABLE people (
    id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL,
    email TEXT, aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE meeting_participants (
    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    person_id  TEXT REFERENCES people(id),
    PRIMARY KEY (meeting_id, person_id)
);
```

## 3.2 Replace `LIKE` search with FTS5

Beyond the outright bug (§1.1), the design scans six tables with leading-wildcard `LIKE` on every
query. SQLite ships FTS5 — use it:

```sql
CREATE VIRTUAL TABLE meetings_fts USING fts5(
    meeting_id UNINDEXED, title, summary, transcript, tasks, decisions,
    tokenize = 'porter unicode61'
);
```

Populate via triggers on insert/update/delete. You get ranking (`bm25()`), snippet highlighting
(`snippet()`), prefix and phrase queries, and sub-millisecond lookups — and the search box can
finally show *why* a meeting matched.

## 3.3 Missing schema essentials

- **No migrations.** `init_db()` runs `CREATE TABLE IF NOT EXISTS` (`database.py:140`) — any
  schema change to an existing database is a manual `ALTER`. Add Alembic, or a simple
  `PRAGMA user_version` migration runner.
- **No `updated_at`**, so nothing can be sorted or synced by modification time.
- **Missing index on `meetings.created_at`**, which is the column both list queries actually
  `ORDER BY` (`database.py:231`). The existing `idx_meetings_date` indexes a different column.
- **No pagination.** `get_meeting_list()` returns *every* meeting with two correlated subqueries
  each. At 1,000 meetings the dashboard payload is megabytes. Add `limit`/`offset` (or keyset
  pagination on `created_at`) to the query, the endpoint and the UI.
- **Status columns are free text.** `action_items.status` and `.priority` accept anything the LLM
  emits. Add `CHECK (status IN ('open','in_progress','done','cancelled'))`.
- **Transcripts are stored twice** (raw + cleaned) in full, uncompressed. For long meetings
  consider `zlib`-compressing `raw_text`, or storing it on disk with a path in the DB.
- **No soft delete.** `delete_meeting` is irreversible with no confirmation on the API side and a
  single click in the UI (`MeetingHistory.tsx:74`). Add `deleted_at` and an undo window.

---

# PART 4 — Performance & cost

## 4.1 Process chunks concurrently (biggest single win)

**`pipeline.py:165`** — chunks are summarised in a serial `for` loop. A 20-chunk transcript at
~3 s/call is a **60-second** wall-clock request. With `asyncio.gather` and a semaphore of 5 it's
~12 s, a 5× improvement for a few lines of code:

```python
sem = asyncio.Semaphore(settings.max_concurrent_requests)
async def summarise(i, chunk):
    async with sem:
        return await provider.agenerate(...)
results = await asyncio.gather(*(summarise(i, c) for i, c in enumerate(chunks)),
                               return_exceptions=True)
```

Requires the async provider work in §2.4. Add a token-bucket rate limiter per provider so
concurrency doesn't just convert latency into 429s.

## 4.2 Make processing a background job

**`api/routes.py:85`** — `POST /api/process` does all the LLM work inline. A long transcript holds
an HTTP connection for minutes, will be killed by any reverse proxy's default timeout, and cannot
be polled or cancelled. The frontend's fake progress bar (§1.8) exists precisely because there is
nothing to report.

```
POST /api/process        -> 202 {"job_id": "..."}      (enqueue)
GET  /api/jobs/{job_id}  -> {"stage": "summarising", "progress": 0.45, "chunks_done": 9}
GET  /api/jobs/{job_id}/events  (SSE stream)
```

Start with FastAPI `BackgroundTasks` + a `jobs` table; graduate to Redis + ARQ/Celery if you need
multiple workers. This also unlocks batch upload (§6.1).

## 4.3 Smaller wins

- **`init_db()` at import time** (`api/routes.py:31`) — a side effect on module import makes
  the module untestable in isolation. Move it into a FastAPI `lifespan` handler.
- **Provider instances are constructed per call** (`pipeline.py:68` → `cls()`), so every pipeline
  stage builds a new SDK client and a new HTTPS connection pool. Cache instances per
  `(provider, model)`.
- **`get_full_meeting` runs 7 queries across 2 connections.** One query with `json_group_array`,
  or three queries on one connection, would do.
- **No response compression.** Add `GZipMiddleware` — meeting payloads embed full transcripts.
- **Frontend has no caching or dedupe.** Every navigation refetches everything. TanStack Query
  gives you caching, background refetch, retry and request dedupe in ~20 lines.
- **`frontend/dist/` is stale build output sitting in the working tree** and is not in any
  `.gitignore`. It is not currently tracked — keep it that way by ignoring it explicitly.

---

# PART 5 — Security, ops & developer experience

## 5.1 Security

| Issue | Location | Fix |
|---|---|---|
| **No authentication whatsoever** | all of `api/` | Anyone who reaches the port reads every transcript. Add API-key middleware at minimum; OIDC/JWT for multi-user. |
| **`allow_origins=["*"]` with `allow_credentials=True`** | `api/main.py:38-44` | Invalid per the CORS spec and browsers reject it. Use an explicit `ALLOWED_ORIGINS` env list. |
| **Internal error text returned to clients** | `api/main.py:34` | `f"Internal server error: {exc}"` can leak file paths, SQL and provider responses. Return a generic message + a correlation id; log the detail. |
| **Same leak on the process endpoint** | `api/routes.py:99,102,105` | `detail=str(exc)` — same treatment. |
| **No request size limit** | `POST /api/process` | A 500 MB paste will OOM the worker. Cap `text` with `Field(max_length=...)` and set a body-size limit at the proxy. |
| **No rate limiting** | all endpoints | Each request spends real money on LLM calls. Add `slowapi` or equivalent. |
| **Transcripts stored in plaintext** | `data/meetings.db` | Meeting transcripts are sensitive. Consider SQLCipher or application-level encryption of `raw_text`, and document the data-retention policy. |
| **No PII handling** | pipeline | Offer an opt-in redaction pass (emails, phone numbers, card numbers) before anything leaves the machine for a third-party LLM. |
| **`.env` sits next to the code** | repo root | It is correctly gitignored, but document key rotation and prefer a secrets manager in production. |
| **Container runs as root** | `Dockerfile` | Add a non-root `USER app`. |

## 5.2 Repository hygiene — **`frontend/node_modules` is committed**

```
total tracked files : 6558
node_modules        : 6501   (99.1%)
.git size           : 27 MB
```

There is no `frontend/.gitignore`, and the root `.gitignore` never mentions `node_modules`.
Every `npm install` produces a colossal diff, clones are slow, and code review is impossible.

```bash
printf 'node_modules/\ndist/\n.vite/\n*.local\n' > frontend/.gitignore
git rm -r --cached frontend/node_modules
git commit -m "chore: stop tracking node_modules"
# history is only 27 MB — optionally purge with git-filter-repo
```

Also add a **`.dockerignore`** (there is none). `COPY . .` currently ships `.venv/`,
`frontend/node_modules/`, `data/meetings.db`, `.git/` and `data/logs/` into the image.

## 5.3 CI/CD — there is none

No `.github/` directory. Add `.github/workflows/ci.yml`:

```yaml
- uv sync --all-extras && uv run pytest --cov --cov-fail-under=70
- uv run ruff check . && uv run ruff format --check .
- uv run mypy .                      # pyproject already sets strict = true
- cd frontend && npm ci && npm run build && npx tsc --noEmit
- docker build -t ami:ci .
```

Add `.pre-commit-config.yaml` (ruff, ruff-format, trailing whitespace, a
`detect-secrets`/`gitleaks` hook) so the `.env`-adjacent mistakes can't happen.

Note: `pyproject.toml` already configures ruff *and* `mypy strict`, but neither is enforced
anywhere and the code would not currently pass strict mypy (untyped `provider: object` in
`pipeline.py:129`, `Any` returns in `graph.py`).

## 5.4 Docker

The current setup is dev-only masquerading as deployable:

- **The frontend "service" is `node:20-alpine` running `npm install && npm run dev`**
  (`docker-compose.yml`). That's a dev server with HMR, re-installing dependencies on every
  container start, exposed on 5173.
- **`VITE_API_URL=http://api:8080`** is set as a runtime env var, but `vite.config.ts` reads it at
  **config-evaluation time** via `process.env`, and the browser — not the container — is what
  resolves `api:8080`. This proxy config cannot work as written in compose.
- **No healthchecks**, no `depends_on: condition: service_healthy`.
- **No multi-stage build.** Build the React app with `node:20 AS build`, then serve the static
  bundle from the FastAPI container (or nginx) so there is one origin and no CORS at all.
- **No resource limits, no restart backoff, no volume for logs.**

## 5.5 Testing

71 tests exist and the backend domain logic is decently covered, but:

- **Zero API tests.** No `TestClient` coverage of the 7 endpoints — status codes, 404s, validation
  errors, the 500 on search.
- **Zero provider tests.** No mocked-transport tests for response parsing, fence stripping, token
  accounting or error mapping.
- **Zero frontend tests.** No Vitest, no Testing Library, no Playwright. `npm run build` isn't run
  in CI either, so a TypeScript error can land on `main`.
- **`tests/conftest.py:26`** opens `meetings/sample_transcript.txt` with a **relative path**, so the
  suite only passes when run from the repo root. Use `Path(__file__).parent.parent`.
- **Tests hit the real database.** `database.py` reads `DB_PATH` from module-level config, so
  `tests/test_database.py` operates on `data/meetings.db` — the developer's real data. Inject the
  path as a fixture with `tmp_path`.
- **No golden/regression tests for the pipeline** against a recorded provider response
  (record with `pytest-recording`/VCR), no property-based tests for the chunker (`hypothesis`:
  "chunks always reconstruct the input", "no chunk exceeds chunk_size").
- **No coverage gate** — `pytest-cov` is a dependency but nothing enforces a threshold.
- `validate.py` is a hand-rolled smoke test that duplicates what CI should do; its
  `REQUIRED_FILES` list is a manual index that will rot. Convert it to a pytest module.

---

# PART 6 — New features, by tier

## Tier 1 — Quick wins (hours to a day each)

1. **Export** — Markdown / PDF / CSV / `.ics`. The single most requested capability for a meeting
   tool and there is nothing today. `GET /api/meetings/{id}/export?format=md|pdf|csv`,
   plus an "Export all action items" CSV across meetings.
2. **Edit extracted data.** Action items, deadlines and decisions are write-once — the LLM's
   output is final. Add `PATCH /api/meetings/{id}/action-items/{item_id}` and inline editing so a
   user can fix an owner, tick something done, or delete a hallucinated item. Without this, the
   extracted data is a read-only report rather than a working tool.
3. **Copy-to-clipboard / share link** for the summary.
4. **Meeting title & date editing** — the title is inferred by the LLM and often wrong.
5. **Tags/labels** on meetings, with filter-by-tag in history.
6. **Dark/light theme toggle** — the CSS is already fully tokenised in `index.css:9-22`; it is
   ~20 lines to add a `[data-theme="light"]` block and a switch.
7. **Empty/loading/error states** everywhere (today: `.catch(console.error)` and a blank page).
8. **Keyboard shortcuts** — `/` to focus search, `n` for new meeting, `Esc` to close detail.
9. **`GET /api/health`** with DB + provider readiness, wired to the frontend banner.
10. **Sample-transcript button** in the New Meeting page — one click to try the product.

## Tier 2 — Substantial features (days each)

11. **🎤 Audio/video upload with transcription.** The biggest gap: the product is called *Meeting*
    Intelligence but cannot ingest a meeting. Accept `.mp3/.m4a/.wav/.mp4`, transcribe via
    Groq Whisper (already an API you hold a key for — `whisper-large-v3`, very cheap and fast) or
    local `faster-whisper`. Add **speaker diarisation** (`pyannote.audio`) so speaker labels are
    real rather than regex-guessed from `Name:` prefixes (`utils.py:25`).
12. **💬 Chat with your meetings (RAG).** Embed chunks (`sentence-transformers` locally, or a
    provider embedding API), store vectors in `sqlite-vec` or Chroma, and answer
    "What did we decide about rate limiting?" with citations back to transcript positions.
    This is the feature that turns an archive into a product.
13. **🔗 Cross-meeting knowledge graph.** Today each meeting has an isolated graph
    (`graph_data` per meeting). Merge them: resolve entities across meetings (same person, same
    project, same recurring task), and render one organisational graph with a meeting filter.
    Combined with §3.1 this is a genuinely differentiating feature.
14. **👤 Person pages.** "Everything Alice owns, across all meetings" — open action items,
    decisions she drove, meetings attended, her workload trend.
15. **📋 Action-item workspace.** A cross-meeting Kanban (Open / In progress / Done) with owner
    and due-date filters, overdue highlighting, and a "my items" view.
16. **📅 Timeline / calendar view** of deadlines across all meetings, with `.ics` subscription.
17. **🔁 Recurring-meeting series.** Link meetings into a series, then show "what changed since
    last time", carried-over action items, and a rolling series summary.
18. **📊 Analytics dashboard.** The current dashboard is four counters (`Dashboard.tsx:28-31`).
    Add: meetings/week, action-item completion rate, average meeting length, talk-time
    distribution per participant, top topics over time, LLM spend per week.
19. **Batch processing.** Upload a folder of transcripts and process them as a queue with a
    progress table (needs §4.2).
20. **Diff two meetings** — decisions added/changed/reversed between two sessions.

## Tier 3 — Platform / flagship (weeks)

21. **Integrations.**
    - **Slack/Teams bot**: post the summary + action items to a channel after processing.
    - **Jira/Linear/Asana**: one-click "create issues from action items".
    - **Google Calendar / Outlook**: auto-attach the summary to the calendar event; auto-ingest
      recordings from Meet/Zoom.
    - **Email digest**: send each participant their own action items.
    - **Webhooks**: `meeting.processed`, `action_item.overdue`.
22. **Multi-tenancy & auth.** Users, workspaces, roles, per-meeting sharing and visibility. This is
    the prerequisite for any deployment beyond a single laptop.
23. **Real-time / live meetings.** Stream audio in, produce a rolling summary, flag action items as
    they are spoken.
24. **Local-model support (Ollama).** A `LocalProvider` against `llama3.1` / `qwen2.5` keeps
    sensitive transcripts entirely on-premises and costs nothing. The provider abstraction already
    makes this a ~100-line addition and it's a strong selling point for privacy-sensitive users.
25. **Multi-language.** Detect transcript language, localise prompts, optionally translate the
    summary.
26. **Sentiment & engagement analysis.** Tone over the meeting timeline, interruption counts,
    speaking-time balance — surfaces meeting-health insights nobody else is showing.
27. **Automatic topic modelling** across the archive, feeding both search facets and the graph.
28. **Meeting quality score** — did it have an agenda, were decisions recorded, did every action
    item get an owner and a date?

---

# PART 7 — Suggested roadmap

### Phase 0 — Stop the bleeding ✅ COMPLETE
- [x] Fix the search SQL bug (§1.1) and add a regression test
- [x] Untrack `node_modules`, add `frontend/.gitignore` and `.dockerignore` (§5.2)
- [x] Fix the chunking math and the `or`-default coercion (§1.3)
- [x] Fix the cache key to include provider/model/temperature/prompt version (§1.4)
- [x] Fix `INSERT` → delete-then-insert for meeting children (§1.2)
- [x] Lock down CORS, stop leaking exception text (§5.1)
- [x] Add `GET /api/health`; fix the missing React `key` (§1.8)
- [x] Get `pytest` green and add CI (§5.3)
- [x] **Bonus:** fix quoted `.env` values breaking every Docker run (§1.10, found during Phase 0)

**Result:** 20 failing tests → **118 passing, 3 skipped**; coverage 76%; `ruff check` clean;
tracked files 6,558 → 57; the Docker container now starts and serves `/api/health`.
See §9 for the full change log.

### Phase 1 — Make it robust ✅ COMPLETE
- [x] Retries + timeouts + typed provider errors + fallback chain (§2.4)
- [x] Async providers and concurrent chunk summarisation (§4.1)
- [x] Background jobs with real progress via SSE (§4.2)
- [x] Structured/schema-constrained output; delete the JSON scraping (§2.5)
- [x] Surface partial failures in the UI (§2.3)
- [x] FTS5 search + pagination (§3.2, §3.3)
- [x] API tests + provider tests + coverage gate (§5.5)
- [x] **Bonus:** schema migrations (§3.3), model selection (§2.4),
      stale model defaults, participant deduplication

**Result:** 118 → **173 tests**, coverage 76% → **81%**. Chunk summarisation is
~1.6× faster at the default concurrency of 5. Verified end to end against the live
Gemini API. See §10 for the full change log.

### Phase 2 — Make it a product (2–3 weeks)
- [ ] Editable action items / deadlines / decisions (§6 T1-2)
- [ ] Export: Markdown, PDF, CSV, ICS (§6 T1-1)
- [ ] People table + person pages (§3.1, §6 T2-14)
- [ ] Action-item workspace and deadline timeline (§6 T2-15, T2-16)
- [ ] Retire the Streamlit app; single React frontend (§2.2)
- [ ] Repackage into `src/meeting_intelligence/`; multi-stage Docker (§2.1, §5.4)

### Phase 3 — Differentiate (1–2 months)
- [ ] Audio upload + Whisper transcription + diarisation (§6 T2-11)
- [ ] RAG chat over the meeting archive (§6 T2-12)
- [ ] Cross-meeting knowledge graph with entity resolution (§6 T2-13)
- [ ] Ollama / local-model provider (§6 T3-24)
- [ ] Auth, multi-tenancy, sharing (§6 T3-22)
- [ ] Slack + Jira integrations (§6 T3-21)

---

# PART 8 — Quick reference: every finding by file

| File | Line(s) | Finding |
|---|---|---|
| `database.py` | 338 | 🔴 Phantom table alias breaks all search |
| `database.py` | 154,176-188 | 🔴 `INSERT OR REPLACE` parent + plain `INSERT` children → duplicates |
| `database.py` | 40-55 | 🟠 Retry `continue` after `yield` → `RuntimeError` |
| `database.py` | 86,385 | 🟠 Participants as JSON blob; full scan to list them |
| `database.py` | 140 | 🟠 No migrations; schema changes need manual `ALTER` |
| `database.py` | 222-246 | 🟠 No pagination; 2 correlated subqueries per row |
| `database.py` | 132-136 | 🟡 Index on `date`, but queries `ORDER BY created_at` |
| `utils.py` | 113-128 | 🟠 Inverted tokens-per-word ratio; chunks 56% oversized |
| `utils.py` | 187-188 | 🟠 `or`-defaults make `chunk_size=0` / `overlap=0` impossible |
| `utils.py` | 258-268 | 🟠 Unbounded module-global cache; `clear_chunk_cache` never called |
| `utils.py` | 25 | 🟡 Speaker regex only matches `Firstname[ Lastname]:` — misses most real formats |
| `pipeline.py` | 71 | 🟠 Cache key ignores provider/model/temperature/prompt version |
| `pipeline.py` | 91-286 | 🟠 200-line function, no resumability, persists only at the end |
| `pipeline.py` | 165 | 🟠 Serial chunk loop — 5× slower than necessary |
| `pipeline.py` | 231-239 | 🟠 `Model(**item)` raises uncaught `TypeError` on unexpected keys |
| `pipeline.py` | 68,129 | 🟡 New provider instance per call; typed as bare `object` |
| `providers/*` | all | 🟠 No retries, no timeouts (Gemini/Groq), hardcoded models, `except Exception` |
| `prompts.py` | 38 | 🟠 Non-recursive merge overflows context on long meetings |
| `prompts.py` | 66-68 | 🟠 Asks for dates without ever grounding "today" |
| `prompts.py` | 120 | 🟡 `repair_json` written but never called |
| `graph.py` | 55 | 🟡 `nx.Graph` loses direction; config claims `directed=True` |
| `graph.py` | 70 | 🟡 Silently drops edges with unknown endpoints |
| `models.py` | 58-72 | 🟡 JSON re-parsed on every property access |
| `api/main.py` | 34 | 🔴 Internal exception text returned to clients |
| `api/main.py` | 38-44 | 🔴 `allow_origins=["*"]` + `allow_credentials=True` |
| `api/routes.py` | 31 | 🟠 `init_db()` side effect at import time |
| `api/routes.py` | 85 | 🟠 Synchronous long-running endpoint; no job/progress model |
| `api/routes.py` | — | 🔴 No auth, no rate limit, no request-size cap on any endpoint |
| `api/schemas.py` | 6-12 | 🟡 No field constraints on `text`, `temperature`, `chunk_size` |
| `config.py` | 7 | 🟡 Unused `os` import |
| `config.py` | 29-39 | 🟡 No bounds validation on any setting |
| `logger.py` | 68,83-91 | 🟠 Forces `DEBUG` on root logger; no rotation; no redaction |
| `Layout.tsx` | 22 | 🟡 Missing React `key` |
| `NewMeeting.tsx` | 34,46 | 🟡 Fake progress bar |
| `MeetingHistory.tsx` | 52 | 🟠 Request per keystroke, no debounce, no cancellation |
| `client.ts` | 60 | 🟡 `checkHealth` points at `/config` |
| `Dashboard/Settings/KnowledgeGraph.tsx` | — | 🟠 Errors swallowed into `console.error` |
| `pyproject.toml` | 41-42 | 🟠 `packages.find` names a package that doesn't exist |
| `requirements.txt` | — | 🟠 Missing `fastapi`/`uvicorn`; drifted from pyproject |
| `Dockerfile` | — | 🟠 Single stage, root user, no `.dockerignore`, no healthcheck |
| `docker-compose.yml` | — | 🟠 Dev server as "production" frontend; `VITE_API_URL` proxy can't work |
| `tests/conftest.py` | 26 | 🟠 Relative path; tests run against the real `data/meetings.db` |
| repo | — | 🔴 6,501 tracked `node_modules` files; no `frontend/.gitignore` |
| repo | — | 🟠 No `.github/`, no CI, no pre-commit, ruff/mypy configured but unenforced |
| `app.py` | 640 lines | 🟠 Second full UI duplicating the React app |

---

**Legend:** 🔴 broken / security · 🟠 significant · 🟡 polish


---

# PART 9 — Phase 0 change log

Completed on 2026-09-18. Every fix has a regression test that fails against the old code.

## Backend correctness

| File | Change |
|---|---|
| `database.py` | Removed the undeclared table alias from the search query. Extracted `_MEETING_LIST_SQL` + `_row_to_list_item` so `get_meeting_list` and `search_meetings` share one definition instead of two divergent copies — the duplication is why the bug went unnoticed. |
| `database.py` | `insert_meeting` now deletes child rows before re-inserting, so saving the same meeting twice no longer doubles its action items, deadlines and decisions. |
| `utils.py` | `_token_chunks` derives the tokens-per-word ratio from the actual text instead of an inverted constant. A 1000-token request now yields ~1000-token chunks (was ~1563). |
| `utils.py` | `chunk_size`/`overlap` compare against `None`, so `overlap=0` is honoured instead of being replaced by the default. |
| `utils.py` | The chunk cache is a bounded LRU (`CHUNK_CACHE_MAX_ENTRIES = 512`) rather than an unbounded module dict. |
| `pipeline.py` | `_hash_chunk` → `_chunk_cache_key`, keyed on prompt version + provider + model + temperature + text (SHA-256). Switching provider no longer replays the previous provider's summaries. |
| `pipeline.py` | `processing_time` rounds to 3dp; 2dp collapsed fast runs to `0.0`. Dropped the unused `clear_chunk_cache` import. |
| `prompts.py` | Added `PROMPT_VERSION`, which participates in the cache key so editing a prompt invalidates stale entries. |
| `graph.py` | Dropped the unused `ConfigBuilder` import. |

## API

| File | Change |
|---|---|
| `api/main.py` | CORS origins come from `ALLOWED_ORIGINS` instead of `*`, with an explicit method/header allowlist. Added `GZipMiddleware`. `init_db()` moved from import-time into a `lifespan` handler. |
| `api/main.py` | The global handler returns an opaque message plus a correlation `error_id`; the exception text is logged, never sent. |
| `api/routes.py` | Added `GET /api/health` (database reachability + configured providers). Provider failures return 502 and validation errors 400, both without internal detail. |
| `api/routes.py` | The graph endpoint always returns `{entities, relationships}`, even when the stored JSON is valid but wrongly shaped. |
| `api/schemas.py` | Added `HealthResponse`; constrained `ProcessRequest` (`text` 1–2,000,000 chars, `temperature` 0–2, `chunk_size` 100–100,000, `chunk_mode` pattern). |
| `config.py` | Added `ALLOWED_ORIGINS`; bounds on temperature/chunk settings; a validator rejecting `overlap >= chunk_size`; quote-stripping for container env files. Removed the unused `os` import. |

## Frontend

| File | Change |
|---|---|
| `components/Layout.tsx` | Added the missing `key` prop on the nav links. |
| `api/client.ts` | `checkHealth` calls the real `/health` endpoint instead of `/config`. |
| `types/index.ts` | Added the `Health` interface. |

## Repository & tooling

| File | Change |
|---|---|
| `frontend/.gitignore` | **New.** `node_modules/`, `dist/`, `.vite/`. |
| `.dockerignore` | **New.** Keeps `.venv/`, `node_modules/`, `.git/`, `data/` and `.env` out of the image. |
| `.gitignore` | Added `node_modules/`, build output, and tooling caches. |
| — | `git rm -r --cached frontend/node_modules`: **6,558 → 57 tracked files** (files untouched on disk). |
| `.github/workflows/ci.yml` | **New.** Backend matrix (3.11/3.12) with ruff + pytest + 70% coverage gate; frontend `tsc --noEmit` + build; Docker build. |
| `.pre-commit-config.yaml` | **New.** ruff, large-file guard, private-key detection, gitleaks. |
| `pyproject.toml` | `packages.find` no longer names a non-existent package. Added `ruff`/`httpx` to dev extras, `line-length = 120`, `N806` ignore, per-file `E501` ignores, coverage omits for unimportable modules. |
| `requirements.txt` | Re-synced with `pyproject.toml` — it was missing `fastapi` and `uvicorn`, so it could not run the app. |
| `.env.example` | Unquoted values, plus the new `ALLOWED_ORIGINS` key. |

## Tests: 53 passing / 20 failing → **118 passing, 3 skipped**

| File | Change |
|---|---|
| `tests/test_api.py` | **New, 24 tests.** Health, config, stats, CRUD, 404s, the graph endpoint, request validation, and a test asserting internal error detail never reaches the client. |
| `tests/test_database.py` | Fixture rewritten: the old one pointed at `":memory:"`, and since each call opens a new connection, every call got a *different empty database* — which is why all 15 tests failed. Now uses a `tmp_path` file. Added 8 regression tests for search and re-insert. |
| `tests/test_pipeline.py` | Added an autouse temp-database fixture (the suite was writing test meetings into the real `data/meetings.db`), plus 8 tests for cache-key composition and LRU eviction. |
| `tests/test_utils.py` | Added 4 chunk-budget regression tests. Split `test_speaker_mode`, which asserted a split that contradicts the function's documented merging of short turns, into separate split and merge tests. |
| `tests/test_graph.py` | The three agraph tests now skip when the optional `streamlit-agraph` extra is absent instead of failing. |
| `tests/conftest.py` | `sample_transcript` resolves from `__file__`, so the suite passes from any working directory. |

## Verified

- `pytest` — 118 passed, 3 skipped, **76% coverage**
- `ruff check .` — clean
- `npx tsc --noEmit` and `npm run build` — clean
- `docker build` + container smoke test — `/api/health` returns `{"status":"ok"}`, CORS allows the dev origin and rejects others
- `validate.py` — all five checks pass

## Known remaining (deliberately deferred)

- `get_connection`'s retry-after-`yield` can still raise `RuntimeError: generator didn't stop after throw()` (§1.5) — belongs with the connection-pooling work in Phase 1.
- `nx.Graph` still loses relationship direction (§1.6).
- `GraphData` still re-parses its JSON per access (§1.7).
- Frontend search still fires per keystroke with no debounce (§1.8).
- **`data/meetings.db` holds 23 rows of test debris** ("Mock Meeting", "Empty Transcript") from suite runs before the isolation fix. Your 5 real meetings are intact. These are safe to delete but were left in place pending your confirmation.

---

# PART 10 — Phase 1 change log

Completed on 2026-09-18. Verified against the live Gemini API, not only mocks.

## Provider layer

| Change | Why |
|---|---|
| `providers/errors.py` — typed hierarchy, each carrying `retryable`, status code and `Retry-After` | Every failure was a bare `RuntimeError`; callers could not tell an expired key from a rate limit |
| `providers/retry.py` — exponential backoff with full jitter, honouring `Retry-After` | One transient 429 permanently lost a chunk of the transcript |
| `BaseProvider` restructured as a template (`_generate_raw` / `_agenerate_raw`) | Removed the duplicated `generate`/`generate_json` pair from all three providers |
| Async support via each SDK's own async client | Required for concurrent summarisation |
| Timeouts on Gemini and Groq | Neither had one; a hung connection stalled a run indefinitely |
| `AVAILABLE_MODELS` per provider + `model` on `ProcessRequest` | Models were fixed in constructors and could not be chosen |
| Gemini default → `gemini-flash-latest` | `gemini-2.0-flash` had been retired and 404'd on every call |

## Pipeline

| Change | Why |
|---|---|
| Concurrent chunk summarisation behind a semaphore | Serial loop made wall-clock time linear in transcript length |
| Recursive merge in batches of 5, batches run concurrently | Merging every summary in one prompt overflows the context window |
| `chunk_failures` / `degraded` tracked and persisted | A partial run was presented as if it were complete |
| `PipelineError` when *no* chunk succeeds | A total failure was saved as a hollow meeting and reported as success |
| Auth failures abort the run | A rejected key walked the whole transcript failing identically; a 62-chunk transcript now stops after the in-flight batch |
| `LLMClient` with a provider fallback chain | One vendor's outage ended the run |
| Fallback responses are never written to the summary cache | The key names the *requested* provider, so caching a fallback's output would make a later healthy run replay another vendor's work |
| Progress callbacks + stage-weighted `overall_fraction` | Nothing real existed to drive a progress bar |
| `StructuredExtraction` / `KnowledgeGraphPayload` replace JSON scraping | `Model(**item)` raised `TypeError` on one unexpected key and discarded the whole batch |
| `merge_participant_names` | "Alice" and "Alice Chen" were listed as two people, doubling the participant count |

## Data layer

| Change | Why |
|---|---|
| `PRAGMA user_version` migration runner, **append-only** | `CREATE TABLE IF NOT EXISTS` cannot evolve a populated schema |
| FTS5 index over titles, participants, summaries, transcripts, tasks, decisions, deadlines, BM25-ranked | `LIKE '%term%'` across six tables could not rank and never searched transcripts |
| Pagination on list and search | Listing was unbounded with two correlated subqueries per row |
| Index on `created_at` | Both queries sort by it; only `date` was indexed |
| New columns: `model`, `chunk_total`, `chunk_failures`, `input_tokens`, `output_tokens`, `served_by`, `updated_at` | Nothing recorded how a run actually went |

## API

| Change | Why |
|---|---|
| `POST /api/process` → 202 + job id | Inline processing held a connection for minutes; any proxy would cut it |
| `GET /api/jobs/{id}` and `/events` (SSE) | No way to poll, stream or cancel |
| `DELETE /api/jobs/{id}` | Cancellation |
| `GET /api/providers` | Exposes configured state and selectable models |
| Provider guard checks the registry first, requires a key only for hosted providers | Requiring a key for everything would reject a local or test provider |
| Progress clamped to its high-water mark | The merge stage revisits a stage across rounds, making the bar jump backwards |

## Frontend

Real SSE-driven progress with polling fallback and cancellation; degraded-run
warnings in the result pane and a "partial" badge in the history list; provider
and model pickers; 300 ms search debounce with `AbortController` so a slow
earlier response cannot overwrite a later one; real error states replacing
`.catch(console.error)`; delete confirmation.

## Verified

- `pytest` — **173 passed, 3 skipped, 81% coverage**
- `ruff check .` — clean; `tsc --noEmit` and `npm run build` — clean
- **Live Gemini run**: sample transcript → 10 action items, 4 decisions,
  5 deadlines, 24-entity graph, 5 deduplicated participants, 15.8s, not degraded
- **Live failure paths**: an invalid Groq key produces "The provider rejected the
  configured API key", not an opaque error; an exhausted quota retries with
  backoff, then reports a degraded or failed run honestly

## Notes for whoever picks this up

- The **Groq key in `.env` is invalid** (401) and the **Gemini free-tier quota is
  exhausted** on the larger models. `gemini-flash-lite-latest` still had quota and
  was used for the successful verification run.
- Groq and OpenRouter `AVAILABLE_MODELS` are **unverified** — no working key was
  available. Confirm against each vendor's model list before trusting them.
- Jobs are in-process. Restarting the API loses running jobs. Moving to Redis and
  a separate worker is contained: the HTTP surface would not change.

## Still outstanding from Part 1

- `get_connection`'s retry-after-`yield` can still raise `RuntimeError` (§1.5)
- `nx.Graph` still loses relationship direction (§1.6)
- `GraphData` still re-parses its JSON per access (§1.7)
- `app.py`, the parallel Streamlit UI, still exists (§2.2) — Phase 2
