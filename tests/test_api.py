"""Tests for the FastAPI layer.

Every test runs against a throwaway database and a mock provider, so no
network calls are made and ``data/meetings.db`` is never touched.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from models import ProviderResponse


class _MockProvider:
    """Returns canned responses so the pipeline can run without an API key."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-model"

    def generate(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
        return ProviderResponse(content="Mock summary", model="mock-model")

    def generate_json(self, prompt: str, temperature: float = 0.3, system_prompt: str = "") -> ProviderResponse:
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
    import database
    from pipeline import PROVIDER_REGISTRY, register_provider

    monkeypatch.setattr("database.DB_PATH", str(tmp_path / "api_test.db"))
    database.init_db()
    register_provider("mock", _MockProvider)

    from api.main import app

    with TestClient(app) as c:
        yield c

    PROVIDER_REGISTRY.pop("mock", None)


@pytest.fixture
def seeded_meeting(client):
    """Process one transcript through the real pipeline and return its id."""
    response = client.post(
        "/api/process",
        json={"text": "Alice: Let's ship the release.\nBob: Agreed.", "provider_name": "mock"},
    )
    assert response.status_code == 200
    return response.json()["id"]


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
        import database

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

    def test_unknown_provider_is_a_client_error(self, client):
        response = client.post(
            "/api/process", json={"text": "hello", "provider_name": "not-a-provider"}
        )
        assert response.status_code == 400
        assert "Unknown provider" in response.json()["detail"]

    def test_oversized_transcript_is_rejected(self, client):
        from api.schemas import MAX_TRANSCRIPT_CHARS

        response = client.post("/api/process", json={"text": "x" * (MAX_TRANSCRIPT_CHARS + 1)})
        assert response.status_code == 422


class TestErrorDisclosure:
    """Internal failure detail must not reach the client."""

    def test_provider_failure_returns_an_opaque_message(self, client, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("secret detail: /home/user/.env GEMINI_API_KEY=sk-abc123")

        monkeypatch.setattr("api.routes.process_transcript", boom)
        response = client.post("/api/process", json={"text": "hello", "provider_name": "mock"})

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "secret detail" not in detail
        assert "sk-abc123" not in detail
        assert "Error id:" in detail
