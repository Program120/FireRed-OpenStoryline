"""
Batch executor for skill-level parallelism.

Provides two levels of concurrency control:
  1. Pipeline layer: multiple independent skills run in parallel (handled by orchestrator)
  2. Skill internal: a single skill splits inputs into subtasks and runs them in parallel

This module handles level 2 — skill-internal batching with semaphore-limited concurrency.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from open_storyline.core.skill import (
    SkillContext,
    SkillHandler,
    SkillResult,
)
from open_storyline.utils.logging import get_logger

logger = get_logger(__name__)

# Three-level semaphores to prevent resource exhaustion
GLOBAL_SEMAPHORE = asyncio.Semaphore(8)   # max concurrent tasks across entire pipeline
SKILL_SEMAPHORE = asyncio.Semaphore(4)    # max subtasks per skill
LLM_SEMAPHORE = asyncio.Semaphore(5)      # max concurrent LLM calls


class BatchExecutor:
    """
    Executes a skill with optional batched parallelism.

    If the skill's concurrency config has ``supports_batching=True`` and the
    handler implements ``split_into_subtasks()``, inputs are split into batches
    and executed in parallel (bounded by semaphores).

    Otherwise, ``handler.execute()`` is called once with the full inputs.
    """

    def __init__(
        self,
        global_semaphore: Optional[asyncio.Semaphore] = None,
        skill_semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        self._global_sem = global_semaphore or GLOBAL_SEMAPHORE
        self._skill_sem = skill_semaphore or SKILL_SEMAPHORE

    async def run(
        self,
        handler: SkillHandler,
        ctx: SkillContext,
        inputs: dict,
    ) -> SkillResult:
        """
        Execute a skill handler, with automatic batching if configured.

        Args:
            handler: The SkillHandler instance.
            ctx:     Execution context.
            inputs:  Input data dict.

        Returns:
            Merged SkillResult.
        """
        concurrency = handler.meta.concurrency

        if not concurrency.supports_batching:
            return await self._run_single(handler, ctx, inputs)

        # Split into subtasks
        subtask_inputs = await handler.split_into_subtasks(inputs)
        if len(subtask_inputs) <= 1:
            return await self._run_single(handler, ctx, inputs)

        total = len(subtask_inputs)
        logger.info(
            f"[{handler.meta.name}] Splitting into {total} subtasks "
            f"(max_concurrency={concurrency.max_concurrency})"
        )

        # Create a per-skill semaphore based on max_concurrency config
        batch_sem = asyncio.Semaphore(concurrency.max_concurrency)

        async def _run_subtask(index: int, sub_inputs: dict) -> SkillResult:
            async with self._global_sem:
                async with batch_sem:
                    try:
                        result = await handler.execute(ctx, sub_inputs)
                        await ctx.log(
                            "info",
                            f"Subtask {index + 1}/{total} completed",
                        )
                        await ctx.progress(
                            (index + 1) / total,
                            f"Batch {index + 1}/{total} done",
                        )
                        return result
                    except Exception as exc:
                        if concurrency.allow_partial:
                            logger.warning(
                                f"[{handler.meta.name}] Subtask {index + 1}/{total} "
                                f"failed (allow_partial=True): {exc}"
                            )
                            return SkillResult(
                                success=False,
                                error=str(exc),
                            )
                        raise

        # Run all subtasks with asyncio.gather
        try:
            results = await asyncio.gather(
                *[
                    _run_subtask(i, sub_inputs)
                    for i, sub_inputs in enumerate(subtask_inputs)
                ],
                return_exceptions=not concurrency.allow_partial,
            )
        except Exception as exc:
            logger.error(f"[{handler.meta.name}] Batch execution failed: {exc}")
            return SkillResult(success=False, error=str(exc))

        # Handle exceptions in results when allow_partial=True
        clean_results = []
        for r in results:
            if isinstance(r, Exception):
                clean_results.append(SkillResult(success=False, error=str(r)))
            else:
                clean_results.append(r)

        return await handler.merge_subtask_results(clean_results)

    async def _run_single(
        self,
        handler: SkillHandler,
        ctx: SkillContext,
        inputs: dict,
    ) -> SkillResult:
        """Run handler.execute() once with global semaphore."""
        async with self._global_sem:
            return await handler.execute(ctx, inputs)
