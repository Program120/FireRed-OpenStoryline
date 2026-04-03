"""
Core skill abstractions.

A Skill is a distributable, installable capability unit defined by SKILL.md + optional handler.py.
This module provides:
  - SkillMeta: parsed from SKILL.md YAML frontmatter
  - SkillContext: runtime context injected into handler execution
  - SkillResult: standardized return value from skill execution
  - SkillHandler: base class for Python-based skill handlers
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# SkillMeta — parsed from SKILL.md YAML frontmatter
# ---------------------------------------------------------------------------

@dataclass
class PipelineMeta:
    """Pipeline DAG metadata from SKILL.md frontmatter."""
    skill_id: str
    display_name: str = ""
    node_kind: str = ""     # NodeManager kind (defaults to skill_id if empty)
    depends_on: List[str] = field(default_factory=list)
    next_skills: List[str] = field(default_factory=list)


@dataclass
class ConcurrencyMeta:
    """Concurrency / batching config from SKILL.md frontmatter."""
    supports_batching: bool = False
    batch_key: str = ""
    default_batch_size: int = 1
    max_concurrency: int = 1
    allow_partial: bool = False
    max_retries: int = 2


@dataclass
class SkillMeta:
    """
    Complete metadata for a skill, parsed from SKILL.md YAML frontmatter.

    Attributes:
        name:           Skill name (kebab-case, e.g. "understand-clips")
        description:    Human-readable description
        version:        Semver string
        source:         Where the skill came from: builtin / installed / custom
        local_path:     Filesystem path to the skill directory
        handler_file:   Relative path to handler.py (None if pure-prompt skill)
        pipeline:       DAG orchestration metadata
        concurrency:    Batching / parallelism config
        body:           Markdown body of SKILL.md (after frontmatter)
    """
    name: str
    description: str = ""
    version: str = "0.0.0"
    source: str = "builtin"         # builtin | installed | custom
    local_path: Optional[Path] = None
    handler_file: Optional[str] = None  # e.g. "handler.py"
    pipeline: PipelineMeta = field(default_factory=lambda: PipelineMeta(skill_id=""))
    concurrency: ConcurrencyMeta = field(default_factory=ConcurrencyMeta)
    body: str = ""                  # markdown body of SKILL.md


# ---------------------------------------------------------------------------
# SkillContext — runtime context injected into every skill execution
# ---------------------------------------------------------------------------

@dataclass
class SkillContext:
    """
    Execution context passed to every SkillHandler.execute() call.

    Provides access to session state, LLM clients, storage, logging,
    and progress reporting — without the skill needing to know about
    the outer framework.
    """
    session_id: str
    execution_id: str
    lang: str = "zh"

    # -- injected services (set by executor before calling handler) --
    llm: Any = None             # LLM client (will be typed properly when agent layer is built)
    config: Any = None          # Settings object
    artifact_store: Any = None  # artifact storage
    db: Any = None              # async DB session (will be AsyncSession when DB layer is built)

    # -- logging & progress callbacks --
    _log_fn: Optional[Callable[..., Coroutine]] = field(default=None, repr=False)
    _progress_fn: Optional[Callable[..., Coroutine]] = field(default=None, repr=False)

    async def log(self, level: str, message: str, detail: str = "") -> None:
        """Send a log entry (real-time to frontend + DB when wired up)."""
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, f"[{self.execution_id}] {message}")
        if detail:
            logger.debug(f"[{self.execution_id}] detail: {detail[:2000]}")
        if self._log_fn:
            await self._log_fn(level=level, message=message, detail=detail)

    async def progress(self, pct: float, message: str = "") -> None:
        """Report progress (0.0 – 1.0) for frontend display."""
        logger.info(f"[{self.execution_id}] progress {pct:.0%} {message}")
        if self._progress_fn:
            await self._progress_fn(pct=pct, message=message)


# ---------------------------------------------------------------------------
# SkillResult — standardized return value
# ---------------------------------------------------------------------------

@dataclass
class SkillResult:
    """
    Standardized result returned by SkillHandler.execute().

    Attributes:
        success:    Whether execution succeeded
        data:       Output data dict (skill-specific)
        error:      Error message if success=False
        logs:       List of log dicts accumulated during execution
    """
    success: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    logs: List[Dict[str, Any]] = field(default_factory=list)
    preview_urls: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SkillHandler — base class for Python-backed skills
# ---------------------------------------------------------------------------

class SkillHandler(ABC):
    """
    Base class for skill handlers (handler.py).

    Subclasses MUST implement ``execute()``.
    Optionally override ``split_into_subtasks()`` and ``merge_subtask_results()``
    to enable batched parallel execution.

    The ``meta`` attribute is injected by SkillLoader after instantiation —
    handlers should NOT hardcode metadata; it always comes from SKILL.md.
    """

    meta: SkillMeta  # injected by SkillLoader

    # -- required --

    @abstractmethod
    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """
        Main execution entry point.

        Args:
            ctx:    Runtime context with session info, LLM client, DB, etc.
            inputs: Deserialized input dict (upstream skill outputs + user params).

        Returns:
            SkillResult with output data.
        """
        ...

    # -- optional: batching support --

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        """
        Split inputs into batches for parallel execution.

        Only called when ``concurrency.supports_batching`` is True in SKILL.md.
        Default: returns the full input as a single batch.
        """
        return [inputs]

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        """
        Merge results from parallel subtask executions.

        Default: returns the single result if only one, otherwise merges data dicts.
        """
        if len(results) == 1:
            return results[0]

        merged_data: Dict[str, Any] = {}
        all_logs: List[Dict[str, Any]] = []
        has_error = False

        for r in results:
            if not r.success:
                has_error = True
            for key, value in r.data.items():
                if key in merged_data and isinstance(merged_data[key], list) and isinstance(value, list):
                    merged_data[key].extend(value)
                else:
                    merged_data[key] = value
            all_logs.extend(r.logs)

        return SkillResult(
            success=not has_error,
            data=merged_data,
            error="partial failure in subtasks" if has_error else "",
            logs=all_logs,
        )

    # -- optional: prompt loading --

    def get_prompt(self, name: str) -> str:
        """
        Load a prompt template from the skill's prompts/ directory.

        Args:
            name: Template filename (without extension), e.g. "system"

        Returns:
            Prompt text content.
        """
        if self.meta.local_path is None:
            raise FileNotFoundError(f"Skill {self.meta.name} has no local_path set")

        prompt_dir = self.meta.local_path / "prompts"
        for ext in (".md", ".txt", ""):
            candidate = prompt_dir / f"{name}{ext}"
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")

        raise FileNotFoundError(
            f"Prompt '{name}' not found in {prompt_dir}"
        )

    # -- optional: default/skip behavior --

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """
        Fallback execution when mode is 'skip' or 'default'.
        Override this if the skill needs pass-through behavior.

        Default: returns empty success result.
        """
        return SkillResult(success=True, data={})
