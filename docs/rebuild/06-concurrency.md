# Skill 并发模型

---

## 两级并发

### 第一级：Pipeline 层级并发

DAG 的同一层中的 Skill 可以并行执行。例如：

```
Layer 1: [load_media]            ← 串行（只有一个）
Layer 2: [split_shots]           ← 串行
Layer 3: [asr, understand_clips] ← 并行！两个 Skill 同时执行
Layer 4: [speech_rough_cut, filter_clips] ← 并行
...
```

这一层由 **Executor** 控制，使用 `asyncio.gather` 并行分发同层 Skill。

### 第二级：Skill 内部并发

支持 batching 的 Skill 可以将输入拆分为子任务并行执行。例如：

```
UnderstandClips (20 个片段):
  batch_size=5, max_concurrency=4
  → 拆分为 4 个子任务，每个处理 5 个片段
  → 4 个子任务通过 asyncio.gather 并行执行
  → 合并 4 个子结果为最终输出
```

这一层由 **BatchExecutor** 控制。

### 组合效果

```
Layer 3: [asr(20clips), understand_clips(20clips)]
         ↓                ↓
     4 batches ×5      4 batches ×5
     并行执行            并行执行
     (Semaphore=4)      (Semaphore=4)

总效果: 8 个并发 worker 同时运行
       (受全局 Semaphore 限制)
```

## 信号量控制

```python
# 三层信号量防止资源耗尽

GLOBAL_SEMAPHORE = asyncio.Semaphore(8)      # 全局最大并发数
# ↓
SKILL_SEMAPHORE = asyncio.Semaphore(4)       # 单个 Skill 最大并发子任务
# ↓
LLM_SEMAPHORE = asyncio.Semaphore(5)         # LLM API 调用并发限制
```

`GLOBAL_SEMAPHORE` 保证不会同时跑太多任务导致内存/CPU 爆掉。`SKILL_SEMAPHORE` 防止一个 Skill 独占所有 worker。`LLM_SEMAPHORE` 配合 API 的 rate limit。

## 示例：UnderstandClips 并发实现

```python
class UnderstandClipsSkill(BaseSkill):
    meta = SkillMeta(
        skill_id="understand_clips",
        display_name="画面理解",
        depends_on=["load_media", "split_shots"],
        supports_batching=True,
        default_batch_size=5,
        max_concurrency=4,
    )

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        clips = inputs.get("clips", [])
        media = inputs.get("media", {})
        batches = []
        for i in range(0, len(clips), self.meta.default_batch_size):
            batch_clips = clips[i:i + self.meta.default_batch_size]
            batches.append({"clips": batch_clips, "media": media})
        return batches

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        clips = inputs.get("clips", [])
        captions = []
        for clip in clips:
            # 直接调 LLM (不走 MCP)
            response = await ctx.llm.complete_vision(
                system_prompt=self._get_system_prompt(ctx.lang),
                user_prompt=self._get_user_prompt(clip),
                media=[{"path": clip["path"]}],
            )
            captions.append({"clip_id": clip["clip_id"], "caption": response})

            # 记录 LLM 交互到 DB
            await ctx.db.add(LLMInteraction(
                execution_id=ctx.execution_id,
                session_id=ctx.session_id,
                provider=ctx.llm.provider,
                model=ctx.llm.model,
                role="vlm",
                user_prompt=self._get_user_prompt(clip)[:500],
                response_text=response[:500],
            ))

        return SkillResult(success=True, data={"clip_captions": captions})

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        all_captions = []
        for r in results:
            all_captions.extend(r.data.get("clip_captions", []))
        # 按 clip_id 排序保证顺序一致
        all_captions.sort(key=lambda c: c["clip_id"])
        return SkillResult(success=True, data={"clip_captions": all_captions})
```

## 进度上报

并行执行时，进度通过 `SkillContext.progress_callback` 实时推送：

```python
# Executor 内部
completed_subtasks = 0

async def on_subtask_done(batch_idx):
    nonlocal completed_subtasks
    completed_subtasks += 1
    pct = completed_subtasks / total_batches
    await ctx.progress_callback(pct, f"批次 {completed_subtasks}/{total_batches} 完成")
    # 同时推送 WebSocket 事件
    await ws_send("skill.subtask_complete", {
        "skill_id": skill.meta.skill_id,
        "batch_index": batch_idx,
        "batch_total": total_batches,
    })
```

## 与当前方案的对比

| 维度 | 当前 (V0/V1) | 重建后 |
|------|-------------|--------|
| Pipeline 并行 | V1 DAG 分层但未接入实际执行 | Executor 真正并行执行同层 Skill |
| Skill 内并行 | 无（串行 for 循环） | BatchExecutor 拆子任务并行 |
| LLM 并发 | MCP 串行限制 | 直接 async 调用，Semaphore 控制 |
| 进度上报 | report_progress hack | 直接 WebSocket 推送 |
| 资源控制 | 无 | 三层 Semaphore |
