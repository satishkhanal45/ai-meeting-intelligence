"""Streamlit frontend for the AI Meeting Intelligence Platform.

Multi-page application with sidebar-driven navigation and tabbed
result views after meeting processing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from config import settings
from database import (
    delete_meeting,
    get_full_meeting,
    get_meeting_count,
    get_meeting_list,
    get_all_participants,
    init_db,
    search_meetings,
)
from graph import (
    build_agraph_config,
    build_agraph_nodes_edges,
    graph_statistics,
)
from logger import get_logger
from models import GraphData
from pipeline import get_provider, process_transcript, register_provider
from providers.gemini_provider import GeminiProvider
from providers.groq_provider import GroqProvider
from providers.openrouter_provider import OpenRouterProvider
from utils import read_transcript_file, truncate

logger = get_logger(__name__)

# ── Page config ─────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Meeting Intelligence",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Provider registration ──────────────────────────────────────────────

register_provider("gemini", GeminiProvider)
register_provider("groq", GroqProvider)
register_provider("openrouter", OpenRouterProvider)

# ── Database initialisation ─────────────────────────────────────────────

init_db()

# ── Session state ──────────────────────────────────────────────────────

_DEFAULT_STATE: dict[str, Any] = {
    "page": "dashboard",
    "current_meeting_id": None,
    "processing": False,
    "error": None,
    "search_query": "",
    "selected_meeting_id": None,
}

for key, value in _DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ── Sidebar ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🧠 Meeting AI")
    st.markdown("---")

    configured = settings.get_configured_providers()
    if not configured:
        st.error("No API keys configured. Add at least one to `.env`.")
        st.page_link = ""

    default_idx = max(
        configured.index(settings.default_provider) if settings.default_provider in configured else 0,
        0,
    )
    provider_name = st.selectbox(
        "Provider",
        options=configured if configured else ["gemini"],
        index=default_idx,
        help="LLM provider for processing",
    )

    temperature = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=settings.default_temperature,
        step=0.05,
        help="Lower = more deterministic, higher = more creative",
    )

    chunk_size = st.number_input(
        "Chunk size (tokens)",
        min_value=100,
        max_value=8000,
        value=settings.default_chunk_size,
        step=100,
        help="Target size of each transcript chunk",
    )

    chunk_overlap = st.number_input(
        "Chunk overlap (tokens)",
        min_value=0,
        max_value=2000,
        value=settings.default_chunk_overlap,
        step=50,
        help="Token overlap between consecutive chunks",
    )

    chunk_mode = st.selectbox(
        "Chunk mode",
        options=["token", "speaker"],
        index=0,
        help="Token-based: fixed-size windows. Speaker-based: split on speaker changes.",
    )

    st.markdown("---")
    st.caption(f"Total meetings: {get_meeting_count()}")
    st.caption("AI Meeting Intelligence v1.0")

# ── Page navigation ────────────────────────────────────────────────────

page_labels = {
    "dashboard": "📊 Dashboard",
    "new": "📝 New Meeting",
    "history": "📚 Meeting History",
    "graph": "🕸️ Knowledge Graph",
    "settings": "⚙️ Settings",
}

current_page = st.session_state.get("page", "dashboard")

selected_label = st.sidebar.selectbox(
    "Navigate",
    options=list(page_labels.values()),
    index=list(page_labels.keys()).index(current_page),
    label_visibility="collapsed",
)
page_key_map = {v: k for k, v in page_labels.items()}
st.session_state.page = page_key_map.get(selected_label, "dashboard")

# ═══════════════════════════════════════════════════════════════════════
# PAGE: Dashboard
# ═══════════════════════════════════════════════════════════════════════

def render_dashboard() -> None:
    st.header("📊 Dashboard")

    col1, col2, col3, col4 = st.columns(4)
    meetings = get_meeting_list()

    with col1:
        st.metric("Total Meetings", len(meetings))
    with col2:
        all_participants = get_all_participants()
        st.metric("Unique Participants", len(all_participants))
    with col3:
        total_actions = sum(m.action_item_count for m in meetings)
        st.metric("Total Action Items", total_actions)
    with col4:
        total_decisions = sum(m.decision_count for m in meetings)
        st.metric("Total Decisions", total_decisions)

    st.markdown("---")

    if not meetings:
        st.info("No meetings processed yet. Go to **New Meeting** to get started.")
        return

    st.subheader("Recent Meetings")
    for m in meetings[:10]:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            with c1:
                st.write(f"**{m.title}**")
                st.caption(f"{m.date} · {', '.join(m.participants[:3])}")
            with c2:
                st.write(f"📋 {m.action_item_count} actions")
            with c3:
                st.write(f"✅ {m.decision_count} decisions")
            with c4:
                if st.button("View", key=f"dash_view_{m.id}"):
                    st.session_state.selected_meeting_id = m.id
                    st.session_state.page = "history"
                    st.rerun()

# ═══════════════════════════════════════════════════════════════════════
# PAGE: New Meeting
# ═══════════════════════════════════════════════════════════════════════

def render_new_meeting() -> None:
    st.header("📝 New Meeting")

    input_mode = st.radio(
        "Input mode",
        options=["paste", "upload"],
        horizontal=True,
        label_visibility="collapsed",
    )

    transcript_text = ""

    if input_mode == "paste":
        transcript_text = st.text_area(
            "Paste transcript",
            height=300,
            placeholder="Paste meeting transcript here...",
        )
    else:
        uploaded = st.file_uploader(
            "Upload transcript file",
            type=["txt", "md", "csv", "json", "html"],
            help="Supported formats: .txt, .md, .csv, .json, .html",
        )
        if uploaded is not None:
            try:
                content = uploaded.read()
                transcript_text = content.decode("utf-8")
                st.success(f"Loaded {len(transcript_text)} characters from {uploaded.name}")
            except UnicodeDecodeError:
                st.error("Could not decode file. Please ensure it is UTF-8 encoded.")

    if transcript_text.strip():
        st.caption(f"Transcript length: {len(transcript_text)} characters")

    col1, col2 = st.columns([1, 5])
    with col1:
        process_clicked = st.button(
            "🚀 Process Meeting",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.processing,
        )
    with col2:
        if st.session_state.processing:
            st.info("Processing... this may take a minute.")

    if process_clicked:
        if not transcript_text.strip():
            st.error("Please provide a transcript before processing.")
            return

        try:
            provider_instance = get_provider(provider_name)
        except ValueError as exc:
            st.error(str(exc))
            return

        st.session_state.processing = True
        st.session_state.error = None
        progress_bar = st.progress(0, text="Starting pipeline...")

        try:
            progress_bar.progress(10, text="Processing transcript...")
            start_time = time.perf_counter()

            meeting = process_transcript(
                text=transcript_text,
                provider_name=provider_name,
                temperature=temperature,
                chunk_size=int(chunk_size),
                chunk_overlap=int(chunk_overlap),
                chunk_mode=chunk_mode,
            )

            elapsed = time.perf_counter() - start_time
            st.session_state.current_meeting_id = meeting.id
            st.session_state.processing = False
            progress_bar.progress(100, text="Complete!")

            st.success(
                f"Meeting processed in {elapsed:.1f}s. "
                f"({len(meeting.action_items)} actions, "
                f"{len(meeting.deadlines)} deadlines, "
                f"{len(meeting.decisions)} decisions)"
            )
            st.rerun()

        except Exception as exc:
            st.session_state.processing = False
            st.session_state.error = str(exc)
            logger.error("Processing failed", extra={"error": str(exc)})
            st.error(f"Processing failed: {exc}")
            progress_bar.progress(0)

    # Show results for the current meeting
    _show_current_meeting_results()


def _show_current_meeting_results() -> None:
    """Render tabs for the currently selected meeting result."""
    meeting_id = st.session_state.current_meeting_id
    if not meeting_id:
        return

    meeting = get_full_meeting(meeting_id)
    if meeting is None:
        return

    st.markdown("---")
    st.subheader(f"📄 {meeting.title}")

    tab_summary, tab_actions, tab_deadlines, tab_decisions, tab_graph, tab_transcript = st.tabs(
        ["Summary", "Action Items", "Deadlines", "Key Decisions", "Knowledge Graph", "Transcript"]
    )

    with tab_summary:
        st.markdown(meeting.summary.executive_summary)

    with tab_actions:
        if not meeting.action_items:
            st.info("No action items extracted.")
        else:
            for i, item in enumerate(meeting.action_items, 1):
                priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                icon = priority_icon.get(item.priority.lower(), "⚪")
                with st.container(border=True):
                    cols = st.columns([3, 2, 1, 1])
                    with cols[0]:
                        st.write(f"{icon} **{item.task}**")
                    with cols[1]:
                        st.write(f"Owner: {item.owner}" if item.owner else "Owner: —")
                    with cols[2]:
                        st.write(f"Priority: {item.priority}")
                    with cols[3]:
                        st.write(f"Status: {item.status}")

    with tab_deadlines:
        if not meeting.deadlines:
            st.info("No deadlines extracted.")
        else:
            for dl in meeting.deadlines:
                type_icon = {"explicit": "📅", "relative": "📆", "milestone": "🏁"}
                icon = type_icon.get(dl.type.lower(), "📌")
                with st.container(border=True):
                    st.write(f"{icon} **{dl.description}**")
                    st.caption(f"Date: {dl.date} · Type: {dl.type}")

    with tab_decisions:
        if not meeting.decisions:
            st.info("No key decisions extracted.")
        else:
            for dec in meeting.decisions:
                with st.container(border=True):
                    st.write(f"✅ **{dec.decision}**")
                    if dec.rationale:
                        st.caption(f"Rationale: {dec.rationale}")

    with tab_graph:
        _render_graph(meeting.graph_data.graph_json)

    with tab_transcript:
        with st.expander("Raw transcript", expanded=False):
            st.text(meeting.transcript.raw_text)
        with st.expander("Cleaned transcript", expanded=False):
            st.text(meeting.transcript.cleaned_text)

    st.markdown("---")
    st.caption(
        f"Processed with {meeting.provider} · "
        f"{meeting.processing_time}s · "
        f"{meeting.date}"
    )


# ═══════════════════════════════════════════════════════════════════════
# PAGE: Meeting History
# ═══════════════════════════════════════════════════════════════════════

def render_history() -> None:
    st.header("📚 Meeting History")

    search_query = st.text_input(
        "Search meetings",
        placeholder="Search by title, participant, task, decision...",
        value=st.session_state.search_query,
    )
    st.session_state.search_query = search_query

    meetings = search_meetings(search_query) if search_query.strip() else get_meeting_list()

    if not meetings:
        st.info("No meetings found.")
        return

    st.caption(f"{len(meetings)} meeting(s) found")

    for m in meetings:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            with c1:
                st.write(f"**{m.title}**")
                st.caption(
                    f"{m.date} · "
                    f"{', '.join(m.participants[:4])}{'...' if len(m.participants) > 4 else ''} · "
                    f"via {m.provider}"
                )
            with c2:
                st.write(f"📋 {m.action_item_count}")
            with c3:
                st.write(f"✅ {m.decision_count}")
            with c4:
                if st.button("View", key=f"hist_view_{m.id}"):
                    st.session_state.selected_meeting_id = m.id

    selected_id = st.session_state.selected_meeting_id
    if selected_id:
        st.markdown("---")
        st.subheader("Meeting Details")
        meeting = get_full_meeting(selected_id)
        if meeting is None:
            st.error("Meeting not found.")
            st.session_state.selected_meeting_id = None
            st.rerun()
            return

        tab_summary, tab_actions, tab_deadlines, tab_decisions, tab_graph, tab_transcript = st.tabs(
            ["Summary", "Action Items", "Deadlines", "Key Decisions", "Knowledge Graph", "Transcript"]
        )

        with tab_summary:
            st.markdown(meeting.summary.executive_summary)

        with tab_actions:
            if not meeting.action_items:
                st.info("No action items.")
            else:
                for item in meeting.action_items:
                    with st.container(border=True):
                        st.write(f"**{item.task}** — *{item.owner}* (priority: {item.priority}, status: {item.status})")

        with tab_deadlines:
            if not meeting.deadlines:
                st.info("No deadlines.")
            else:
                for dl in meeting.deadlines:
                    with st.container(border=True):
                        st.write(f"**{dl.description}** — *{dl.date}* ({dl.type})")

        with tab_decisions:
            if not meeting.decisions:
                st.info("No decisions.")
            else:
                for dec in meeting.decisions:
                    with st.container(border=True):
                        st.write(f"**{dec.decision}**")
                        if dec.rationale:
                            st.caption(dec.rationale)

        with tab_graph:
            _render_graph(meeting.graph_data.graph_json)

        with tab_transcript:
            with st.expander("Raw transcript", expanded=False):
                st.text(meeting.transcript.raw_text)
            with st.expander("Cleaned transcript", expanded=False):
                st.text(meeting.transcript.cleaned_text)

        if st.button("🗑️ Delete this meeting", type="secondary"):
            if delete_meeting(selected_id):
                st.success("Meeting deleted.")
                st.session_state.selected_meeting_id = None
                st.rerun()
            else:
                st.error("Failed to delete meeting.")


# ═══════════════════════════════════════════════════════════════════════
# PAGE: Knowledge Graphs
# ═══════════════════════════════════════════════════════════════════════

def render_graphs() -> None:
    st.header("🕸️ Knowledge Graphs")

    meetings = get_meeting_list()
    if not meetings:
        st.info("No meetings available. Process a meeting first.")
        return

    meeting_options = {f"{m.title[:60]} ({m.date})": m.id for m in meetings}
    selected_label = st.selectbox(
        "Select a meeting to visualise",
        options=list(meeting_options.keys()),
        index=0,
    )
    selected_id = meeting_options.get(selected_label)

    if selected_id:
        meeting = get_full_meeting(selected_id)
        if meeting is None:
            st.error("Meeting not found.")
            return

        stats = graph_statistics(meeting.graph_data)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Nodes", stats["node_count"])
        with col2:
            st.metric("Edges", stats["edge_count"])
        with col3:
            breakdown = ", ".join(f"{k}: {v}" for k, v in stats["type_breakdown"].items())
            st.metric("Types", breakdown if breakdown else "—")

        st.markdown("---")
        _render_graph(meeting.graph_data.graph_json)

        with st.expander("Raw graph JSON", expanded=False):
            try:
                formatted = json.dumps(json.loads(meeting.graph_data.graph_json), indent=2)
                st.code(formatted, language="json")
            except (json.JSONDecodeError, ValueError):
                st.text(meeting.graph_data.graph_json)


# ── Graph renderer ─────────────────────────────────────────────────────

def _render_graph(graph_json: str) -> None:
    """Render a knowledge graph from JSON using streamlit-agraph."""
    if not graph_json or graph_json == "{}":
        st.info("No knowledge graph data available.")
        return

    try:
        from streamlit_agraph import agraph

        graph_data = GraphData(graph_json=graph_json)
        nodes, edges = build_agraph_nodes_edges(graph_data)

        if not nodes:
            st.info("Graph has no entities to display.")
            return

        config = build_agraph_config()
        if config is None:
            st.error("streamlit-agraph configuration failed.")
            return

        agraph(nodes=nodes, edges=edges, config=config)

    except ImportError:
        st.error(
            "streamlit-agraph is required for graph visualisation. "
            "Run: uv add streamlit-agraph"
        )
    except Exception as exc:
        logger.error("Graph rendering failed", extra={"error": str(exc)})
        st.error(f"Failed to render graph: {exc}")


# ═══════════════════════════════════════════════════════════════════════
# PAGE: Settings
# ═══════════════════════════════════════════════════════════════════════

def render_settings() -> None:
    st.header("⚙️ Settings")

    with st.container(border=True):
        st.subheader("API Configuration")
        configured = settings.get_configured_providers()
        for prov in ["gemini", "groq", "openrouter"]:
            status = "✅ Configured" if prov in configured else "❌ Not configured"
            st.write(f"**{prov.title()}**: {status}")

        st.caption(
            "Add or update API keys in the `.env` file at the project root. "
            "Restart the app for changes to take effect."
        )

    with st.container(border=True):
        st.subheader("Database")
        st.write(f"**Path**: `{settings.DB_PATH}`")
        st.write(f"**Meetings stored**: {get_meeting_count()}")

        if st.button("🗑️ Clear all data", type="secondary"):
            st.warning("This will permanently delete all meetings. Are you sure?")
            if st.button("Yes, delete everything", type="primary"):
                _clear_all_data()
                st.success("All data cleared.")
                st.rerun()

    with st.container(border=True):
        st.subheader("About")
        st.write("**AI Meeting Intelligence Platform** v1.0.0")
        st.write("Built with Python 3.11+, Streamlit, and API-based LLMs.")
        st.write("Knowledge graphs powered by NetworkX and streamlit-agraph.")


def _clear_all_data() -> None:
    """Drop all data from the database."""
    import sqlite3
    from config import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            DELETE FROM graph_data;
            DELETE FROM decisions;
            DELETE FROM deadlines;
            DELETE FROM action_items;
            DELETE FROM summaries;
            DELETE FROM transcripts;
            DELETE FROM meetings;
            PRAGMA foreign_keys = ON;
        """)
        conn.commit()
    finally:
        conn.close()
    logger.info("All data cleared")


# ═══════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════

page_routes = {
    "dashboard": render_dashboard,
    "new": render_new_meeting,
    "history": render_history,
    "graph": render_graphs,
    "settings": render_settings,
}

render_fn = page_routes.get(current_page, render_dashboard)
render_fn()
