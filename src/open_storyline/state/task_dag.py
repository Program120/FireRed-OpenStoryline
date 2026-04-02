"""
V1 Task DAG: directed acyclic graph for pipeline dependency management.

Supports:
- Topological sorting
- Dirty propagation (mark downstream nodes)
- Incremental execution planning (only re-run affected nodes)
- Parallel layer computation (nodes in the same layer can run concurrently)
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Set

from pydantic import BaseModel, Field


class DAGEdge(BaseModel):
    """A directed edge from source node to target node."""
    source: str
    target: str


class TaskDAG(BaseModel):
    """
    Pipeline represented as a DAG.

    Nodes are identified by node_kind (e.g., "load_media", "split_shots").
    Edges represent dependencies: source must complete before target can start.
    """
    nodes: List[str] = Field(default_factory=list)
    edges: List[DAGEdge] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        self._rebuild_index()

    def _rebuild_index(self):
        """Build adjacency lists from edge list."""
        self._forward: Dict[str, List[str]] = defaultdict(list)
        self._reverse: Dict[str, List[str]] = defaultdict(list)
        for e in self.edges:
            self._forward[e.source].append(e.target)
            self._reverse[e.target].append(e.source)

    def get_direct_downstream(self, node_id: str) -> List[str]:
        """Get immediate downstream nodes."""
        return self._forward.get(node_id, [])

    def get_direct_upstream(self, node_id: str) -> List[str]:
        """Get immediate upstream nodes."""
        return self._reverse.get(node_id, [])

    def get_all_downstream(self, node_id: str) -> Set[str]:
        """Get transitive closure of all downstream nodes."""
        result = set()
        queue = deque(self.get_direct_downstream(node_id))
        while queue:
            n = queue.popleft()
            if n not in result:
                result.add(n)
                queue.extend(self.get_direct_downstream(n))
        return result

    def get_all_upstream(self, node_id: str) -> Set[str]:
        """Get transitive closure of all upstream nodes."""
        result = set()
        queue = deque(self.get_direct_upstream(node_id))
        while queue:
            n = queue.popleft()
            if n not in result:
                result.add(n)
                queue.extend(self.get_direct_upstream(n))
        return result

    def topological_sort(self) -> List[str]:
        """
        Kahn's algorithm topological sort.
        Raises ValueError if the graph contains a cycle.
        """
        in_degree: Dict[str, int] = {n: 0 for n in self.nodes}
        for e in self.edges:
            in_degree[e.target] = in_degree.get(e.target, 0) + 1

        queue = deque(n for n in self.nodes if in_degree.get(n, 0) == 0)
        result = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for child in self._forward.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self.nodes):
            raise ValueError(
                f"DAG contains a cycle. Sorted {len(result)} of {len(self.nodes)} nodes."
            )
        return result

    def compute_execution_plan(self, dirty_nodes: Set[str]) -> List[List[str]]:
        """
        Given a set of dirty nodes, compute the minimal incremental execution plan.

        Returns a list of layers, where each layer contains nodes that can run
        in parallel (all their upstream dependencies in this plan are in earlier layers).

        Only includes dirty nodes and their transitive downstream that are also dirty.
        """
        if not dirty_nodes:
            return []

        # Collect all nodes that need execution
        to_run = set(dirty_nodes)
        for n in list(dirty_nodes):
            to_run.update(self.get_all_downstream(n) & dirty_nodes)

        # Filter to only nodes that are actually in our dirty set
        # (the caller should have already propagated dirty status)
        to_run = to_run & dirty_nodes

        if not to_run:
            return []

        # Topological sort of the subgraph
        topo = self.topological_sort()
        node_layer: Dict[str, int] = {}
        layers: List[List[str]] = []

        for n in topo:
            if n not in to_run:
                continue

            # Layer = max(parent layers) + 1, or 0 if no parents in to_run
            parents_in_plan = [
                p for p in self.get_direct_upstream(n) if p in to_run
            ]
            if not parents_in_plan:
                layer = 0
            else:
                layer = max(node_layer[p] for p in parents_in_plan) + 1

            node_layer[n] = layer
            while len(layers) <= layer:
                layers.append([])
            layers[layer].append(n)

        return layers
