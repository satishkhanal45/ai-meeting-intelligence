"""Tests for the FastAPI layer.

Every test runs against a throwaway database and a mock provider, so no
network calls are made and ``data/meetings.db`` is never touched.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from meeting_intelligence.models import ProviderResponse
from meeting_intelligence.providers.errors import ProviderServerError


class _MockProvider:
    """Returns canned responses so the pipeline can run without an API key."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-model"

    async def agenerate(
        self, prompt: str, temperature: float = 0.3, system_prompt: str = ""
    ) -> ProviderResponse:
        return ProviderResponse(content="Mock summary", model="mock-model")

    async def agenerate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        # The pipeline asks for two different JSON documents; tell them apart the
        # same way a real provider would, from the system prompt.
        if "knowledge graph" in system_prompt.lower():
            payload = {
                "entities": [
                    {"id": "person-alice", "label": "Alice", "type": "person", "properties": {}},
                    {"id": "task-ship", "label": "Ship it", "type": "task", "properties": {}},
                ],
                "relationships": [
                    {"source": "person-alice", "target": "task-ship", "label": "owns"}
                ],
            }
        else:
            payload = {
                "title": "Mock Meeting",
                "participants": ["Alice"],
                "action_items": [
                    {"owner": "Alice", "task": "Ship it", "priority": "high", "status": "open"}
                ],
                "deadlines": [],
                "decisions": [{"decision": "Use SQLite", "rationale": "Simple"}],
            }
        return ProviderResponse(content=json.dumps(payload), model="mock-model")


@pytest.fixture
def client(monkeypatch, tmp_path):
    from meeting_intelligence import database
    from meeting_intelligence.pipeline import PROVIDER_REGISTRY, register_provider

    monkeypatch.setattr("meeting_intelligence.database.DB_PATH", str(tmp_path / "api_test.db"))
    database.init_db()
    register_provider("mock", _MockProvider)

    from meeting_intelligence.api.main import app

    with TestClient(app) as c:
        yield c

    PROVIDER_REGISTRY.pop("mock", None)


def run_to_completion(client, text: str = "Alice: Let's ship the release.\nBob: Agreed.", **extra):
    """Submit a transcript and block until its job reaches a terminal state."""
    response = client.post(
        "/api/process", json={"text": text, "provider_name": "mock", **extra}
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in time")


@pytest.fixture
def seeded_meeting(client):
    """Process one transcript through the real pipeline and return its id."""
    job = run_to_completion(client)
    assert job["status"] == "succeeded", job
    return job["meeting_id"]


class TestHealth:
    def test_health_reports_database_and_providers(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["database"] is True
        assert body["status"] in {"ok", "degraded"}
        assert isinstance(body["configured_providers"], list)


class TestConfig:
    def test_config_exposes_defaults(self, client):
        response = client.get("/api/config")
        assert response.status_code == 200
        body = response.json()
        assert "default_provider" in body
        assert "configured_providers" in body


class TestStats:
    def test_stats_on_empty_database(self, client):
        body = client.get("/api/stats").json()
        assert body == {
            "total_meetings": 0,
            "unique_participants": 0,
            "total_action_items": 0,
            "total_decisions": 0,
        }

    def test_stats_reflect_a_processed_meeting(self, client, seeded_meeting):
        body = client.get("/api/stats").json()
        assert body["total_meetings"] == 1
        assert body["total_action_items"] == 1
        assert body["total_decisions"] == 1


class TestMeetings:
    def test_list_is_empty_initially(self, client):
        assert client.get("/api/meetings").json() == []

    def test_get_meeting_returns_full_payload(self, client, seeded_meeting):
        body = client.get(f"/api/meetings/{seeded_meeting}").json()
        assert body["id"] == seeded_meeting
        assert body["summary"]["executive_summary"]
        assert body["transcript"]["raw_text"]

    def test_get_unknown_meeting_is_404(self, client):
        response = client.get("/api/meetings/does-not-exist")
        assert response.status_code == 404

    def test_delete_removes_the_meeting(self, client, seeded_meeting):
        assert client.delete(f"/api/meetings/{seeded_meeting}").status_code == 204
        assert client.get(f"/api/meetings/{seeded_meeting}").status_code == 404

    def test_delete_unknown_meeting_is_404(self, client):
        assert client.delete("/api/meetings/does-not-exist").status_code == 404

    def test_graph_endpoint_returns_entities_and_relationships(self, client, seeded_meeting):
        body = client.get(f"/api/meetings/{seeded_meeting}/graph").json()
        assert [e["id"] for e in body["entities"]] == ["person-alice", "task-ship"]
        assert body["relationships"][0]["label"] == "owns"

    def test_graph_endpoint_always_returns_the_expected_shape(self, client, seeded_meeting):
        # Even if the stored JSON is valid but not graph-shaped, the endpoint
        # must not hand the frontend a document without these two keys.
        from meeting_intelligence import database

        meeting = database.get_full_meeting(seeded_meeting)
        meeting.graph_data.graph_json = json.dumps({"unexpected": "payload"})
        database.insert_meeting(meeting)

        body = client.get(f"/api/meetings/{seeded_meeting}/graph").json()
        assert body == {"entities": [], "relationships": []}

    def test_graph_for_unknown_meeting_is_404(self, client):
        assert client.get("/api/meetings/nope/graph").status_code == 404


class TestSearchEndpoint:
    """``GET /api/meetings?search=`` used to return 500 for every query."""

    @pytest.mark.parametrize("query", ["Mock", "Alice", "Ship it", "Use SQLite"])
    def test_search_returns_200_and_matches(self, client, seeded_meeting, query):
        response = client.get("/api/meetings", params={"search": query})
        assert response.status_code == 200
        assert [m["id"] for m in response.json()] == [seeded_meeting]

    def test_search_with_no_matches_returns_empty_list(self, client, seeded_meeting):
        response = client.get("/api/meetings", params={"search": "zzzz-no-such-term"})
        assert response.status_code == 200
        assert response.json() == []

    def test_blank_search_returns_everything(self, client, seeded_meeting):
        response = client.get("/api/meetings", params={"search": "   "})
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestProcessValidation:
    def test_empty_text_is_rejected(self, client):
        assert client.post("/api/process", json={"text": ""}).status_code == 422

    def test_out_of_range_temperature_is_rejected(self, client):
        response = client.post("/api/process", json={"text": "hello", "temperature": 5.0})
        assert response.status_code == 422

    def test_invalid_chunk_mode_is_rejected(self, client):
        response = client.post("/api/process", json={"text": "hello", "chunk_mode": "sideways"})
        assert response.status_code == 422

    def test_oversized_transcript_is_rejected(self, client):
        from meeting_intelligence.api.schemas import MAX_TRANSCRIPT_CHARS

        response = client.post("/api/process", json={"text": "x" * (MAX_TRANSCRIPT_CHARS + 1)})
        assert response.status_code == 422


class TestErrorDisclosure:
    """Internal failure detail must not reach the client."""

    def test_job_failure_reports_an_opaque_message(self, client, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("secret detail: /home/user/.env GEMINI_API_KEY=sk-abc123")

        monkeypatch.setattr("meeting_intelligence.api.routes.aprocess_transcript", boom)
        job = run_to_completion(client)

        assert job["status"] == "failed"
        blob = json.dumps(job)
        assert "secret detail" not in blob
        assert "sk-abc123" not in blob
        assert job["error_id"], "a correlation id should be returned for support"

    def test_provider_failure_is_reported_without_internals(self, client, monkeypatch):
        async def boom(*args, **kwargs):
            raise ProviderServerError(
                "upstream said: api_key=sk-secret-value", provider="mock"
            )

        monkeypatch.setattr("meeting_intelligence.api.routes.aprocess_transcript", boom)
        job = run_to_completion(client)

        assert job["status"] == "failed"
        assert "sk-secret-value" not in json.dumps(job)


class TestJobs:
    def test_job_reaches_full_progress(self, client):
        job = run_to_completion(client)
        assert job["status"] == "succeeded"
        assert job["fraction"] == 1.0
        assert job["stage"] == "done"
        assert job["finished_at"]

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope").status_code == 404
        assert client.delete("/api/jobs/nope").status_code == 404

    def test_cancelling_a_finished_job_is_409(self, client):
        response = client.post(
            "/api/process", json={"text": "Alice: hi there", "provider_name": "mock"}
        )
        job_id = response.json()["job_id"]
        run_deadline = time.monotonic() + 15
        while time.monotonic() < run_deadline:
            if client.get(f"/api/jobs/{job_id}").json()["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert client.delete(f"/api/jobs/{job_id}").status_code == 409

    def test_events_stream_reports_progress_then_completion(self, client):
        response = client.post(
            "/api/process",
            json={
                "text": "\n".join(f"Speaker{i}: " + "word " * 120 for i in range(6)),
                "provider_name": "mock",
                "chunk_size": 100,
                "chunk_overlap": 0,
            },
        )
        job_id = response.json()["job_id"]

        events = []
        with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[len("data: ") :])
                    events.append(event)
                    if event["status"] in {"succeeded", "failed"}:
                        break

        assert events[-1]["status"] == "succeeded"
        assert events[-1]["fraction"] == 1.0
        fractions = [e["fraction"] for e in events]
        # A progress bar must never run backwards, including across the
        # multiple rounds the merge stage needs for a long transcript.
        assert fractions == sorted(fractions)

    def test_events_for_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope/events").status_code == 404


class TestProvidersEndpoint:
    def test_lists_providers_and_models(self, client):
        body = client.get("/api/providers").json()
        names = {p["name"] for p in body["providers"]}
        assert names == {"gemini", "groq", "openrouter"}
        for provider in body["providers"]:
            assert provider["default_model"]
            assert provider["default_model"] in provider["available_models"]

    def test_reports_which_providers_are_configured(self, client):
        body = client.get("/api/providers").json()
        for provider in body["providers"]:
            assert isinstance(provider["configured"], bool)


class TestProcessGuards:
    def test_unknown_provider_is_rejected(self, client):
        response = client.post(
            "/api/process", json={"text": "hello", "provider_name": "not-a-provider"}
        )
        assert response.status_code == 400
        assert "Unknown provider" in response.json()["detail"]

    def test_unconfigured_builtin_provider_is_rejected(self, client, monkeypatch):
        from meeting_intelligence.config import Settings

        # Patch the class, not the instance: pydantic-settings rejects
        # attributes that are not declared fields.
        monkeypatch.setattr(Settings, "is_provider_configured", lambda self, name: False)
        response = client.post(
            "/api/process", json={"text": "hello", "provider_name": "openrouter"}
        )
        assert response.status_code == 400
        assert "not configured" in response.json()["detail"]


class TestPagination:
    def test_limit_caps_the_page(self, client):
        for index in range(3):
            run_to_completion(client, text=f"Alice: meeting number {index} content here")
        assert len(client.get("/api/meetings", params={"limit": 2}).json()) == 2

    def test_offset_walks_the_list(self, client):
        for index in range(3):
            run_to_completion(client, text=f"Alice: meeting number {index} content here")
        first = client.get("/api/meetings", params={"limit": 1, "offset": 0}).json()
        second = client.get("/api/meetings", params={"limit": 1, "offset": 1}).json()
        assert first[0]["id"] != second[0]["id"]

    def test_invalid_limit_is_rejected(self, client):
        assert client.get("/api/meetings", params={"limit": 0}).status_code == 422
        assert client.get("/api/meetings", params={"limit": 10_000}).status_code == 422


class TestEditingExtractedItems:
    """Extraction output used to be write-once.

    A wrong owner, a hallucinated task or a completed item could not be
    corrected, which made the extracted data a read-only report rather than
    something a team could work from.
    """

    def test_action_item_status_can_be_changed(self, client, seeded_meeting):
        meeting = client.get(f"/api/meetings/{seeded_meeting}").json()
        item = meeting["action_items"][0]
        assert item["status"] == "open"

        response = client.patch(f"/api/items/action-items/{item['id']}", json={"status": "done"})
        assert response.status_code == 200
        assert response.json()["action_items"][0]["status"] == "done"

    def test_partial_update_leaves_other_fields_alone(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        original_task = item["task"]

        client.patch(f"/api/items/action-items/{item['id']}", json={"owner": "Reassigned"})
        updated = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        assert updated["owner"] == "Reassigned"
        assert updated["task"] == original_task

    def test_invalid_status_is_rejected(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        response = client.patch(
            f"/api/items/action-items/{item['id']}", json={"status": "somehow"}
        )
        assert response.status_code == 422

    def test_items_can_be_added(self, client, seeded_meeting):
        response = client.post(
            f"/api/meetings/{seeded_meeting}/action-items",
            json={"task": "Something extraction missed", "owner": "Dana", "priority": "high"},
        )
        assert response.status_code == 201
        new_id = response.json()["id"]

        tasks = {
            i["task"] for i in client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"]
        }
        assert "Something extraction missed" in tasks
        assert isinstance(new_id, int)

    def test_hallucinated_items_can_be_deleted(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        assert client.delete(f"/api/items/action-items/{item['id']}").status_code == 204
        assert client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"] == []

    def test_deleting_twice_is_404(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        client.delete(f"/api/items/action-items/{item['id']}")
        assert client.delete(f"/api/items/action-items/{item['id']}").status_code == 404

    def test_unknown_item_is_404(self, client):
        assert client.patch("/api/items/action-items/999999", json={"status": "done"}).status_code == 404
        assert client.delete("/api/items/action-items/999999").status_code == 404

    def test_unknown_item_type_is_404(self, client, seeded_meeting):
        assert client.patch("/api/items/sandwiches/1", json={"x": 1}).status_code == 404
        assert client.post(f"/api/meetings/{seeded_meeting}/sandwiches", json={}).status_code == 404

    def test_adding_to_an_unknown_meeting_is_404(self, client):
        response = client.post("/api/meetings/nope/action-items", json={"task": "x"})
        assert response.status_code == 404

    def test_decisions_and_deadlines_are_editable_too(self, client, seeded_meeting):
        meeting = client.get(f"/api/meetings/{seeded_meeting}").json()
        decision = meeting["decisions"][0]
        assert (
            client.patch(
                f"/api/items/decisions/{decision['id']}", json={"rationale": "Revised reason"}
            ).status_code
            == 200
        )

        created = client.post(
            f"/api/meetings/{seeded_meeting}/deadlines",
            json={"description": "Launch", "date": "2026-10-01", "type": "explicit"},
        )
        assert created.status_code == 201

        refreshed = client.get(f"/api/meetings/{seeded_meeting}").json()
        assert refreshed["decisions"][0]["rationale"] == "Revised reason"
        assert any(d["description"] == "Launch" for d in refreshed["deadlines"])

    def test_meeting_title_can_be_corrected(self, client, seeded_meeting):
        response = client.patch(
            f"/api/meetings/{seeded_meeting}", json={"title": "Corrected Title"}
        )
        assert response.status_code == 200
        assert client.get(f"/api/meetings/{seeded_meeting}").json()["title"] == "Corrected Title"

    def test_empty_title_is_rejected(self, client, seeded_meeting):
        assert client.patch(f"/api/meetings/{seeded_meeting}", json={"title": ""}).status_code == 422

    def test_edits_are_searchable(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        client.patch(f"/api/items/action-items/{item['id']}", json={"task": "Xylophone audit"})

        found = client.get("/api/meetings", params={"search": "Xylophone"}).json()
        assert [m["id"] for m in found] == [seeded_meeting]

    def test_editing_bumps_updated_at(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        client.patch(f"/api/items/action-items/{item['id']}", json={"status": "done"})
        meta = client.patch(
            f"/api/meetings/{seeded_meeting}", json={"title": "Touched"}
        ).json()
        assert meta["updated_at"]

    def test_meeting_id_cannot_be_reassigned(self, client, seeded_meeting):
        """Only declared columns are writable, so an item cannot be moved."""
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        client.patch(
            f"/api/items/action-items/{item['id']}",
            json={"status": "done", "meeting_id": "somewhere-else"},
        )
        still_there = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"]
        assert [i["id"] for i in still_there] == [item["id"]]


class TestExportEndpoints:
    def test_markdown_export_is_a_download(self, client, seeded_meeting):
        response = client.get(f"/api/meetings/{seeded_meeting}/export", params={"format": "md"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment;" in response.headers["content-disposition"]
        assert response.text.startswith("# ")

    @pytest.mark.parametrize(
        ("fmt", "media_type"),
        [("md", "text/markdown"), ("csv", "text/csv"), ("ics", "text/calendar"), ("json", "application/json")],
    )
    def test_every_format_is_served(self, client, seeded_meeting, fmt, media_type):
        response = client.get(f"/api/meetings/{seeded_meeting}/export", params={"format": fmt})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media_type)

    def test_unknown_format_is_rejected(self, client, seeded_meeting):
        response = client.get(f"/api/meetings/{seeded_meeting}/export", params={"format": "exe"})
        assert response.status_code == 422

    def test_export_of_unknown_meeting_is_404(self, client):
        assert client.get("/api/meetings/nope/export").status_code == 404

    def test_action_items_csv_spans_all_meetings(self, client):
        run_to_completion(client, text="Alice: first meeting content goes here")
        run_to_completion(client, text="Bob: second meeting content goes here")
        body = client.get("/api/export/action-items").text
        rows = body.strip().split("\n")
        assert rows[0].startswith("meeting_id")
        assert len(rows) == 3  # header + one action item per meeting

    def test_meetings_index_csv(self, client, seeded_meeting):
        body = client.get("/api/export/meetings").text
        assert body.startswith("id,title,date")
        assert seeded_meeting in body

    def test_deadline_calendar_is_served(self, client, seeded_meeting):
        response = client.get("/api/export/deadlines.ics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        assert response.text.startswith("BEGIN:VCALENDAR")

    def test_export_reflects_edits(self, client, seeded_meeting):
        item = client.get(f"/api/meetings/{seeded_meeting}").json()["action_items"][0]
        client.patch(f"/api/items/action-items/{item['id']}", json={"status": "done"})
        markdown = client.get(
            f"/api/meetings/{seeded_meeting}/export", params={"format": "md"}
        ).text
        assert "- [x]" in markdown


class TestPeopleEndpoints:
    def test_people_are_listed(self, client, seeded_meeting):
        people = client.get("/api/people").json()
        assert any(p["name"] == "Alice" for p in people)

    def test_person_detail_links_back_to_meetings(self, client, seeded_meeting):
        person = next(p for p in client.get("/api/people").json() if p["name"] == "Alice")
        detail = client.get(f"/api/people/{person['id']}").json()
        assert [m["id"] for m in detail["meetings"]] == [seeded_meeting]
        assert detail["action_items"][0]["meeting_id"] == seeded_meeting

    def test_unknown_person_is_404(self, client):
        assert client.get("/api/people/nope").status_code == 404

    def test_action_items_endpoint_spans_meetings(self, client):
        run_to_completion(client, text="Alice: first meeting content here")
        run_to_completion(client, text="Alice: second meeting content here")
        items = client.get("/api/action-items").json()
        assert len(items) == 2
        assert len({i["meeting_id"] for i in items}) == 2

    def test_action_items_filter_by_status(self, client, seeded_meeting):
        assert len(client.get("/api/action-items", params={"status": "open"}).json()) == 1
        assert client.get("/api/action-items", params={"status": "done"}).json() == []

    def test_invalid_status_filter_is_rejected(self, client):
        assert client.get("/api/action-items", params={"status": "nope"}).status_code == 422

    def test_deadlines_endpoint(self, client, seeded_meeting):
        assert isinstance(client.get("/api/deadlines").json(), list)

    def test_completing_an_item_moves_it_between_filters(self, client, seeded_meeting):
        item = client.get("/api/action-items", params={"status": "open"}).json()[0]
        client.patch(f"/api/items/action-items/{item['id']}", json={"status": "done"})
        assert client.get("/api/action-items", params={"status": "open"}).json() == []
        assert len(client.get("/api/action-items", params={"status": "done"}).json()) == 1
