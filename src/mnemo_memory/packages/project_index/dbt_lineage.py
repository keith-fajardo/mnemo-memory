"""Bounded, deterministic dbt lineage traversal over parsed artifact evidence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from mnemo_memory.packages.domain.dbt_manifest import (
    DbtLineageEdge,
    DbtManifestArtifact,
    DbtManifestNode,
    DbtNodeId,
    LineageNodeNotFound,
)


@dataclass(frozen=True, slots=True)
class TraversedNode:
    node: DbtManifestNode
    depth: int


@dataclass(frozen=True, slots=True)
class LineageTraversal:
    nodes: tuple[TraversedNode, ...]
    edges: tuple[DbtLineageEdge, ...]
    truncated: bool


class DbtLineageGraph:
    """An iterative graph that preserves manifest evidence and stable traversal ordering."""

    def __init__(self, artifact: DbtManifestArtifact, *, max_visited_nodes: int = 50_000) -> None:
        if max_visited_nodes < 1:
            raise ValueError("max_visited_nodes must be positive")
        self._artifact = artifact
        self._max_visited_nodes = max_visited_nodes
        self._nodes = {node.unique_id: node for node in artifact.nodes}
        self._upstream: dict[DbtNodeId, tuple[DbtLineageEdge, ...]] = {}
        self._downstream: dict[DbtNodeId, tuple[DbtLineageEdge, ...]] = {}
        for edge in artifact.edges:
            self._upstream.setdefault(edge.child_id, tuple())
            self._upstream[edge.child_id] = (*self._upstream[edge.child_id], edge)
            self._downstream.setdefault(edge.parent_id, tuple())
            self._downstream[edge.parent_id] = (*self._downstream[edge.parent_id], edge)

    def get_node(self, unique_id: DbtNodeId) -> DbtManifestNode:
        try:
            return self._nodes[unique_id]
        except KeyError as error:
            raise LineageNodeNotFound(
                "dbt lineage node was not found in the authorized artifact"
            ) from error

    def direct_upstream(
        self, unique_id: DbtNodeId, *, include_disabled: bool = True
    ) -> LineageTraversal:
        return self._traverse(
            unique_id, upstream=True, max_depth=1, include_disabled=include_disabled
        )

    def direct_downstream(
        self, unique_id: DbtNodeId, *, include_disabled: bool = True
    ) -> LineageTraversal:
        return self._traverse(
            unique_id, upstream=False, max_depth=1, include_disabled=include_disabled
        )

    def transitive_upstream(
        self, unique_id: DbtNodeId, *, max_depth: int | None = None, include_disabled: bool = True
    ) -> LineageTraversal:
        return self._traverse(
            unique_id, upstream=True, max_depth=max_depth, include_disabled=include_disabled
        )

    def transitive_downstream(
        self, unique_id: DbtNodeId, *, max_depth: int | None = None, include_disabled: bool = True
    ) -> LineageTraversal:
        return self._traverse(
            unique_id, upstream=False, max_depth=max_depth, include_disabled=include_disabled
        )

    def _traverse(
        self, start: DbtNodeId, *, upstream: bool, max_depth: int | None, include_disabled: bool
    ) -> LineageTraversal:
        self.get_node(start)
        if max_depth is not None and max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        adjacent = self._upstream if upstream else self._downstream
        visited = {start}
        queue: deque[tuple[DbtNodeId, int]] = deque([(start, 0)])
        returned: dict[DbtNodeId, TraversedNode] = {}
        traversed_edges: set[DbtLineageEdge] = set()
        truncated = False
        while queue:
            node_id, depth = queue.popleft()
            edges = adjacent.get(node_id, ())
            if max_depth is not None and depth >= max_depth:
                if edges:
                    truncated = True
                continue
            for edge in sorted(
                edges,
                key=lambda item: (
                    str(item.parent_id),
                    str(item.child_id),
                    item.edge_type.value,
                ),
            ):
                neighbor = edge.parent_id if upstream else edge.child_id
                if neighbor in visited:
                    continue
                if len(visited) >= self._max_visited_nodes:
                    truncated = True
                    continue
                visited.add(neighbor)
                neighbor_node = self._nodes[neighbor]
                next_depth = depth + 1
                if include_disabled or neighbor_node.enabled:
                    returned[neighbor] = TraversedNode(neighbor_node, next_depth)
                    traversed_edges.add(edge)
                queue.append((neighbor, next_depth))
        nodes = tuple(
            sorted(returned.values(), key=lambda item: (item.depth, str(item.node.unique_id)))
        )
        edges = tuple(
            sorted(
                traversed_edges,
                key=lambda item: (
                    str(item.parent_id),
                    str(item.child_id),
                    item.edge_type.value,
                ),
            )
        )
        return LineageTraversal(nodes=nodes, edges=edges, truncated=truncated)
