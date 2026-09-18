"""Tests for the knowledge graph module."""

from __future__ import annotations

import json

from graph import build_graph, graph_statistics
from models import GraphData


class TestBuildGraph:
    def test_empty_graph(self):
        gd = GraphData()
        G = build_graph(gd)
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_graph_with_entities(self, sample_graph_data):
        G = build_graph(sample_graph_data)
        assert G.number_of_nodes() >= 4
        assert G.has_node("person-alice")
        assert G.has_node("person-bob")
        assert G.has_node("task-review")

    def test_graph_with_relationships(self, sample_graph_data):
        G = build_graph(sample_graph_data)
        assert G.has_edge("person-alice", "task-review")
        assert G.has_edge("person-bob", "task-review")
        assert G.has_edge("task-review", "deadline-thu")

    def test_node_attributes(self, sample_graph_data):
        G = build_graph(sample_graph_data)
        node = G.nodes["task-review"]
        assert node["label"] == "Security review"
        assert node["type"] == "task"
        assert node["color"] == "#27AE60"
        assert node["priority"] == "high"

    def test_missing_relationship_target(self):
        data = GraphData(
            graph_json=json.dumps({
                "entities": [{"id": "p1", "label": "Alice", "type": "person"}],
                "relationships": [{"source": "p1", "target": "nonexistent", "label": "assigns"}],
            })
        )
        G = build_graph(data)
        assert G.number_of_nodes() == 1
        assert G.number_of_edges() == 0


class TestGraphStatistics:
    def test_empty_stats(self):
        stats = graph_statistics(GraphData())
        assert stats["node_count"] == 0
        assert stats["edge_count"] == 0

    def test_stats_with_data(self, sample_graph_data):
        stats = graph_statistics(sample_graph_data)
        assert stats["node_count"] >= 4
        assert stats["edge_count"] >= 3
        assert "person" in stats["type_breakdown"]
        assert "task" in stats["type_breakdown"]
        assert stats["type_breakdown"]["person"] >= 2
