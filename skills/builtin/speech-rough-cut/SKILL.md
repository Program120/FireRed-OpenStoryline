---
name: speech-rough-cut
description: >
  基于 ASR 识别结果，使用 LLM 对语音内容进行智能粗剪。
  采用两阶段策略：先批量预筛选需要处理的句子，再对标记句子进行精细编辑（清洗口水词、
  删除重复、时间戳对齐）。最终通过 ffmpeg 无损切割输出清理后的视频片段，并校准 ASR 时间戳。
version: 1.0.0

pipeline:
  skill_id: speech_rough_cut
  display_name: 语音粗剪
  depends_on: [asr, speech_rough_cut]
  next_skills: []

concurrency:
  supports_batching: false

handler: handler.py
---

# 语音粗剪 Speech Rough Cut

根据 ASR 识别出的句子级时间戳，使用 LLM 判断哪些句子需要清洗（口水词、重复、无意义内容），
然后用 ffmpeg 按保留的句子时间范围切割视频，输出粗剪后的片段。

## 两阶段处理策略

这个技能采用两阶段 LLM 调用来平衡质量和效率：

1. **预筛选（Phase 1）**：将所有句子一次性发给 LLM，快速标记需要处理的句子编号，
   大幅减少后续 LLM 调用次数（从 N 次降到 1 + M 次，M << N）
2. **精细编辑（Phase 2）**：只对被标记的句子逐一调用 LLM，执行文本清洗、
   时间戳对齐和拆分操作

## 处理逻辑

1. 从 `inputs["asr"]["asr_infos"]` 获取 ASR 结果
2. 对每个 clip 的 asr_sentence_info：
   a. Phase 1: 一次 LLM 调用批量预筛选
   b. Phase 2: 对标记句子逐一精细处理（使用 prompts/tasks/speech_rough_cut/ 模板）
   c. 未标记句子直接保留
3. 将处理后的句子按间隔阈值（gap_threshold）分组
4. ffmpeg 按分组范围切割视频
5. 校准 ASR 时间戳（减去被删除的时间段）

## 输入

```
asr: dict                      # 上游 local-asr 的输出
  asr_infos: list[dict]

speech_rough_cut: dict          # 可选，历史粗剪结果（多轮编辑场景）
  rough_cut_jsons: list

user_request: str               # 用户对粗剪的要求
gap_threshold: int              # 句子分组间隔阈值 ms，默认 400
```

## 输出

```
clips: list[dict]               # 粗剪后的视频片段
  clip_id, kind, path, fps, source_ref

rough_cut_jsons: list[list]     # 每个 clip 的粗剪 JSON（保留的句子列表）
```

## 依赖

- LLM（通过 ctx.llm 调用）
- ffmpeg
- prompts/tasks/speech_rough_cut/ 下的 prompt 模板
