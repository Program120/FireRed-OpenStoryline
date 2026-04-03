---
name: load-media
description: >
  加载并索引用户上传的媒体文件（视频、图片），提取分辨率、时长、帧率、音频等元数据。
  这是整个剪辑流程的入口技能，无上游依赖，所有下游技能都需要它的输出。
version: 1.0.0

pipeline:
  skill_id: load_media
  display_name: 素材加载
  depends_on: []
  next_skills: [split_shots]

concurrency:
  supports_batching: false

handler: handler.py
---

# 素材加载 Load Media

读取用户提供的媒体文件列表，逐一检测文件类型并提取元数据，为后续剪辑流程提供结构化的素材索引。

## 处理逻辑

1. 遍历 `inputs` 列表中的每个文件路径
2. 根据文件扩展名判断类型：
   - 视频（`.mp4` `.mov` `.mkv` `.avi`）→ 用 PyAV 读取时长、分辨率、帧率、音频采样率，并通过 ffprobe 检测旋转角度修正宽高
   - 图片（`.jpg` `.jpeg` `.png` `.webp` `.bmp`）→ 用 Pillow 读取分辨率，自动处理 EXIF 旋转
   - 其他格式 → 跳过并记录警告
3. 为每个有效媒体分配递增 ID（`media_0001`, `media_0002`, ...）
4. 统计视频/图片数量并输出汇总日志

## 输入

```
inputs: list[dict]
  每个元素: { "path": "媒体文件的绝对路径" }
```

## 输出

```
media: list[dict]
  每个元素:
    media_id:   str        # "media_0001"
    path:       str        # 文件绝对路径
    media_type: str        # "video" | "image"
    metadata:   dict       # 视频: {duration, width, height, fps, has_audio, audio_sample_rate_hz}
                           # 图片: {width, height}
```

## 边界情况

- 输入列表为空 → 返回空 `media` 列表，不报错
- 文件不存在或无法读取 → 由 PyAV/Pillow 抛出异常，上层处理
- 视频无视频流 → 抛出 ValueError
- 90°/270° 旋转的视频 → 宽高自动交换
