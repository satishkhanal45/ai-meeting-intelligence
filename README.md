# AI Meeting Intelligence Platform

A production-ready AI-powered meeting intelligence system that understands long meeting transcripts, extracts structured information, visualizes relationships, and maintains a searchable archive of previous meetings.

Built for real-world use — runs entirely locally with only API-based LLM dependencies.

## Features

- **Executive Summaries** — Concise overviews of lengthy transcripts via hierarchical summarization
- **Action Item Extraction** — Owner, task, priority, and status extraction with visual priority coding
- **Deadline Detection** — Explicit dates, relative dates, and milestone extraction
- **Key Decision Extraction** — Strategic decisions agreed during the meeting with rationale
- **Interactive Knowledge Graph** — Dynamic entity-relationship visualization with zoom, pan, drag, hover, and click
- **Meeting Archive** — Persistent SQLite storage with full CRUD operations
- **Full-Text Search** — Search across participants, titles, keywords, tasks, owners, deadlines, and decisions
- **Multi-Provider AI** — Support for Google Gemini, Groq, and OpenRouter via a pluggable abstraction layer
- **Hierarchical Summarization** — Handles transcripts exceeding 1500 words via chunk → summarize → merge pipeline
- **Chunk Summary Caching** — Prevents redundant API calls when re-processing

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Streamlit Frontend                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Dashboard │ │New       │ │History   │ │Settings  │       │
│  │          │ │Meeting   │ │          │ │          │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│         │            │            │            │            │
│         └────────────┴────────────┴────────────┘            │
│                          │                                  │
│                    ┌──────┴──────┐                          │
│                    │   app.py    │                          │
│                    └──────┬──────┘                          │
└───────────────────────────┼─────────────────────────────────┘
                            │
┌───────────────────────────┼─────────────────────────────────┐
│                    ┌──────┴──────┐                          │
│                    │  pipeline   │                          │
│                    │  .py        │                          │
│                    └──────┬──────┘                          │
│          ┌────────────────┼────────────────┐                │
│          ▼                ▼                ▼                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  prompts.py │  │  graph.py   │  │  models.py  │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
│          │                                                 │
│          ▼                                                 │
│  ┌────────────────────────────────────────────────────┐    │
│  │              Provider Abstraction                   │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │    │
│  │  │  Gemini   │  │   Groq   │  │   OpenRouter     │ │    │
│  │  │ Provider  │  │ Provider  │  │   Provider       │ │    │
│  │  └──────────┘  └──────────┘  └──────────────────┘ │    │
│  └────────────────────────────────────────────────────┘    │
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
│ 10. Display      │  Render results in Streamlit tabs + interactive graph
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

| Provider   | Env Variable         | Get Key At                                 |
|------------|---------------------|--------------------------------------------|
| Gemini     | `GEMINI_API_KEY`    | https://aistudio.google.com/app/apikey     |
| Groq       | `GROQ_API_KEY`      | https://console.groq.com/keys              |
| OpenRouter | `OPENROUTER_API_KEY` | https://openrouter.ai/keys                |

At least one API key is required. The application will detect which providers are configured and make them available in the sidebar.

## Running Locally

```bash
uv run streamlit run app.py
```

The application opens in your browser at `http://localhost:8501`.

## Configuration

All configuration is managed via `.env` file:

| Variable                | Default  | Description                  |
|------------------------|----------|------------------------------|
| `DEFAULT_PROVIDER`     | gemini   | Default LLM provider         |
| `DEFAULT_TEMPERATURE`  | 0.3      | LLM temperature              |
| `DEFAULT_CHUNK_SIZE`   | 1000     | Token chunk size             |
| `DEFAULT_CHUNK_OVERLAP`| 200      | Chunk overlap in tokens      |

## Usage Guide

### 1. Process a Meeting

1. Navigate to **New Meeting** in the sidebar
2. Paste a transcript or upload a `.txt`/`.md` file
3. Configure provider, temperature, chunk settings in the sidebar
4. Click **Process Meeting**
5. View results in the six tabs: Summary, Action Items, Deadlines, Key Decisions, Knowledge Graph, Transcript

### 2. Browse History

1. Navigate to **Meeting History**
2. Search by keyword, participant, title, task, or decision
3. Click **View** on any meeting to see full details

### 3. Explore Knowledge Graphs

1. Navigate to **Knowledge Graph**
2. Select a meeting from the dropdown
3. Explore the interactive graph — zoom, pan, drag nodes, hover for details

### 4. Dashboard

The dashboard shows summary metrics: total meetings, unique participants, total action items, and total decisions.

## Project Structure

```
meeting-intelligence/
├── app.py                    # Streamlit UI entry point (multi-page)
├── pipeline.py               # Hierarchical summarization pipeline
├── graph.py                  # Knowledge graph builder (NetworkX + streamlit-agraph)
├── database.py               # SQLite CRUD and full-text search
├── config.py                 # Environment configuration (pydantic-settings)
├── logger.py                 # Structured JSON logging (console + file)
├── prompts.py                # LLM prompt templates (5 prompt factories)
├── models.py                 # Pydantic data models (12 model classes)
├── utils.py                  # Cleaning, chunking, caching utilities
├── providers/                # LLM provider abstraction
│   ├── __init__.py           # Package exports
│   ├── base_provider.py      # Abstract base class
│   ├── gemini_provider.py    # Google Gemini implementation
│   ├── groq_provider.py      # Groq implementation
│   └── openrouter_provider.py # OpenRouter implementation
├── data/                     # SQLite database storage (auto-created)
│   └── logs/                 # Application logs (auto-created)
├── meetings/                 # Sample transcripts and exports
│   └── sample_transcript.txt # Sprint planning sample
├── tests/                    # Pytest test suite
│   ├── conftest.py           # Shared fixtures
│   ├── test_models.py        # 8 model test classes
│   ├── test_utils.py         # 7 utility test classes
│   ├── test_database.py      # 6 database test classes
│   ├── test_graph.py         # 4 graph test classes
│   └── test_pipeline.py      # 3 pipeline test classes
├── assets/                   # Static assets
├── .streamlit/               # Streamlit configuration
├── pyproject.toml            # Project metadata & dependencies
├── requirements.txt          # pip-compatible dependency list
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

The knowledge graph is dynamically generated from extracted entities and relationships. It is stored as JSON and reconstructed on demand — no AI re-processing needed.

### Entity Types & Appearance

| Type        | Color   | Shape       | Description                    |
|-------------|---------|-------------|--------------------------------|
| Person      | Blue    | Image       | Participants and individuals   |
| Task        | Green   | Box         | Action items                   |
| Deadline    | Yellow  | Hexagon     | Time-bound deliverables        |
| Decision    | Purple  | Diamond     | Strategic choices              |
| Milestone   | Orange  | Star        | Key project milestones         |
| Information | Gray    | Ellipsis    | Contextual data nodes          |
| Critical    | Red     | Triangle    | High-priority items            |

### Relationship Types

| Label             | Direction | Meaning                                    |
|-------------------|-----------|--------------------------------------------|
| `owns`            | → Task    | Person owns/responsible for a task         |
| `assigns`         | → Task    | Person assigns task to another             |
| `depends_on`      | → Task    | Task depends on another task               |
| `reviewed_by`     | → Person  | Item reviewed by a person                  |
| `due_on`          | → Deadline| Task is due on a specific deadline         |
| `primary_contact`  | → Person  | Person is primary contact for task/area    |
| `backup_contact`  | → Person  | Person is backup contact                   |
| `belongs_to`      | → Entity  | Item belongs to a team or project          |
| `discussed_in`    | → Meeting | Entity was discussed in the meeting        |

### Interaction

- **Zoom** — Scroll wheel or pinch
- **Pan** — Click and drag background
- **Drag** — Click and drag individual nodes
- **Hover** — Hover over a node to see its properties (owner, deadline, status, etc.)
- **Navigation buttons** — Zoom controls in the bottom-left corner

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

1. Start the app: `uv run streamlit run app.py`
2. Go to **New Meeting**
3. Upload `meetings/sample_transcript.txt`
4. Configure provider in sidebar
5. Click **Process Meeting**

## Screenshots

<!-- Add screenshots here after running the application:

![Dashboard](./assets/dashboard.png)
*Dashboard with summary metrics*

![New Meeting](./assets/new_meeting.png)
*Processing a new meeting with results tabs*

![Knowledge Graph](./assets/knowledge_graph.png)
*Interactive knowledge graph visualization*

![Meeting History](./assets/meeting_history.png)
*Search and browse past meetings*
-->

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
- **Docker Deployment** — Containerized deployment with docker-compose

## Troubleshooting

| Problem                          | Solution                                           |
|----------------------------------|----------------------------------------------------|
| No API key configured            | Add at least one key to `.env`                     |
| Provider returns empty response  | Check API key validity and quota                   |
| Database locked error            | Ensure only one instance is running                 |
| Large transcript fails           | Reduce chunk size in sidebar settings              |
| Graph not displaying             | Refresh the page; ensure streamlit-agraph installed|
| ImportError: streamlit-agraph    | Run `uv add streamlit-agraph`                      |
| Invalid JSON from LLM            | Retry with lower temperature (0.1–0.3)             |
| File upload fails                | Ensure file is UTF-8 encoded; try `.txt` format    |
| `uv` command not found           | Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh` |

## License

MIT
