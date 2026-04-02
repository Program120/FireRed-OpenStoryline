# 核心抽象设计

---

## 1. BaseSkill（替代 BaseNode）

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

@dataclass
class SkillMeta:
    skill_id: str                      # "understand_clips"
    display_name: str                  # "画面理解"
    description: str
    depends_on: list[str] = field(default_factory=list)
    next_skills: list[str] = field(default_factory=list)
    supports_batching: bool = False
    default_batch_size: int = 5
    max_concurrency: int = 4

@dataclass
class SkillContext:
    session_id: str
    execution_id: str
    db: Any                            # AsyncSession
    llm: Any                           # LLMClient (multi-provider)
    artifact_store: Any
    progress_callback: Any             # async (pct, msg) -> None
    lang: str = "zh"

@dataclass
class SkillResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: Optional[str] = None
    subtask_results: list["SkillResult"] = field(default_factory=list)

class BaseSkill(ABC):
    meta: SkillMeta

    @abstractmethod
    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """执行 Skill 的核心逻辑。"""
        ...

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        """将输入拆分为子任务。仅 supports_batching=True 时需实现。"""
        return [inputs]

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        """合并并行子任务的结果。"""
        merged_data = {}
        for r in results:
            for k, v in r.data.items():
                if k in merged_data and isinstance(v, list):
                    merged_data[k].extend(v)
                else:
                    merged_data[k] = v
        return SkillResult(success=all(r.success for r in results), data=merged_data)
```

## 2. 哪些 Skill 需要并发

| Skill | 并发 | 拆分逻辑 | 合并逻辑 |
|-------|------|----------|----------|
| LoadMedia | 否 | — | — |
| SearchMedia | 否 | — | — |
| SplitShots | 否 | — | — |
| **LocalASR** | **是** | 按 clip 拆分（每 5 个 clip 一批） | 按 clip_id 顺序合并 asr_infos |
| SpeechRoughCut | 否 | 预筛选已优化为 1 次 LLM 调用 | — |
| **UnderstandClips** | **是** | 按 clip 拆分（每 5 个一批） | 按 clip_id 顺序合并 captions |
| FilterClips | 否 | 单次 LLM 调用 | — |
| GroupClips | 否 | 单次 LLM 调用 | — |
| ScriptTemplateRec | 否 | 向量搜索 | — |
| GenerateScript | 否 | 单次 LLM 调用 | — |
| **GenerateVoiceover** | **是** | 按 group 拆分（每 3 个一批） | 按 group_id 合并 voiceover 列表 |
| SelectBGM | 否 | 音频分析 + LLM | — |
| RecommendTransition | 否 | 单次 LLM 调用 | — |
| RecommendText | 否 | 单次 LLM 调用 | — |
| PlanTimeline | 否 | 算法编排 | — |
| RenderVideo | 否 | 单次 FFmpeg 调用 | — |

## 3. BatchExecutor（并发执行器）

```python
import asyncio

class BatchExecutor:
    def __init__(self, global_semaphore: asyncio.Semaphore):
        self.global_sem = global_semaphore  # 全局并发上限

    async def execute_skill(self, skill: BaseSkill, ctx: SkillContext, inputs: dict) -> SkillResult:
        if skill.meta.supports_batching:
            subtasks = await skill.split_into_subtasks(inputs)
            if len(subtasks) <= 1:
                return await self._run_single(skill, ctx, inputs)

            skill_sem = asyncio.Semaphore(skill.meta.max_concurrency)

            async def run_subtask(idx: int, sub_input: dict) -> SkillResult:
                async with self.global_sem, skill_sem:
                    sub_ctx = SkillContext(
                        session_id=ctx.session_id,
                        execution_id=f"{ctx.execution_id}_batch_{idx}",
                        db=ctx.db, llm=ctx.llm,
                        artifact_store=ctx.artifact_store,
                        progress_callback=ctx.progress_callback,
                        lang=ctx.lang,
                    )
                    return await skill.execute(sub_ctx, sub_input)

            results = await asyncio.gather(
                *[run_subtask(i, sub) for i, sub in enumerate(subtasks)],
                return_exceptions=True,
            )

            # 处理异常
            skill_results = []
            for r in results:
                if isinstance(r, Exception):
                    skill_results.append(SkillResult(success=False, error=str(r)))
                else:
                    skill_results.append(r)

            return await skill.merge_subtask_results(skill_results)
        else:
            return await self._run_single(skill, ctx, inputs)

    async def _run_single(self, skill, ctx, inputs):
        return await skill.execute(ctx, inputs)
```

## 4. Pipeline（DAG 定义）

```python
class Pipeline:
    """技能执行管道，基于 DAG 自动推导执行层。"""

    def __init__(self, skills: list[BaseSkill]):
        self.skills = {s.meta.skill_id: s for s in skills}
        self.dag = self._build_dag(skills)

    def _build_dag(self, skills) -> TaskDAG:
        """从 Skill 的 depends_on 自动构建 DAG。"""
        ...

    def compute_plan(self, dirty_skills: set[str]) -> list[list[str]]:
        """计算增量执行计划（分层并行）。"""
        return self.dag.compute_execution_plan(dirty_skills)
```

## 5. Session（会话模型）

```python
@dataclass
class Session:
    session_id: str
    display_name: str
    status: str                    # active / paused / completed
    lang: str
    config: dict                   # model config snapshot
    created_at: datetime
    updated_at: datetime

    # 运行时（从 DB 加载）
    pipeline_version: int
    skill_states: dict[str, SkillExecutionState]
    chat_history: list[ChatMessage]
    media_files: list[MediaFile]
```

Session 的完整状态存在 DB 中。前端通过 REST API 加载 Session snapshot，通过 WebSocket 接收实时更新。浏览器刷新后从 DB 恢复到精确状态。
