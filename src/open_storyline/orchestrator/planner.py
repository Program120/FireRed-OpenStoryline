"""
V1 Orchestrator: rule-based Planner + concurrent Worker.

The Planner maps user intents to dirty node_kinds using keyword matching,
then uses ProjectState.mark_dirty() for propagation and TaskDAG.compute_execution_plan()
for layered execution planning.

The Worker executes nodes layer by layer with asyncio concurrency.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from open_storyline.state.project_state import ProjectState, NodeStatus
from open_storyline.state.task_dag import TaskDAG
from open_storyline.state.default_pipeline import build_default_dag
from open_storyline.storage.agent_memory import ArtifactStore
from open_storyline.mcp.hooks.node_interceptors import (
    compress_payload_to_base64,
    should_inline_media_as_base64,
)
from open_storyline.utils.logging import get_logger

if TYPE_CHECKING:
    from open_storyline.nodes.node_manager import NodeManager

logger = get_logger(__name__)


# ── Intent → dirty node_kinds mapping (rule-based) ──────────────

# Each rule is (compiled_regex, set_of_node_kinds_to_dirty).
# First match wins; if nothing matches → full pipeline.
_INTENT_RULES: List[Tuple[re.Pattern, List[str]]] = [
    (re.compile(r"[换改].*(?:BGM|bgm|音乐|配乐|背景音)"), ["music_rec"]),
    (re.compile(r"(?:换|改|重新).*(?:配音|语音|旁白|朗读|TTS|tts)"), ["tts"]),
    (re.compile(r"(?:改|换|重写|重新写|修改).*(?:文案|脚本|字幕|台词|文本)"), ["generate_script"]),
    (re.compile(r"(?:换|调|改).*(?:顺序|排列|排序|时间线)"), ["plan_timeline"]),
    (re.compile(r"(?:重新渲染|重新导出|再渲染|再导出|渲染|导出)"), ["render"]),
    (re.compile(r"(?:换|删|加|增|减|移除).*(?:片段|镜头|视频|素材|画面)"), ["filter_clips"]),
]


class Planner:
    """
    Rule-based intent recogniser.

    Analyses user text to determine which pipeline node_kinds should be
    marked dirty. Falls back to full pipeline rebuild when no rule matches.
    """

    def __init__(self, dag: TaskDAG) -> None:
        self.dag = dag

    def recognise_dirty_kinds(self, user_text: str) -> List[str]:
        """
        Return the list of node_kinds that should be marked dirty based
        on the user's editing instruction.

        If no pattern matches, returns ["load_media"] to trigger a full
        pipeline re-run from the root.
        """
        for pattern, kinds in _INTENT_RULES:
            if pattern.search(user_text):
                logger.info(f"[Planner] Matched intent rule → dirty kinds: {kinds}")
                return kinds
        logger.info("[Planner] No intent matched → full pipeline from load_media")
        return ["load_media"]

    def plan(
        self,
        user_text: str,
        project_state: ProjectState,
    ) -> List[List[str]]:
        """
        High-level entry: recognise intent → mark dirty → compute layers.

        Returns a list of layers (each layer = list of node_kinds that can
        execute in parallel).
        """
        dirty_kinds = self.recognise_dirty_kinds(user_text)

        # Mark dirty + propagate downstream
        all_dirty: Set[str] = set()
        for kind in dirty_kinds:
            dirtied = project_state.mark_dirty(kind, self.dag)
            all_dirty.update(dirtied)

        # Also include any already-pending / dirty nodes
        for nid, snap in project_state.nodes.items():
            if snap.status in (NodeStatus.DIRTY, NodeStatus.PENDING):
                all_dirty.add(nid)

        layers = self.dag.compute_execution_plan(all_dirty)
        logger.info(f"[Planner] Execution plan ({len(layers)} layers): {layers}")
        return layers


# ── Worker: concurrent layer-by-layer executor ──────────────────

class Worker:
    """
    Executes a layered plan produced by the Planner.

    Within each layer nodes run concurrently (up to *max_parallel* at a time).
    Each node execution:
      1. Assembles input_data by loading upstream artifacts (same pattern as
         ``ToolInterceptor.inject_media_content_before``).
      2. Computes input_hash; skips execution when hash is unchanged.
      3. Calls the MCP tool via ``tool.ainvoke()``.
      4. Updates ProjectState with the result.
    """

    def __init__(
        self,
        node_manager: "NodeManager",
        store: ArtifactStore,
        project_state: ProjectState,
        dag: TaskDAG,
        session_id: str,
        *,
        client_cfg: Any = None,
        media_dir: Optional[str] = None,
        lang: str = "zh",
        max_parallel: int = 3,
        context: Any = None,
    ) -> None:
        self.node_manager = node_manager
        self.store = store
        self.project_state = project_state
        self.dag = dag
        self.session_id = session_id
        self.client_cfg = client_cfg
        self.media_dir = media_dir
        self.lang = lang
        self.max_parallel = max_parallel
        self.context = context
        self._semaphore = asyncio.Semaphore(max_parallel)

    # ── input assembly (mirrors inject_media_content_before) ─────

    def _load_collected_data(
        self, collected_node: Dict[str, Any], input_data: Dict[str, Any]
    ) -> None:
        """Load prior node outputs into *input_data* keyed by node_kind."""
        for kind, artifact_meta in collected_node.items():
            _, prior_output = self.store.load_result(artifact_meta.artifact_id)
            compress_payload_to_base64(prior_output["payload"], self.client_cfg)
            input_data[kind] = prior_output["payload"]

    def _assemble_load_media_input(self) -> Dict[str, Any]:
        """Build the ``inputs`` list for the *load_media* root node."""
        import os

        input_data: Dict[str, Any] = {"inputs": []}
        inline_base64 = should_inline_media_as_base64(self.client_cfg)
        seen_paths: set = set()
        media_dir = Path(self.media_dir) if self.media_dir else None

        if media_dir is None or not media_dir.exists():
            return input_data

        try:
            project_media_root = Path(self.client_cfg.project.media_dir).resolve()
        except Exception:
            project_media_root = None

        from open_storyline.storage.file import FileCompressor

        for file_name in os.listdir(media_dir):
            path = media_dir / file_name
            if path.is_dir():
                continue
            if inline_base64:
                rel_path = str(path.relative_to(os.getcwd()))
                compress_data = FileCompressor.compress_and_encode(path)
                input_data["inputs"].append({
                    "path": rel_path,
                    "base64": compress_data.base64,
                    "md5": compress_data.md5,
                })
            else:
                abs_path = path.resolve()
                if project_media_root is not None:
                    try:
                        rel_or_abs = str(abs_path.relative_to(project_media_root))
                    except ValueError:
                        rel_or_abs = str(abs_path)
                else:
                    rel_or_abs = str(abs_path)

                if rel_or_abs not in seen_paths:
                    seen_paths.add(rel_or_abs)
                    input_data["inputs"].append({
                        "path": rel_or_abs,
                        "orig_path": rel_or_abs,
                        "orig_md5": None,
                    })

        # Include auto-searched media (path-only mode)
        if not inline_base64:
            latest_search = self.store.get_latest_meta(
                node_id="search_media", session_id=self.session_id
            )
            if latest_search:
                _, data = self.store.load_result(latest_search.artifact_id)
                if isinstance(data, dict):
                    paths = data.get("payload", {}).get("search_media") or []
                    for p in paths:
                        if isinstance(p, dict):
                            p = p.get("path")
                        if not p or not isinstance(p, str):
                            continue
                        norm = str(Path(p).resolve()) if not os.path.isabs(p) else p
                        if norm in seen_paths:
                            continue
                        seen_paths.add(norm)
                        input_data["inputs"].append({
                            "path": p,
                            "orig_path": p,
                            "orig_md5": None,
                        })

        return input_data

    def _assemble_node_input(self, node_id: str) -> Dict[str, Any]:
        """
        Assemble the full input dict for *node_id*, loading upstream
        artifacts exactly like ``inject_media_content_before``.
        """
        if node_id == "load_media":
            return self._assemble_load_media_input()

        input_data: Dict[str, Any] = {}

        if node_id in self.node_manager.id_to_tool:
            # Use default_require (we always run in 'default' mode)
            require_kind = self.node_manager.id_to_default_require_prior_kind.get(
                node_id, []
            )
            collect_result = self.node_manager.check_excutable(
                self.session_id, self.store, require_kind
            )
            self._load_collected_data(collect_result["collected_node"], input_data)
        else:
            input_data["artifacts_dir"] = self.store.artifacts_dir

        return input_data

    # ── single node execution ────────────────────────────────────

    async def _execute_node(self, node_id: str) -> bool:
        """
        Execute a single pipeline node.

        Returns True on success, False on failure.
        """
        async with self._semaphore:
            logger.info(f"[Worker] Starting node '{node_id}'")
            self.project_state.update_node(node_id, NodeStatus.RUNNING)

            try:
                # 1. Assemble input
                input_data = self._assemble_node_input(node_id)
                artifact_id = self.store.generate_artifact_id(node_id)

                # 2. Compute input hash & skip if unchanged
                input_hash = ProjectState.compute_input_hash(input_data)
                snap = self.project_state.ensure_node(node_id)
                if (
                    snap.input_hash == input_hash
                    and snap.artifact_id is not None
                    and snap.status != NodeStatus.PENDING
                ):
                    logger.info(
                        f"[Worker] Skipping '{node_id}' — input hash unchanged"
                    )
                    self.project_state.update_node(
                        node_id, NodeStatus.COMPLETED, input_hash=input_hash
                    )
                    return True

                # 3. Call the MCP tool
                tool = self.node_manager.get_tool(node_id)
                if tool is None:
                    raise RuntimeError(f"No tool registered for node '{node_id}'")

                tool_call_input: Dict[str, Any] = {
                    "artifact_id": artifact_id,
                    "lang": self.lang,
                    "mode": "default",
                }
                tool_call_input.update(input_data)

                # Use tool.ainvoke which goes through the normal MCP path
                result = await tool.ainvoke(tool_call_input)

                # 4. Parse result
                if isinstance(result, str):
                    result_data = json.loads(result)
                elif hasattr(result, "content"):
                    content = result.content
                    if isinstance(content, str):
                        result_data = json.loads(content)
                    elif isinstance(content, list) and content:
                        result_data = json.loads(content[0].get("text", "{}"))
                    else:
                        result_data = {}
                else:
                    result_data = result if isinstance(result, dict) else {}

                is_error = result_data.get("isError", False)

                if is_error:
                    error_msg = result_data.get("summary", "unknown error")
                    logger.error(f"[Worker] Node '{node_id}' returned error: {error_msg}")
                    self.project_state.update_node(
                        node_id,
                        NodeStatus.FAILED,
                        artifact_id=artifact_id,
                        input_hash=input_hash,
                        error_msg=str(error_msg),
                    )
                    return False

                # 5. Save result to artifact store
                self.store.save_result(self.session_id, node_id, result_data)

                # 6. Update project state
                self.project_state.update_node(
                    node_id,
                    NodeStatus.COMPLETED,
                    artifact_id=artifact_id,
                    input_hash=input_hash,
                )
                self.store.save_project_state(self.project_state)
                logger.info(f"[Worker] Node '{node_id}' completed successfully")
                return True

            except Exception as e:
                logger.error(
                    f"[Worker] Node '{node_id}' failed: {''.join(traceback.format_exception(e))}"
                )
                self.project_state.update_node(
                    node_id,
                    NodeStatus.FAILED,
                    error_msg=str(e),
                )
                return False

    # ── layer-by-layer execution ─────────────────────────────────

    async def execute_plan(self, layers: List[List[str]]) -> Dict[str, bool]:
        """
        Execute a layered plan. Returns ``{node_id: success}`` for every node.
        """
        results: Dict[str, bool] = {}
        for layer_idx, layer in enumerate(layers):
            logger.info(
                f"[Worker] Executing layer {layer_idx + 1}/{len(layers)}: {layer}"
            )
            tasks = [self._execute_node(nid) for nid in layer]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for nid, outcome in zip(layer, outcomes):
                if isinstance(outcome, Exception):
                    logger.error(f"[Worker] Node '{nid}' raised: {outcome}")
                    results[nid] = False
                else:
                    results[nid] = bool(outcome)

            # If any node in this layer failed, downstream layers will likely
            # fail too — but we continue to let independent branches proceed.
        return results


# ── LangGraph StateGraph builder ─────────────────────────────────

def build_orchestrator(
    node_manager: "NodeManager",
    store: ArtifactStore,
    session_id: str,
    *,
    client_cfg: Any = None,
    media_dir: Optional[str] = None,
    lang: str = "zh",
    max_parallel: int = 3,
    context: Any = None,
) -> Dict[str, Any]:
    """
    Build the V1 orchestrator components.

    Returns a dict with ``planner``, ``worker``, ``dag``, and ``project_state``
    so the caller can drive the plan-then-execute loop.

    The LangGraph StateGraph is intentionally kept lightweight: it wraps the
    planner + worker in a single compiled graph with a ``plan`` node and an
    ``execute`` node connected sequentially.
    """
    dag = build_default_dag()

    # Load or create project state
    project_state = store.load_project_state()
    if project_state is None:
        project_state = ProjectState(
            project_id=session_id,
            session_id=session_id,
        )
        # Initialise snapshots for all DAG nodes
        for nid in dag.nodes:
            project_state.ensure_node(nid)
        store.save_project_state(project_state)

    planner = Planner(dag)
    worker = Worker(
        node_manager=node_manager,
        store=store,
        project_state=project_state,
        dag=dag,
        session_id=session_id,
        client_cfg=client_cfg,
        media_dir=media_dir,
        lang=lang,
        max_parallel=max_parallel,
        context=context,
    )

    # Build a minimal LangGraph StateGraph
    try:
        from langgraph.graph import StateGraph, END
        from typing import TypedDict

        class OrchestratorState(TypedDict):
            user_text: str
            layers: List[List[str]]
            results: Dict[str, bool]
            error: Optional[str]

        async def plan_node(state: OrchestratorState) -> dict:
            user_text = state["user_text"]
            try:
                layers = planner.plan(user_text, project_state)
                return {"layers": layers, "error": None}
            except Exception as e:
                logger.error(f"[Orchestrator] Planning failed: {e}")
                return {"layers": [], "error": str(e)}

        async def execute_node(state: OrchestratorState) -> dict:
            layers = state.get("layers", [])
            if not layers:
                return {"results": {}, "error": state.get("error")}
            try:
                results = await worker.execute_plan(layers)
                return {"results": results, "error": None}
            except Exception as e:
                logger.error(f"[Orchestrator] Execution failed: {e}")
                return {"results": {}, "error": str(e)}

        graph = StateGraph(OrchestratorState)
        graph.add_node("plan", plan_node)
        graph.add_node("execute", execute_node)
        graph.set_entry_point("plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", END)

        compiled = graph.compile()
    except Exception as e:
        logger.warning(f"[Orchestrator] LangGraph compilation failed: {e}; "
                       "falling back to direct planner+worker usage")
        compiled = None

    return {
        "planner": planner,
        "worker": worker,
        "dag": dag,
        "project_state": project_state,
        "graph": compiled,
    }
