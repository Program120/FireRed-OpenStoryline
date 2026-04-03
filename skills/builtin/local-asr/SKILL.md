---
name: local-asr
description: >
  使用 FunASR（paraformer-zh）模型对视频片段进行本地语音识别，提取每个片段的文本内容、
  字级时间戳和句子级时间戳信息。自动跳过图片和无音轨的视频片段。
  该技能是语音粗剪（speech-rough-cut）的前置依赖。
version: 1.0.0

pipeline:
  skill_id: local_asr
  node_kind: asr
  display_name: 语音识别
  depends_on: [split_shots]
  next_skills: [group_clips, speech_rough_cut]

concurrency:
  supports_batching: true
  batch_key: clips
  default_batch_size: 4
  max_concurrency: 2

handler: handler.py
---

# 语音识别 Local ASR

对 split-shots 输出的视频片段逐一提取音频并运行语音识别，为下游的文案生成、语音粗剪等技能提供文本基础。

## 处理逻辑

1. 从 `inputs["split_shots"]["clips"]` 获取片段列表
2. 对每个片段：
   - **图片或非视频** → 跳过，输出空 ASR 结果
   - **视频无音轨**（通过 ffprobe 检测） → 跳过
   - **视频有音轨** →
     a. ffmpeg 提取音频为 16kHz 单声道 WAV，同时应用降噪滤镜（afftdn + agate）
     b. FunASR paraformer-zh 推理，输出文本 + 句子级时间戳
3. 整理输出格式：统一 clip_id、path、asr_text、asr_timestamps、asr_sentence_info

## 输入

```
split_shots: dict
  clips: list[dict]    # 上游 split-shots 输出的片段列表
```

## 输出

```
asr_infos: list[dict]
  clip_id:            str
  kind:               str          # "video" | "image"
  path:               str
  asr_text:           str          # 完整识别文本
  asr_timestamps:     list         # 字级时间戳
  asr_sentence_info:  list[dict]   # 句子级信息 [{text, start, end}, ...]
  source_ref:         dict
  fps:                float
```

## 边界情况

- 片段列表为空 → 返回空 `asr_infos`
- 视频无音轨 → 该片段 asr_text 为空字符串，asr_sentence_info 为空列表
- FunASR 返回空结果 → 同上处理

## 依赖

- FunASR（paraformer-zh + fsmn-vad + ct-punc）
- ffmpeg / ffprobe
