"""
V1 Project State: persistent, structured state for incremental editing.

Each node's execution status is tracked as a NodeSnapshot.
When a node's inputs change, it and all downstream nodes are marked DIRTY.
Only DIRTY/PENDING nodes need re-execution.
"""

from __future__ import annotations

import hashlib
import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from open_storyline.state.task_dag import TaskDAG


class NodeStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    DIRTY = "dirty"
    RUNNING = "running"
    FAILED = "failed"


class NodeSnapshot(BaseModel):
    """Execution state of a single pipeline node."""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    artifact_id: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    completed_at: Optional[float] = None
    error_msg: Optional[str] = None


class ProjectState(BaseModel):
    """
    Global project state for a single editing session.

    Tracks which nodes have been executed, their outputs (via artifact_id),
    and user overrides for incremental editing.
    """
    project_id: str
    session_id: str
    version: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    nodes: Dict[str, NodeSnapshot] = Field(default_factory=dict)
    user_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    def ensure_node(self, node_id: str) -> NodeSnapshot:
        """Get or create a NodeSnapshot for the given node."""
        if node_id not in self.nodes:
            self.nodes[node_id] = NodeSnapshot(node_id=node_id)
        return self.nodes[node_id]

    def mark_dirty(self, node_id: str, dag: "TaskDAG") -> List[str]:
        """
        Mark a node and all its downstream dependents as DIRTY.
        Returns the list of newly-dirtied node IDs.
        """
        dirty_list = []
        queue = [node_id]
        visited = set()

        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)

            snap = self.ensure_node(nid)
            if snap.status in (NodeStatus.COMPLETED, NodeStatus.FAILED):
                snap.status = NodeStatus.DIRTY
                dirty_list.append(nid)
            elif snap.status == NodeStatus.PENDING:
                dirty_list.append(nid)

            for downstream in dag.get_direct_downstream(nid):
                queue.append(downstream)

        if dirty_list:
            self.version += 1
            self.updated_at = time.time()

        return dirty_list

    def get_runnable_nodes(self) -> List[str]:
        """Return all nodes that need execution (DIRTY or PENDING)."""
        return [
            nid for nid, snap in self.nodes.items()
            if snap.status in (NodeStatus.DIRTY, NodeStatus.PENDING)
        ]

    def update_node(
        self,
        node_id: str,
        status: NodeStatus,
        artifact_id: Optional[str] = None,
        input_hash: Optional[str] = None,
        output_hash: Optional[str] = None,
        error_msg: Optional[str] = None,
    ):
        """Update a node's snapshot after execution."""
        snap = self.ensure_node(node_id)
        snap.status = status
        if artifact_id is not None:
            snap.artifact_id = artifact_id
        if input_hash is not None:
            snap.input_hash = input_hash
        if output_hash is not None:
            snap.output_hash = output_hash
        if error_msg is not None:
            snap.error_msg = error_msg
        if status == NodeStatus.COMPLETED:
            snap.completed_at = time.time()
        self.updated_at = time.time()

    @staticmethod
    def compute_input_hash(params: Dict[str, Any]) -> str:
        """
        Compute a stable hash of input parameters.
        Ignores volatile fields (artifact_id, base64 blobs, lang).
        Used to detect whether a node actually needs re-execution.
        """
        skip_keys = {"base64", "artifact_id", "lang", "mode", "session_id"}
        filtered = {k: v for k, v in params.items() if k not in skip_keys}
        raw = json.dumps(filtered, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
