"""
LangChain middleware for skill-based tools.

Handles the same responsibilities as MCP interceptors, but for in-process skill tools:
  - Before: inject session_id, config, media files, upstream dependencies
  - After: save results to ArtifactStore
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage

from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)


@wrap_tool_call
async def handle_skill_context(request, handler):
    """
    Middleware that injects context into skill tool calls and saves results.

    Only activates for tools with metadata['_is_skill'] = True.
    For non-skill tools, passes through to the next handler unchanged.
    """
    tool = request.tool
    metadata = getattr(tool, "metadata", None) or {}

    if not metadata.get("_is_skill"):
        return await handler(request)

    # Access runtime context
    runtime = request.runtime
    context = runtime.context
    store = runtime.store
    session_id = context.session_id
    client_cfg = getattr(context, "cfg", None)
    lang = getattr(context, "lang", "zh")

    tool_call = request.tool_call
    node_id = tool_call.get("name", "")
    args = dict(tool_call.get("args", {}))

    artifact_id = store.generate_artifact_id(node_id)

    # --- Before: inject context ---
    args["artifact_id"] = artifact_id
    args["session_id"] = session_id
    args["lang"] = lang
    # Note: _config is NOT injected into args to avoid JSON serialization issues.
    # Skill handlers get config via handler._config (set at load time).

    # For load_media: scan media dir and inject inputs
    if node_id == "load_media" and "inputs" not in args:
        media_dir = Path(context.media_dir)
        logger.info(f"[Skill middleware] load_media: scanning media_dir={media_dir} (exists={media_dir.is_dir()})")
        if media_dir.is_dir():
            all_files = [f for f in os.listdir(media_dir) if not (media_dir / f).is_dir()]
            logger.info(f"[Skill middleware] load_media: found {len(all_files)} files: {all_files[:10]}")
        inputs_list = []
        seen: set = set()

        if media_dir.is_dir():
            for fname in os.listdir(media_dir):
                fpath = media_dir / fname
                if fpath.is_dir():
                    continue
                abs_path = str(fpath.resolve())
                if abs_path not in seen:
                    seen.add(abs_path)
                    inputs_list.append({
                        "path": abs_path,
                    })

        # Also include search_media results if available
        latest_search = store.get_latest_meta(
            node_id="search_media", session_id=session_id
        )
        if latest_search:
            _, data = store.load_result(latest_search.artifact_id)
            if isinstance(data, dict):
                paths = data.get("payload", {}).get("search_media") or []
                for p in paths:
                    if isinstance(p, dict):
                        p = p.get("path")
                    if not p or not isinstance(p, str):
                        continue
                    norm = str(Path(p).resolve()) if not os.path.isabs(p) else p
                    if norm not in seen:
                        seen.add(norm)
                        inputs_list.append({"path": p})

        args["inputs"] = inputs_list
    else:
        # For other skills: load upstream dependencies
        meta_dict = metadata.get("_meta", {})
        require_kinds = meta_dict.get("require_prior_kind", [])

        is_skip = args.get("mode", "auto") != "auto"
        if is_skip:
            require_kinds = meta_dict.get("default_require_prior_kind", require_kinds)

        for kind in require_kinds:
            if kind in args:
                continue  # Already provided by the agent

            # Find the latest output for this kind
            # kind_to_node_ids mapping: kind is usually same as node_id for our skills
            node_manager = getattr(context, "node_manager", None)
            if node_manager is None:
                continue

            candidate_ids = node_manager.kind_to_node_ids.get(kind, [kind])
            for cid in candidate_ids:
                meta_entry = store.get_latest_meta(
                    node_id=cid, session_id=session_id
                )
                if meta_entry:
                    _, prior_data = store.load_result(meta_entry.artifact_id)
                    if isinstance(prior_data, dict) and "payload" in prior_data:
                        args[kind] = prior_data["payload"]
                        logger.info(
                            f"[Skill middleware] Injected upstream '{kind}' "
                            f"into '{node_id}'"
                        )
                    break

    # Update the tool call args
    tool_call["args"] = args

    # --- Execute ---
    out = await handler(request)

    # --- After: save result to ArtifactStore ---
    try:
        content = getattr(out, "content", None)
        if isinstance(content, str):
            result_data = json.loads(content)
        elif isinstance(content, dict):
            result_data = content
        else:
            result_data = None

        if result_data and isinstance(result_data, dict) and not result_data.get("isError"):
            store.save_result(
                session_id,
                node_id,
                result_data,
            )
            logger.info(f"[Skill middleware] Saved result for '{node_id}' to ArtifactStore")
    except Exception as e:
        logger.warning(f"[Skill middleware] Failed to save result for '{node_id}': {e}")

    return out
