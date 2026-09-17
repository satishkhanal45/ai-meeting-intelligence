"""Knowledge graph builder and visualiser.

Converts extracted graph JSON into a NetworkX graph and generates
a streamlit-agraph compatible configuration for interactive rendering.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from logger import get_logger
from models import GraphData, GraphEntity, GraphRelationship

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


# ── streamlit-agraph helpers ────────────────────────────────────────────


def _build_hover_title(entity: GraphEntity, relationships: list[GraphRelationship]) -> str:
    """Build an HTML hover tooltip for a graph entity."""
    lines = [f"<b>{entity.label}</b>", f"<i>{entity.type}</i>"]

    props = entity.properties
    if props:
        lines.append("<hr>")
        for key, value in props.items():
            lines.append(f"<b>{key}:</b> {value}")

    return "<br>".join(lines)


def build_agraph_nodes_edges(
    graph_data: GraphData,
) -> tuple[list[Any], list[Any]]:
    """Convert ``GraphData`` into streamlit-agraph ``Node`` and ``Edge`` lists.

    Returns ``(nodes, edges)`` suitable for passing to ``agraph()``.
    """
    try:
        from streamlit_agraph import Edge, Node
    except ImportError:
        logger.error("streamlit_agraph is not installed")
        return [], []

    relationships = graph_data.relationships

    nodes: list[Any] = []
    for entity in graph_data.entities:
        color = NODE_COLORS.get(entity.type, DEFAULT_COLOR)
        shape = NODE_SHAPES.get(entity.type, DEFAULT_SHAPE)
        title = _build_hover_title(entity, relationships)
        size = 25 if entity.type == "person" else 20

        nodes.append(
            Node(
                id=entity.id,
                label=entity.label,
                size=size,
                color=color,
                shape=shape,
                title=title,
            )
        )

    edges: list[Any] = []
    for rel in relationships:
        edges.append(
            Edge(
                source=rel.source,
                target=rel.target,
                label=rel.label,
                title=rel.label,
            )
        )

    logger.debug(
        "agraph elements built",
        extra={"nodes": len(nodes), "edges": len(edges)},
    )
    return nodes, edges


def build_agraph_config() -> Any:
    """Return a default streamlit-agraph ``Config`` object.

    The config enables zoom, pan, drag, and hover interactions.
    """
    try:
        from streamlit_agraph import Config
    except ImportError:
        logger.error("streamlit_agraph is not installed")
        return None

    return Config(
        width=900,
        height=600,
        directed=True,
        hierarchical=False,
        node_color=DEFAULT_COLOR,
        edge_color="#7F8C8D",
        font_size=14,
        node_label_highlight=True,
        highlight_color="#2ECC71",
        collapsible=True,
        **{
            "edges": {
                "font": {
                    "size": 12,
                    "color": "#cccccc",
                    "align": "middle",
                    "strokeWidth": 2,
                    "strokeColor": "#0f1117",
                },
                "smooth": {
                    "type": "continuous",
                },
            },
            "interaction": {
                "hover": True,
                "tooltipDelay": 100,
                "navigationButtons": True,
                "keyboard": True,
            },
            "physics": {
                "stabilization": {"iterations": 100},
                "solver": "forceAtlas2Based",
                "forceAtlas2Based": {
                    "gravitationalConstant": -40,
                    "centralGravity": 0.005,
                    "springLength": 200,
                    "springConstant": 0.04,
                    "damping": 0.5,
                },
            },
        },
    )
