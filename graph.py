"""Knowledge graph builder.

Converts extracted graph JSON into a NetworkX graph and reports statistics
over it. Rendering lives in the React frontend, which draws the graph from
the JSON served by ``GET /api/meetings/{id}/graph``.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from logger import get_logger
from models import GraphData

logger = get_logger(__name__)

# ── Colour map ──────────────────────────────────────────────────────────

NODE_COLORS: dict[str, str] = {
    "person": "#4A90D9",
    "task": "#27AE60",
    "deadline": "#F1C40F",
    "decision": "#8E44AD",
    "milestone": "#E67E22",
    "information": "#95A5A6",
    "critical": "#E74C3C",
}

DEFAULT_COLOR = "#95A5A6"

NODE_SHAPES: dict[str, str] = {
    "person": "circle",
    "task": "box",
    "deadline": "hexagon",
    "decision": "diamond",
    "milestone": "star",
    "information": "ellipse",
    "critical": "triangle",
}

DEFAULT_SHAPE = "ellipse"

# ── NetworkX graph builder ─────────────────────────────────────────────


def build_graph(graph_data: GraphData) -> nx.Graph:
    """Build a NetworkX graph from ``GraphData``.

    Nodes are added with attributes: ``label``, ``type``, ``color``,
    ``shape``, and all custom ``properties``.  Edges include the
    relationship ``label``.
    """
    G = nx.Graph()

    for entity in graph_data.entities:
        color = NODE_COLORS.get(entity.type, DEFAULT_COLOR)
        shape = NODE_SHAPES.get(entity.type, DEFAULT_SHAPE)
        G.add_node(
            entity.id,
            label=entity.label,
            type=entity.type,
            color=color,
            shape=shape,
            **entity.properties,
        )

    for rel in graph_data.relationships:
        if rel.source in G and rel.target in G:
            G.add_edge(rel.source, rel.target, label=rel.label)

    logger.debug(
        "NetworkX graph built",
        extra={"nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
    )
    return G


def graph_statistics(graph_data: GraphData) -> dict[str, Any]:
    """Return summary statistics for graphing or display.

    Returns a dict with ``node_count``, ``edge_count``, and a
    ``type_breakdown`` mapping entity types to counts.
    """
    G = build_graph(graph_data)
    type_counts: dict[str, int] = {}
    for _, data in G.nodes(data=True):
        etype = data.get("type", "unknown")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    return {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "type_breakdown": type_counts,
    }
