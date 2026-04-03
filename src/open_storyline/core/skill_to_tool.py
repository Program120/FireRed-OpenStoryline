"""
Bridge: convert SkillHandler-based skills into LangChain StructuredTools.

Each skill becomes a tool that the agent can call directly (in-process),
bypassing MCP. The tool wrapper handles:
  - Creating SkillContext from the agent's ClientContext
  - Running the handler (execute or default_execute based on mode)
  - Saving results to ArtifactStore (same format as MCP nodes)
  - Returning results in the same format the agent expects

The generated tools carry metadata compatible with NodeManager so that
existing dependency resolution (interceptors) works transparently.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from langchain_core.tools.structured import StructuredTool
from pydantic import BaseModel, Field

from open_storyline.core.skill import SkillContext, SkillHandler, SkillMeta, SkillResult
from open_storyline.core.skill_loader import SkillLoader
from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)


def _build_node_compatible_metadata(meta: SkillMeta) -> dict:
    """
    Build tool.metadata dict compatible with NodeManager._build().

    NodeManager expects metadata['_meta'] with:
      node_id, node_kind, require_prior_kind, default_require_prior_kind,
      next_available_node, priority
    """
    skill_id = meta.pipeline.skill_id or meta.name
    node_kind = meta.pipeline.node_kind or skill_id
    return {
        "_meta": {
            "node_id": skill_id,
            "node_kind": node_kind,
            "require_prior_kind": list(meta.pipeline.depends_on),
            "default_require_prior_kind": list(meta.pipeline.depends_on),
            "next_available_node": list(meta.pipeline.next_skills),
            "priority": 5,
        },
        "_is_skill": True,
    }


class _SkillToolInput(BaseModel):
    """Default input schema for skill tools exposed to the LLM.

    The LLM only needs to control ``mode`` and optionally pass free-form
    parameters.  Everything else (upstream data, session_id, config …) is
    injected by the skill middleware *after* the LLM produces its call.

    ``model_config`` allows extra fields so that middleware-injected data
    (inputs, split_shots, asr, session_id, …) passes through validation.
    """
    model_config = {"extra": "allow"}

    mode: Literal["auto", "skip", "default"] = Field(
        default="auto",
        description="auto: execute normally; skip/default: use fallback logic",
    )
    user_request: str = Field(
        default="",
        description="User's specific requirements for this step (optional)",
    )
    gap_threshold: int = Field(
        default=400,
        description="Gap threshold for grouping sentences (ms, only for speech_rough_cut)",
    )
    min_shot_duration: int = Field(
        default=1000,
        description="Minimum shot duration in ms (only for split_shots)",
    )
    max_shot_duration: int = Field(
        default=30000,
        description="Maximum shot duration in ms (only for split_shots)",
    )


def _json_default(obj: Any) -> Any:
    """Fallback serializer for non-JSON-serializable objects."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__str__"):
        return str(obj)
    return repr(obj)


def _make_tool_func(handler: SkillHandler, meta: SkillMeta):
    """
    Create the async callable that will be wrapped as a StructuredTool.

    The function signature uses explicit parameters (from _SkillToolInput)
    so the LLM sees a proper JSON Schema and knows what to pass.
    """
    skill_id = meta.pipeline.skill_id or meta.name

    async def _skill_tool_func(
        mode: str = "auto",
        user_request: str = "",
        gap_threshold: int = 400,
        min_shot_duration: int = 1000,
        max_shot_duration: int = 30000,
        # Framework-injected fields (added by middleware, invisible to LLM)
        artifact_id: str = "",
        session_id: str = "",
        lang: str = "zh",
        _config: Any = None,
        **extra_kwargs,
    ) -> str:
        if not artifact_id:
            artifact_id = f"{skill_id}_{time.time()}"

        config = getattr(handler, "_config", None)

        # Build LLM client (lazy, cached on handler).
        # Always rebuild if the cached client has wrong timeout settings.
        llm = getattr(handler, "_llm_client", None)
        if config is not None and (llm is None or getattr(llm, '_timeout', None) is None):
            from open_storyline.core.skill_llm import build_llm_from_config
            llm = build_llm_from_config(config)
            handler._llm_client = llm  # cache for reuse

        ctx = SkillContext(
            session_id=session_id,
            execution_id=artifact_id,
            lang=lang,
            config=config,
            llm=llm,
        )

        # Build the inputs dict from all arguments (middleware adds upstream data here)
        inputs = dict(extra_kwargs)
        if user_request:
            inputs["user_request"] = user_request
        if gap_threshold != 400:
            inputs["gap_threshold"] = gap_threshold
        if min_shot_duration != 1000:
            inputs["min_shot_duration"] = min_shot_duration
        if max_shot_duration != 30000:
            inputs["max_shot_duration"] = max_shot_duration

        try:
            if mode != "auto":
                result = await handler.default_execute(ctx, inputs)
            else:
                result = await handler.execute(ctx, inputs)

            output = {
                "artifact_id": artifact_id,
                "summary": {
                    "INFO_USER": "\n".join(
                        f"[{log.get('level', 'info')}] {log.get('message', '')}"
                        for log in result.logs
                    ) if result.logs else "",
                    "preview_urls": result.preview_urls,
                    "artifact_id": artifact_id,
                },
                "tool_excute_result": result.data,
                "isError": not result.success,
            }
        except Exception as e:
            logger.error(f"[Skill {skill_id}] execution failed: {traceback.format_exc()}")
            output = {
                "artifact_id": artifact_id,
                "summary": {"error_info": f"[artifact_id {artifact_id}]\n{traceback.format_exc()}"},
                "tool_excute_result": {},
                "isError": True,
            }

        return json.dumps(output, ensure_ascii=False, default=_json_default)

    return _skill_tool_func


def skill_to_langchain_tool(handler: SkillHandler, meta: SkillMeta) -> StructuredTool:
    """
    Convert a single SkillHandler into a LangChain StructuredTool.

    The tool is compatible with NodeManager and the existing interceptor chain.
    """
    skill_id = meta.pipeline.skill_id or meta.name
    func = _make_tool_func(handler, meta)

    tool = StructuredTool.from_function(
        coroutine=func,
        name=skill_id,
        description=meta.description,
        args_schema=_SkillToolInput,
        metadata=_build_node_compatible_metadata(meta),
    )
    return tool


def load_builtin_skill_tools(
    project_root: Path,
    config: Any = None,
) -> tuple[List[StructuredTool], SkillLoader]:
    """
    Discover builtin skills and convert them to LangChain tools.

    Args:
        project_root: Project root directory (where skills/ lives)
        config:       Settings object to inject into SkillContext

    Returns:
        (list of StructuredTools, SkillLoader instance)
    """
    loader = SkillLoader(project_root=project_root)
    loader.scan()

    tools: List[StructuredTool] = []

    for skill_id in loader.skill_ids:
        meta = loader.get_meta(skill_id)
        if meta is None:
            continue

        handler = loader.get_handler(skill_id)
        if handler is None:
            logger.info(f"Skill '{skill_id}' has no handler, skipping tool creation")
            continue

        # Inject config into handler for lazy model loading etc.
        handler._config = config

        tool = skill_to_langchain_tool(handler, meta)
        tools.append(tool)
        logger.info(f"Created LangChain tool for skill: {skill_id}")

    return tools, loader
