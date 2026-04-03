---
name: split-shots
description: >
  使用 TransNetV2 深度学习模型进行镜头边界检测，将视频按场景切分为独立片段。
  支持最小/最大镜头时长约束，使用 ffmpeg stream copy 无损切割。
  图片素材直接透传。
version: 1.0.0

pipeline:
  skill_id: split_shots
  display_name: 镜头切分
  depends_on: [load_media]
  next_skills: [understand_clips, asr]

concurrency:
  supports_batching: false

handler: handler.py
---

# 镜头切分 Split Shots

接收 load-media 输出的媒体列表，对每段视频执行 TransNetV2 场景检测，按检测到的镜头边界将视频切分为短片段。

## 处理逻辑

1. 从 `inputs["load_media"]["media"]` 读取上游媒体列表
2. 对每个媒体项：
   - **图片** → 直接生成一个 clip，不做切分
   - **视频时长 < min_shot_duration** → 跳过切分，整段作为一个 clip
   - **正常视频** →
     a. ffmpeg 解码为低分辨率帧序列（27×48，25fps）
     b. TransNetV2 模型推理，输出逐帧的场景切换概率
     c. 阈值过滤得到候选切分点（秒）
     d. 施加 min/max 时长约束：合并过短片段、均匀拆分过长片段
     e. ffmpeg segment `-c copy` 无损切割生成片段文件
3. 为每个片段分配递增 clip_id（`clip_0001`, `clip_0002`, ...）

## 时长约束

- `min_shot_duration`（默认 1000ms）：短于此值的片段会与相邻片段合并
- `max_shot_duration`（默认 30000ms）：长于此值的片段会被均匀拆分
- min > max 时自动回退到默认值

## 输入

```
load_media: dict          # 上游 load-media 的输出
  media: list[dict]       # 媒体列表

min_shot_duration: int    # 最小镜头时长 ms，可选，默认 1000
max_shot_duration: int    # 最大镜头时长 ms，可选，默认 30000
```

## 输出

```
clips: list[dict]
  clip_id:    str          # "clip_0001"
  kind:       str          # "video" | "image"
  path:       str          # 片段文件路径
  fps:        float | None # 帧率（仅视频）
  source_ref: dict         # 原始媒体溯源
    media_id:  str
    start:     int          # 起始时间 ms
    end:       int          # 结束时间 ms
    duration:  int          # 片段时长 ms
    height:    int | None
    width:     int | None
```

## 依赖

- TransNetV2 模型权重（路径配置在 `config.split_shots.transnet_weights`）
- ffmpeg 可执行文件（自动检测）
- PyTorch（推理）
