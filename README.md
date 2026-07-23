# AI Meeting Intelligence Platform

A production-ready AI-powered meeting intelligence system that understands long meeting transcripts, extracts structured information, visualizes relationships, and maintains a searchable archive of previous meetings.

Built for real-world use — runs entirely locally with only API-based LLM dependencies.

The original Streamlit frontend has been migrated to a **React SPA** powered by a **FastAPI REST backend**. The Streamlit version is still available as an optional fallback (see below).

## Features

- **Executive Summaries** — Concise overviews of lengthy transcripts via hierarchical summarization
- **Action Item Extraction** — Owner, task, priority, and status extraction with visual priority coding
- **Deadline Detection** — Explicit dates, relative dates, and milestone extraction
- **Key Decision Extraction** — Strategic decisions agreed during the meeting with rationale
- **Interactive 3D Knowledge Graph** — Three.js-based entity-relationship visualization with orbit controls, directional arrows, animated particles, and always-visible labels
- **Meeting Archive** — Persistent SQLite storage with full CRUD operations
- **Full-Text Search** — Search across participants, titles, keywords, tasks, owners, deadlines, and decisions
- **Multi-Provider AI** — Support for Google Gemini, Groq, and OpenRouter via a pluggable abstraction layer
- **Hierarchical Summarization** — Handles transcripts exceeding 1500 words via chunk → summarize → merge pipeline
- **Chunk Summary Caching** — Prevents redundant API calls when re-processing

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     React SPA (Vite + TS)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │Dashboard │ │New       │ │History   │ │Settings  │        │
│  │          │ │Meeting   │ │          │ │          │        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
│         │            │            │            │            │
│         └────────────┴────────────┴────────────┘            │
│                          │                                  │
│                     HTTP /api/*                             │
└───────────────────────────┼─────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────┐
│                    ┌──────┴──────┐                          │
│                    │  FastAPI     │                         │
│                    │  api/main.py │                         │
│                    └──────┬──────┘                          │
│                    ┌──────┴──────┐                          │
│                    │  pipeline   │                          │
│                    │  .py        │                          │
│                    └──────┬──────┘                          │
│          ┌────────────────┼────────────────┐                │
│          ▼                ▼                ▼                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  prompts.py │  │  graph.py   │  │  models.py  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│          │                                                  │
│          ▼                                                  │
│  ┌────────────────────────────────────────────────────┐     │
│  │              Provider Abstraction                  │     │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │     │
│  │  │  Gemini   │  │   Groq   │  │   OpenRouter    │  │     │
│  │  │ Provider  │  │ Provider  │  │   Provider     │  │     │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │     │
│  └────────────────────────────────────────────────────┘     │
│                            │                                │
│                            ▼                                │
│                    ┌──────────────┐                         │
│                    │  database.py │                         │
│                    │   (SQLite)   │                         │
│                    └──────────────┘                         │
│                            │                                │
│                    ┌──────────────┐                         │
│                    │   config.py  │                         │
│                    │   logger.py  │                         │
│                    │   utils.py   │                         │
│                    └──────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User input (paste / file upload)
       │
       ▼
┌──────────────────┐
│  1. Clean        │  Fix encoding, normalize whitespace, deduplicate lines
│  2. Detect       │  Extract participant names from speaker patterns
│  3. Chunk        │  Token-based or speaker-based splitting
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  4. Summarize    │  LLM summarizes each chunk (cached by content hash)
│  5. Merge        │  Chunk summaries merged into one coherent summary
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  6. Extract      │  LLM extracts structured data (JSON): action items,
│                  │  deadlines, decisions, participants, title
│  7. Graph        │  LLM generates knowledge graph JSON from extracted data
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  8. Package      │  Assemble Meeting Pydantic model
│  9. Save         │  Persist to SQLite (7 tables)
│ 10. Display      │  Render results in React tabs + interactive graph
└──────────────────┘
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — Fast Python package installer

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd ai-meeting-intelligence

# Create virtual environment and install dependencies
uv venv
uv sync

# Install dev dependencies (for testing)
uv sync --dev

# Configure API keys
cp .env.example .env
# Edit .env and add at least one API key
```

## API Key Setup

| Provider   | Env Variable        | Get Key At                                 |
|------------|---------------------|--------------------------------------------|
| Gemini     | `GEMINI_API_KEY`    | https://aistudio.google.com/app/apikey     |
| Groq       | `GROQ_API_KEY`      | https://console.groq.com/keys              |
| OpenRouter | `OPENROUTER_API_KEY`| https://openrouter.ai/keys                 |

At least one API key is required. The application will detect which providers are configured and make them available.

## Running Locally

You need **two terminals** — one for the Python API backend and one for the React frontend.

### Backend (FastAPI)

```bash
uv run uvicorn api.main:app --reload --port 8080
```

The API runs at `http://localhost:8080` with interactive docs at `http://localhost:8080/docs`.

### Frontend (React)

```bash
cd frontend
npm install
npm run dev
```

The React app runs at `http://localhost:5173` and proxies `/api` requests to the backend.

### Streamlit (optional fallback)

The original Streamlit UI is still available via:

```bash
uv sync --extra streamlit
uv run streamlit run app.py
```

## Configuration

All configuration is managed via `.env` file:

| Variable               | Default  | Description                  |
|------------------------|----------|------------------------------|
| `DEFAULT_PROVIDER`     | groq     | Default LLM provider         |
| `DEFAULT_TEMPERATURE`  | 0.3      | LLM temperature              |
| `DEFAULT_CHUNK_SIZE`   | 1000     | Token chunk size             |
| `DEFAULT_CHUNK_OVERLAP`| 200      | Chunk overlap in tokens      |

## Usage Guide

### 1. Process a Meeting

1. Navigate to **New Meeting** in the sidebar
2. Paste a transcript or upload a `.txt`/`.md`/`.csv`/`.json` file
3. Click **Process Meeting**
4. View results in the tabs: Summary, Action Items, Deadlines, Decisions, Transcript

### 2. Browse History

1. Navigate to **Meeting History**
2. Search by keyword, participant, title, task, or decision
3. Click any meeting to see full details in the side panel

### 3. Explore Knowledge Graphs

1. Navigate to **Knowledge Graph**
2. Select a meeting from the dropdown
3. Explore the interactive 3D graph — orbit controls (rotate/pan/zoom), drag nodes, arrows show direction, particles flow along relationships

### 4. Dashboard

The dashboard shows summary metrics: total meetings, unique participants, total action items, and total decisions.

## Project Structure

```
meeting-intelligence/
├── api/                      # FastAPI REST API
│   ├── __init__.py           # Package init
│   ├── main.py               # FastAPI app with CORS
│   ├── routes.py             # API routes (7 endpoints)
│   └── schemas.py            # Request/response schemas
├── frontend/                 # React SPA (Vite + TypeScript)
│   ├── src/
│   │   ├── api/client.ts     # Typed fetch wrapper
│   │   ├── types/index.ts    # Shared TypeScript interfaces
│   │   ├── components/       # Reusable UI components
│   │   │   ├── Layout.tsx    # Sidebar + main layout
│   │   │   ├── StatCard.tsx  # Metric display card
│   │   │   ├── MeetingCard.tsx # Meeting list item
│   │   │   ├── MeetingTabs.tsx # Detail tabs component
│   │   │   └── GraphViewer.tsx # 3d-force-graph (Three.js)
│   │   └── pages/            # Route pages
│   │       ├── Dashboard.tsx
│   │       ├── NewMeeting.tsx
│   │       ├── MeetingHistory.tsx
│   │       ├── KnowledgeGraph.tsx
│   │       └── Settings.tsx
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── app.py                    # Streamlit UI (optional fallback)
├── pipeline.py               # Hierarchical summarization pipeline
├── graph.py                  # Knowledge graph builder
├── database.py               # SQLite CRUD and full-text search
├── config.py                 # Environment configuration (pydantic-settings)
├── logger.py                 # Structured JSON logging (console + file)
├── prompts.py                # LLM prompt templates (5 prompt factories)
├── models.py                 # Pydantic data models (12 model classes)
├── utils.py                  # Cleaning, chunking, caching utilities
├── providers/                # LLM provider abstraction
│   ├── __init__.py
│   ├── base_provider.py
│   ├── gemini_provider.py
│   ├── groq_provider.py
│   └── openrouter_provider.py
├── data/                     # SQLite database storage (auto-created)
│   └── logs/                 # Application logs (auto-created)
├── meetings/                 # Sample transcripts and exports
│   └── sample_transcript.txt
├── tests/                    # Pytest test suite
│   ├── conftest.py
│   ├── test_models.py
│   ├── test_utils.py
│   ├── test_database.py
│   ├── test_graph.py
│   └── test_pipeline.py
├── docker-compose.yml        # Docker orchestration
├── Dockerfile                # API container image
├── pyproject.toml            # Project metadata & dependencies
├── .env.example              # API key template
├── validate.py               # Project validation script
└── README.md                 # This file
```

## Database Schema

SQLite with WAL mode for concurrent read performance. Seven tables with cascade deletes:

```sql
CREATE TABLE meetings (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    date            TEXT NOT NULL,
    participants    TEXT NOT NULL DEFAULT '[]',  -- JSON array
    provider        TEXT NOT NULL DEFAULT '',
    processing_time REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE transcripts (
    meeting_id   TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    raw_text     TEXT NOT NULL DEFAULT '',
    cleaned_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE summaries (
    meeting_id       TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    executive_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    owner       TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    priority    TEXT NOT NULL DEFAULT 'medium',
    status      TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE deadlines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    date        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'explicit'
);

CREATE TABLE decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    decision    TEXT NOT NULL DEFAULT '',
    rationale   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE graph_data (
    meeting_id TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
    graph_json TEXT NOT NULL DEFAULT '{}'
);
```

## Knowledge Graph

The knowledge graph is generated by the LLM from the meeting summary and extracted structured data. It is stored as a JSON string in the `graph_data` table and reconstructed on demand — no AI re-processing needed. The frontend renders it as a **3D force-directed graph** using Three.js.

### Entity Types & Visual Appearance

| Type        | Color   | Icon  | Description                         |
|-------------|---------|-------|-------------------------------------|
| Person      | `#4A90D9` Blue  | 👤 | Participants and individuals   |
| Task        | `#27AE60` Green | 📋 | Action items                   |
| Deadline    | `#F1C40F` Yellow| 📅 | Time-bound deliverables        |
| Decision    | `#8E44AD` Purple| 🎯 | Strategic choices              |
| Milestone   | `#E67E22` Orange| 🏁 | Key project milestones         |
| Information | `#95A5A6` Gray  | ℹ️ | Contextual data nodes          |
| Critical    | `#E74C3C` Red   | ⚠️ | High-priority items            |

Each node renders as a **sprite text pill** (icon + bold label) with a colored background, rounded corners, and a translucent **glow ring**. Nodes with a `status` property show a colored sphere above (🔴 open / 🟡 in_progress / 🟢 done). Nodes with a `priority` property show a sphere below (🔴 high / 🟡 medium / ⚪ low). Node size scales by connection count.

### Relationship Types & Arrow Colors

| Label           | Arrow Color      | Meaning                               |
|-----------------|------------------|---------------------------------------|
| `assigned_to`   | `#6C63FF` Purple | Person assigned to a task             |
| `depends_on`    | `#E74C3C` Red    | Entity depends on another             |
| `related_to`    | `#4ECDC4` Teal   | General relation between entities     |
| `mentioned_in`  | `#FFA07A` Orange | Entity mentioned in a context         |
| `involves`      | `#45B7D1` Blue   | Entity involved in an activity        |
| `leads_to`      | `#F39C12` Amber  | Leads to a result/outcome             |
| `part_of`       | `#95A5A6` Gray   | Entity is part of a group             |

Each relationship renders as a **curved colored line** with a **directional cone arrow** at 95% toward the target and **2 animated particles** flowing source → target. Link color is determined by relationship label.

### Visualization Features

- **🌐 3D Orbit Controls** — Rotate, pan, and zoom with mouse/trackpad
- **↗️ Directional Arrows** — Cone-shaped arrows at the end of each link show relationship direction
- **✨ Animated Particles** — Small spheres flow along each link to visualize activity direction
- **🏷️ Always-Visible Labels** — Node labels (icon + name) render as camera-facing sprites
- **🔄 Force Simulation** — Physics engine auto-positions nodes (warmup 200 ticks, cooldown 50)
- **🔍 Auto-Zoom** — Camera zooms to fit all nodes when simulation settles
- **🖱️ Drag Nodes** — Click and drag to reposition any node

## Provider Abstraction

Adding a new LLM provider requires only three steps:

1. Create a new file in `providers/` (e.g., `anthropic_provider.py`)
2. Implement the `BaseProvider` abstract class with `generate()` and `generate_json()` methods
3. Register it in the provider registry

```python
from providers.base_provider import BaseProvider
from pipeline import register_provider

register_provider("anthropic", AnthropicProvider)
```

No other part of the application needs to change. The `pipeline.py`, `app.py`, and `graph.py` modules depend only on the abstract interface.

## Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=. --cov-report=term-missing -v

# Run specific test file
uv run pytest tests/test_models.py -v
```

## Validation

```bash
uv run python validate.py
```

Checks:
- All required files exist
- All modules import correctly
- Database initializes
- `.env` configuration is present
- Providers are registered

## Sample Transcript

A realistic sprint planning transcript is included at `meetings/sample_transcript.txt` with 5 participants, 10 action items, 3 decisions, and 5 deadlines. Use it to test the application:

1. Start the backend: `uv run uvicorn api.main:app --reload --port 8080`
2. Start the frontend: `cd frontend && npm run dev`
3. Open `http://localhost:5173` and go to **New Meeting**
4. Upload `meetings/sample_transcript.txt` or paste its contents
5. Click **Process Meeting**


## Future Improvements

- **Audio/Video Transcription** — Integrate Whisper or similar for direct media processing
- **Export** — PDF/CSV export of meeting summaries and action items
- **Multi-Language Support** — Prompt templates localized for non-English transcripts
- **Meeting Comparison** — Diff view between two meetings
- **Sentiment Analysis** — Track emotional tone over the course of a meeting
- **Automated Tagging** — ML-based topic tagging for better search
- **Email Integration** — Send action item summaries to participants
- **Batch Processing** — Bulk process multiple transcripts
- **Pagination** — Paginated meeting history for large archives

## Troubleshooting

| Problem                          | Solution                                                      |
|----------------------------------|---------------------------------------------------------------|
| No API key configured            | Add at least one key to `.env`                                |
| Provider returns empty response  | Check API key validity and quota                              |
| Database locked error            | Ensure only one instance is running                           |
| Large transcript fails           | Reduce chunk size in frontend settings                        |
| 3D graph not displaying          | Ensure browser supports WebGL; check console for errors       |
| Invalid JSON from LLM            | Retry with lower temperature (0.1–0.3)                        |
| File upload fails                | Ensure file is UTF-8 encoded; try `.txt` format               |
| `uv` command not found           | Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` |
| Frontend shows API errors        | Ensure backend is running on port 8080                        |
| Port already in use              | Kill existing process: `kill $(lsof -t -i :PORT)`             |

