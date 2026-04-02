# MCP 工具参考手册

> 本文档列出 FireRed-OpenStoryline 中所有注册的 MCP 工具（节点），包括功能说明、输入输出、依赖关系和核心逻辑。

---

## 概览

系统共有 **16 个核心处理节点** + **2 个辅助工具** = **18 个 MCP 工具**。

### 默认启用的节点

在 `config.toml` 的 `[local_mcp_server].available_nodes` 中配置：

```
LoadMediaNode, SearchMediaNode, SplitShotsNode, LocalASRNode, SpeechRoughCutNode,
UnderstandClipsNode, FilterClipsNode, GroupClipsNode, GenerateScriptNode,
ScriptTemplateRecomendation, GenerateVoiceoverNode, SelectBGMNode,
RecommendTransitionNode, RecommendTextNode, PlanTimelineProNode, RenderVideoNode
```

### 工具分类一览

| 分类 | 工具名 | 中文名 | node_kind |
|------|--------|--------|-----------|
| 素材输入 | `load_media` | 加载素材 | `load_media` |
| 素材输入 | `search_media` | 在线搜索素材 | `search_media` |
| 视频分析 | `split_shots` | 镜头分割 | `split_shots` |
| 视频分析 | `understand_clips` | 画面理解 | `understand_clips` |
| 语音处理 | `local_asr` | 语音识别 | `asr` |
| 语音处理 | `speech_rough_cut` | 口播粗剪 | `speech_rough_cut` |
| 内容编排 | `filter_clips` | 片段筛选 | `filter_clips` |
| 内容编排 | `group_clips` | 片段分组 | `group_clips` |
| 内容编排 | `script_template_rec` | 文案模板推荐 | `script_template_rec` |
| 内容生成 | `generate_script` | 文案生成 | `generate_script` |
| 内容生成 | `generate_voiceover` | 语音合成 | `tts` |
| 推荐系统 | `select_bgm` | 背景音乐选择 | `music_rec` |
| 推荐系统 | `elementrec_transition` | 转场推荐 | `transition_rec` |
| 推荐系统 | `elementrec_text` | 字体推荐 | `text_rec` |
| 合成输出 | `plan_timeline_pro` | 时间线编排 | `plan_timeline` |
| 合成输出 | `render_video` | 视频渲染 | `render` |
| 辅助工具 | `read_node_history` | 读取历史结果 | — |
| 辅助工具 | `write_skills` | 保存技能 | — |

---

## 一、素材输入类

### 1. load_media — 加载素材

| 属性 | 值 |
|------|-----|
| **类名** | `LoadMediaNode` |
| **文件** | `core_nodes/load_media.py` |
| **node_id** | `load_media` |
| **node_kind** | `load_media` |
| **前置依赖** | 无（入口节点） |
| **下游节点** | `split_shots`, `split_shots_pro` |

**功能说明：** 
流水线的入口节点。扫描并索引用户上传的所有媒体文件，提取元数据。

**输入参数 (`LoadMediaInput`)：**
- `media` — 媒体文件列表，每个包含 path、type（video/image）等
- `mode` — 执行模式：`auto` / `default` / `skip`

**输出：**
```json
{
  "media": [
    {
      "media_id": "media_0001",
      "path": "outputs/media/video1.mp4",
      "type": "video",
      "metadata": {
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "duration": 120.5,
        "audio_sample_rate": 44100
      }
    }
  ]
}
```

**核心逻辑：**
- 通过 `av.open()` 读取视频元数据（帧率、分辨率、时长、音频采样率）
- 通过 PIL 读取图片元数据（宽高）
- 按顺序生成 `media_0001`, `media_0002` 等 ID
- 支持的视频格式：`.mp4`, `.mov`, `.avi`, `.mkv`
- 支持的图片格式：`.jpg`, `.png`, `.webp`

---

### 2. search_media — 在线搜索素材

| 属性 | 值 |
|------|-----|
| **类名** | `SearchMediaNode` |
| **文件** | `core_nodes/search_media.py` |
| **node_id** | `search_media` |
| **node_kind** | `search_media` |
| **前置依赖** | 无 |
| **下游节点** | `load_media` |

**功能说明：** 
通过 Pexels API 在线搜索并下载匹配的图片和视频素材。

**输入参数 (`SearchMediaInput`)：**
- `query` — 搜索关键词
- `media_type` — 搜索类型：`video` / `photo`
- `orientation` — 方向过滤：`landscape` / `portrait` / `square`
- `per_page` — 每页结果数
- `min_duration` / `max_duration` — 视频时长过滤（秒）

**输出：**
```json
{
  "search_media": [
    {"path": "outputs/media/pexels_12345.mp4", "type": "video"}
  ]
}
```

**核心逻辑：**
- 调用 Pexels API 搜索视频/图片
- 按时长、方向等条件过滤
- 下载匹配的媒体文件到 `media_dir`
- 需要在 `config.toml` 中配置 `pexels_api_key`

---

## 二、视频分析类

### 3. split_shots — 镜头分割

| 属性 | 值 |
|------|-----|
| **类名** | `SplitShotsNode` |
| **文件** | `core_nodes/split_shots.py` |
| **node_id** | `split_shots` |
| **node_kind** | `split_shots` |
| **前置依赖** | `load_media` |
| **下游节点** | `understand_clips`, `understand_clips_pro` |

**功能说明：** 
基于场景边界检测，将完整视频自动分割为多个镜头片段。

**输入参数 (`SplitShotsInput`)：**
- `media` — 已加载的媒体列表
- `mode` — 执行模式

**输出：**
```json
{
  "clips": [
    {
      "clip_id": "clip_0001",
      "path": "outputs/session/split_shots/clip_0001.mp4",
      "fps": 30.0,
      "source_ref": {
        "media_id": "media_0001",
        "start": 0,
        "end": 3500,
        "duration": 3500
      }
    }
  ]
}
```

**核心逻辑：**
- 使用 **TransNetV2** 预训练模型进行场景检测
- 将视频帧缩放到 48x27（模型输入尺寸）
- 通过 `model.predict_raw()` 获取场景边界预测
- 应用最小/最大镜头时长约束
- 使用 FFmpeg `stream_copy`（无损切割）提取片段
- 图片素材保持原样通过

**配置项：**
```toml
[split_shots]
transnet_weights = ".storyline/models/transnetv2-pytorch-weights.pth"
transnet_device = "cpu"  # 或 "cuda"
```

---

### 4. understand_clips — 画面理解

| 属性 | 值 |
|------|-----|
| **类名** | `UnderstandClipsNode` |
| **文件** | `core_nodes/understand_clips.py` |
| **node_id** | `understand_clips` |
| **node_kind** | `understand_clips` |
| **前置依赖** | `load_media`, `split_shots` |
| **下游节点** | `filter_clips`, `filter_clips_pro` |

**功能说明：** 
调用 VLM（视觉语言模型）对每个视频片段/图片进行画面理解，生成文字描述。

**输入参数 (`UnderstandClipsInput`)：**
- `media` — 原始媒体列表
- `clips` — 分割后的片段列表
- `mode` — 执行模式

**输出：**
```json
{
  "clip_captions": [
    {"clip_id": "clip_0001", "caption": "一位女性在海边散步，背景是夕阳"},
    {"clip_id": "clip_0002", "caption": "特写镜头：桌上摆放的咖啡杯"}
  ],
  "overall": "这是一段关于海边旅行和咖啡时光的视频素材"
}
```

**核心逻辑：**
- 对每个片段调用 VLM 进行多模态理解
- 视频片段：按 2fps 抽帧，最多 64 帧
- 图片：直接传入 VLM
- 使用 `prompts/tasks/understand_clips/` 下的提示词模板
- 失败时重试最多 2 次，兜底返回 "no caption"
- 同时生成整体内容摘要 `overall`

**配置项：**
```toml
[understand_clips]
sample_fps = 2.0        # 每秒抽几帧
max_frames = 64          # 单 clip 最大帧数
```

---

## 三、语音处理类

### 5. local_asr — 语音识别

| 属性 | 值 |
|------|-----|
| **类名** | `LocalASRNode` |
| **文件** | `core_nodes/asr_node.py` |
| **node_id** | `local_asr` |
| **node_kind** | `asr` |
| **前置依赖** | `split_shots` |
| **下游节点** | `group_clips` |

**功能说明：** 
使用 FunASR 模型对视频音频进行本地语音识别，生成带时间戳的文字转录。

**输入参数 (`LocalASRInput`)：**
- `clips` — 片段列表
- `mode` — 执行模式

**输出：** ASR 识别结果，包含每段文字的起止时间戳

**核心逻辑：**
- 使用 FunASR 的 `paraformer-zh` 模型
- 先用 FFmpeg 从视频中提取音频并降噪
- 对每个有音频的片段进行语音识别
- 输出带时间戳的转录文本
- 跳过无音频的片段和图片

---

### 6. speech_rough_cut — 口播粗剪

| 属性 | 值 |
|------|-----|
| **类名** | `SpeechRoughCutNode` |
| **文件** | `core_nodes/speech_rough_cut.py` |
| **node_id** | `speech_rough_cut` |
| **node_kind** | `speech_rough_cut` |
| **前置依赖** | `asr` |
| **下游节点** | 无 |

**功能说明：** 
基于 ASR 结果，自动去除口播视频中的语气词、重复句、停顿等，实现智能粗剪。

**输入参数 (`SpeechRoughCutInput`)：**
- `clips` — 片段列表
- `asr_results` — ASR 识别结果
- `mode` — 执行模式

**输出：** 切割后的片段列表和校准后的 ASR 时间戳

**核心逻辑：**
- LLM 分析 ASR 文本，判断哪些句子需要保留/删除
- 按间隙阈值对句子进行分组
- 添加缓冲区并计算切割点
- 使用 FFmpeg 执行视频分割
- 删除片段后重新校准 ASR 时间戳

---

## 四、内容编排类

### 7. filter_clips — 片段筛选

| 属性 | 值 |
|------|-----|
| **类名** | `FilterClipsNode` |
| **文件** | `core_nodes/filter_clips.py` |
| **node_id** | `filter_clips` |
| **node_kind** | `filter_clips` |
| **前置依赖** | `split_shots`, `understand_clips` |
| **下游节点** | `group_clips`, `group_clips_pro` |

**功能说明：** 
根据用户需求和画面描述，由 LLM 筛选出相关的视频片段。

**输入参数 (`FilterClipsInput`)：**
- `user_request` — 用户的筛选需求描述
- `clip_captions` — 各片段的画面描述
- `clips` — 片段列表
- `mode` — 执行模式

**输出：**
```json
{
  "clip_captions": [...],
  "selected": ["clip_0001", "clip_0003", "clip_0005"]
}
```

**核心逻辑：**
- 将所有片段描述和用户需求传给 LLM
- LLM 输出每个片段的 `keep: true/false` 判断
- 保持原始片段顺序不变
- 使用 `prompts/tasks/filter_clips/` 下的提示词
- 若未指定筛选条件，默认返回全部片段

---

### 8. group_clips — 片段分组

| 属性 | 值 |
|------|-----|
| **类名** | `GroupClipsNode` |
| **文件** | `core_nodes/group_clips.py` |
| **node_id** | `group_clips` |
| **node_kind** | `group_clips` |
| **前置依赖** | `filter_clips` |
| **下游节点** | `generate_script`, `generate_script_pro` |

**功能说明：** 
由 LLM 将筛选后的片段按叙事逻辑分组，形成视频的段落结构。

**输入参数 (`GroupClipsInput`)：**
- `user_request` — 用户需求
- `clip_captions` — 片段描述
- `selected` — 已筛选的 clip_id 列表
- `mode` — 执行模式

**输出：**
```json
{
  "groups": [
    {
      "group_id": "group_0001",
      "summary": "海边漫步场景",
      "clip_ids": ["clip_0001", "clip_0003"],
      "duration": 7200
    }
  ]
}
```

**核心逻辑：**
- LLM 按语义/叙事相似性对片段分组
- 跨组去重 clip_id（同一片段不会出现在多个组中）
- 按顺序重命名 group_id：`group_0001`, `group_0002`, ...
- JSON 解析失败时，自动扩展 Token 预算重试
- 全部失败时降级为单组

**配置项：**
```toml
[group_clips]
base_max_tokens = 4096       # 基础输出 Token 预算
tokens_per_clip = 48          # 每个 clip 额外 Token
max_tokens_cap = 16384        # Token 上限
retry_token_step = 2048       # 每次重试增加的 Token
max_parse_retries = 2         # 最大重试次数
```

---

### 9. script_template_rec — 文案模板推荐

| 属性 | 值 |
|------|-----|
| **类名** | `ScriptTemplateRecomendation` |
| **文件** | `core_nodes/script_template_rec.py` |
| **node_id** | `script_template_rec` |
| **node_kind** | `script_template_rec` |
| **前置依赖** | 无 |
| **下游节点** | `generate_script` |

**功能说明：** 
通过向量相似度搜索，为用户推荐匹配的文案风格模板（如种草、Vlog、测评等）。

**输入参数 (`RecommendScriptTemplateInput`)：**
- `user_request` — 用户需求描述
- `filter_include` — 包含标签过滤
- `filter_exclude` — 排除标签过滤

**输出：**
```json
{
  "candidates": [
    {"template_id": "tmpl_001", "name": "种草风", "score": 0.85},
    {"template_id": "tmpl_002", "name": "Vlog 口播", "score": 0.78}
  ]
}
```

**核心逻辑：**
- 使用 SentenceTransformers + FAISS 进行向量相似度搜索
- 从 `resource/script_templates/meta.json` 加载模板库
- 按标签进行包含/排除过滤
- 返回 Top-3 推荐结果

**配置项：**
```toml
[script_template]
script_template_dir = "./resource/script_templates"
script_template_info_path = "./resource/script_templates/meta.json"
```

---

## 五、内容生成类

### 10. generate_script — 文案生成

| 属性 | 值 |
|------|-----|
| **类名** | `GenerateScriptNode` |
| **文件** | `core_nodes/generate_script.py` |
| **node_id** | `generate_script` |
| **node_kind** | `generate_script` |
| **前置依赖** | `split_shots`, `group_clips`, `understand_clips` |
| **默认前置依赖** | `split_shots`, `group_clips` |
| **下游节点** | `generate_voiceover` |

**功能说明：** 
由 LLM 为每个片段分组生成字幕文案，支持多种风格（抒情、幽默、口语化等）。

**输入参数 (`GenerateScriptInput`)：**
- `user_request` — 用户需求（包含风格要求）
- `groups` — 分组列表
- `clip_captions` — 片段描述
- `custom_script` — 用户自定义文案（可选，覆盖 LLM 生成）
- `script_template` — 文案模板（可选）
- `mode` — 执行模式

**输出：**
```json
{
  "group_scripts": [
    {
      "group_id": "group_0001",
      "raw_text": "阳光洒在海面上，每一步都踩着浪花的节拍",
      "subtitle_units": [
        {"unit_id": "su_0001", "index_in_group": 0, "text": "阳光洒在海面上"},
        {"unit_id": "su_0002", "index_in_group": 1, "text": "每一步都踩着浪花的节拍"}
      ]
    }
  ],
  "title": "海边的慢时光"
}
```

**核心逻辑：**
- 使用 `prompts/tasks/generate_script/` 下的提示词
- 支持 `custom_script` 覆盖 LLM 输出（用户手动编辑文案）
- 将完整文案按字/词分割为 `SubtitleUnit` 列表
- 同时生成视频标题
- 失败时返回空文案

---

### 11. generate_voiceover — 语音合成

| 属性 | 值 |
|------|-----|
| **类名** | `GenerateVoiceoverNode` |
| **文件** | `core_nodes/generate_voiceover.py` |
| **node_id** | `generate_voiceover` |
| **node_kind** | `tts` |
| **前置依赖** | `group_clips`, `generate_script` |
| **下游节点** | 无 |

**功能说明：** 
调用 TTS 服务将文案合成为语音配音文件。

**输入参数 (`GenerateVoiceoverInput`)：**
- `user_request` — 用户需求（用于推断音色、语速等）
- `group_scripts` — 分组文案列表
- `tts_provider` — TTS 服务商
- `voice_type` — 音色类型
- `speed` — 语速
- `mode` — 执行模式

**输出：**
```json
{
  "voiceover": [
    {
      "group_id": "group_0001",
      "voiceover_id": "vo_0001",
      "path": "outputs/session/tts/vo_0001.wav",
      "duration": 4500
    }
  ]
}
```

**核心逻辑：**
- 支持三个 TTS 服务商：
  - **MiniMax**（推荐）— `api.minimax.chat`
  - **字节跳动**（推荐）— 火山引擎 TTS
  - **302.ai** — 备选
- LLM 根据用户需求推断 TTS 参数（音色、语速、情感）
- 为每个 `group_script` 调用 TTS 生成 WAV 文件
- 使用 librosa 计算音频时长
- 支持服务商降级容错

**配置项：**
```toml
[generate_voiceover]
tts_provider_params_path = "./resource/tts/tts_providers.json"

[generate_voiceover.providers.minimax]
base_url = ""
api_key = ""

[generate_voiceover.providers.bytedance]
uid = ""
appid = ""
access_token = ""

[generate_voiceover.providers.302]
base_url = ""
api_key = ""
```

---

## 六、推荐系统类

### 12. select_bgm — 背景音乐选择

| 属性 | 值 |
|------|-----|
| **类名** | `SelectBGMNode` |
| **文件** | `core_nodes/select_bgm.py` |
| **node_id** | `select_bgm` |
| **node_kind** | `music_rec` |
| **前置依赖** | 无 |
| **下游节点** | `plan_timeline` |

**功能说明：** 
通过语义搜索和 LLM 选择，从音乐库中推荐最匹配的背景音乐，并分析节拍信息。

**输入参数 (`SelectBGMInput`)：**
- `user_request` — 用户需求描述（如"轻快的旅行风"）
- `filter_include` — 包含标签
- `filter_exclude` — 排除标签

**输出：**
```json
{
  "bgm": {
    "bgm_id": "bgm_001",
    "path": "resource/bgms/travel_light.mp3",
    "duration": 180000,
    "bpm": 120.0,
    "beats": [500, 1000, 1500, 2000, ...]
  }
}
```

**核心逻辑：**
- 使用 SentenceTransformers + FAISS 从 BGM 库语义搜索 Top-N 候选
- 按标签过滤候选
- LLM 从候选中选择最匹配的音乐
- 使用 librosa 分析选中音乐的特征：
  - BPM（每分钟拍数）
  - beats[]（每个鼓点的毫秒时间戳）
  - RMS 能量
  - 重音节拍

**配置项：**
```toml
[select_bgm]
sample_rate = 22050
hop_length = 2048
frame_length = 2048
```

---

### 13. elementrec_transition — 转场推荐

| 属性 | 值 |
|------|-----|
| **类名** | `RecommendTransitionNode` |
| **文件** | `core_nodes/recommend_effects.py` |
| **node_id** | `elementrec_transition` |
| **node_kind** | `transition_rec` |
| **前置依赖** | `group_clips` |
| **默认前置依赖** | 无 |
| **下游节点** | `plan_timeline` |

**功能说明：** 
根据用户需求和段落数量，推荐转场效果。

**输入参数 (`RecommendTransitionInput`)：**
- `user_request` — 用户需求
- `n_groups` — 段落数量

**输出：**
```json
[
  {"type": "fade_in", "position": "start", "duration": 500},
  {"type": "fade_out", "position": "end", "duration": 500}
]
```

**核心逻辑：**
- 默认返回淡入（片头）+ 淡出（片尾）
- 转场时长可配置

---

### 14. elementrec_text — 字体推荐

| 属性 | 值 |
|------|-----|
| **类名** | `RecommendTextNode` |
| **文件** | `core_nodes/recommend_effects.py` |
| **node_id** | `elementrec_text` |
| **node_kind** | `text_rec` |
| **前置依赖** | `generate_script` |
| **默认前置依赖** | 无 |
| **下游节点** | `plan_timeline` |

**功能说明：** 
根据文案内容和用户需求，推荐合适的字体、颜色和样式。

**输入参数 (`RecommendTextInput`)：**
- `user_request` — 用户需求
- `group_scripts` — 文案内容

**输出：**
```json
[
  {
    "font_name": "HarmonyOS Sans SC",
    "font_path": "resource/fonts/HarmonyOS_Sans_SC.ttf",
    "font_color": [255, 255, 255]
  }
]
```

**核心逻辑：**
- 使用 `ElementFilter` 过滤可用字体
- LLM 根据文案风格选择最匹配的字体
- 返回字体名称、路径和颜色信息

**配置项：**
```toml
[recommend_text]
font_info_path = "resource/fonts/font_info.json"
```

---

## 七、合成输出类

### 15. plan_timeline_pro — 时间线编排

| 属性 | 值 |
|------|-----|
| **类名** | `PlanTimelineProNode` |
| **文件** | `core_nodes/plan_timeline_pro.py` |
| **node_id** | `plan_timeline_pro` |
| **node_kind** | `plan_timeline` |
| **前置依赖** | `load_media`, `split_shots`, `group_clips`, `generate_script` |
| **下游节点** | `render_video` |

**功能说明：** 
将视频片段、字幕、配音、背景音乐同步编排到统一时间线上。这是渲染前的最后一步数据准备。

**输入参数 (`PlanTimelineInput`)：**
- `media` — 原始素材
- `clips` — 片段列表
- `groups` — 分组信息
- `group_scripts` — 文案
- `voiceover` — 配音文件（可选）
- `bgm` — 背景音乐（可选）
- `transitions` — 转场效果（可选）
- `text_style` — 字体样式（可选）
- `mode` — 执行模式

**输出：**
```json
{
  "timeline": {
    "video": [
      {"clip_id": "clip_0001", "source_window": {"start": 0, "end": 3500}, "timeline_window": {"start": 0, "end": 3500}}
    ],
    "subtitles": [
      {"text": "阳光洒在海面上", "timeline_window": {"start": 0, "end": 2000}}
    ],
    "voiceover": [
      {"media_id": "vo_0001", "timeline_window": {"start": 0, "end": 4500}}
    ],
    "bgm": [
      {"bgm_id": "bgm_001", "timeline_window": {"start": 0, "end": 30000}}
    ]
  }
}
```

**核心逻辑：**
- 按组顺序排列视频片段
- 配音存在时：以配音时长为基准对齐视频和字幕
- 无配音时：按估算的字幕阅读时长对齐
- 节拍对齐：片段切换点对齐到 BGM 鼓点
- BGM 不够长时自动循环
- 计算组间间距（margin）

**配置项：**
```toml
[plan_timeline_pro]
min_single_text_duration = 200    # 单段文字最小时长 (ms)
max_text_duration = 5000          # 单句文字最大时长 (ms)
img_default_duration = 1500       # 图片默认时长 (ms)
min_group_margin = 1500           # 组间最小间距 (ms)
max_group_margin = 2000           # 组间最大间距 (ms)
min_clip_duration = 1000          # 最小片段时长 (ms)
tts_margin_mode = "random"        # TTS 间距模式
text_duration_mode = "with_tts"   # 字幕时长跟随配音
```

---

### 16. render_video — 视频渲染

| 属性 | 值 |
|------|-----|
| **类名** | `RenderVideoNode` |
| **文件** | `core_nodes/render_video.py` |
| **node_id** | `render_video` |
| **node_kind** | `render` |
| **前置依赖** | `plan_timeline` |
| **下游节点** | 无（最终输出） |

**功能说明：** 
将时间线数据合成为最终的 MP4 视频文件。这是流水线的最后一步。

**输入参数 (`RenderVideoInput`)：**
- `timeline` — TimelineTracks 时间线数据
- `output_aspect_ratio` — 输出宽高比（默认 16:9）
- `subtitle_params` — 字幕参数（字号、颜色等）
- `mode` — 执行模式

**输出：**
```json
{
  "video_path": "outputs/session_xxx/render/final_video.mp4"
}
```

**核心逻辑：**
- 使用 **MoviePy** 进行视频合成
- 创建画布：按目标宽高比和分辨率（默认 1080p）
- 视频轨：按时间线排列，缩放/填充保持比例，黑色信箱
- 字幕轨：用 PIL 生成字幕图片，叠加到画面底部
- 音频混合：
  - 原声：1.0x 音量
  - 配音：2.0x 音量
  - BGM：0.25x 音量（降低避免干扰人声）
- 使用 FFmpeg 编码：
  - 视频编码：libx264, preset=veryfast, crf=23
  - 音频编码：AAC
  - 帧率：25fps

---

## 八、辅助工具

### 17. read_node_history — 读取历史结果

| 属性 | 值 |
|------|-----|
| **注册位置** | `mcp/register_tools.py` |
| **类型** | 特殊工具（非 BaseNode） |

**功能说明：** 
通过 `artifact_id` 查询任意节点的历史执行结果。

**输入参数：**
- `query_artifact_id: str` — 要查询的制品 ID

**输出：**
```json
{
  "history": {
    "meta": {"node_id": "split_shots", "created_at": "2026-04-01T..."},
    "node_data": { ... }
  }
}
```

**使用场景：** Agent 在对话过程中回溯查看之前节点的执行结果。

---

### 18. write_skills — 保存技能

| 属性 | 值 |
|------|-----|
| **注册位置** | `mcp/register_tools.py` |
| **类型** | 特殊工具（非 BaseNode） |

**功能说明：** 
将 LLM 生成的 Agent 技能（Markdown 格式）保存到文件系统。

**输入参数：**
- `skill_name: str` — 技能文件名（不含扩展名）
- `skill_dir: str` — 保存目录（默认 `.storyline/skills/`）
- `skill_content: str` — 技能内容（Markdown 格式）

**输出：** 保存成功后返回文件的绝对路径

**使用场景：** 用户在对话中让 Agent 总结出一个可复用的剪辑工作流技能。

---

## 九、依赖关系总览

```
                    ┌─────────────────┐
                    │  search_media   │ (无依赖)
                    └───────┬─────────┘
                            ▼
┌───────────┐     ┌─────────────────┐
│           │     │   load_media    │ ◄── 入口节点
│ select_bgm│     └───────┬─────────┘
│ (无依赖)  │             │
└─────┬─────┘             ▼
      │           ┌─────────────────┐
      │           │  split_shots    │
      │           └──┬──────────┬───┘
      │              │          │
      │              ▼          ▼
      │     ┌──────────────┐ ┌──────────┐
      │     │understand_   │ │local_asr │
      │     │   clips      │ └────┬─────┘
      │     └──────┬───────┘      ▼
      │            │        ┌───────────────┐
      │            ▼        │speech_rough_cut│
      │     ┌──────────────┐└───────────────┘
      │     │ filter_clips │
      │     └──────┬───────┘
      │            ▼
      │     ┌──────────────┐   ┌───────────────────┐
      │     │ group_clips  │   │script_template_rec │ (无依赖)
      │     └──────┬───────┘   └─────────┬─────────┘
      │            │                     │
      │            ▼                     ▼
      │     ┌──────────────────────────────┐
      │     │       generate_script        │
      │     └──────┬───────────────────────┘
      │            │              │
      │            ▼              ▼
      │  ┌─────────────────┐ ┌───────────────┐ ┌─────────────────┐
      │  │generate_voiceover│ │elementrec_text│ │elementrec_      │
      │  └────────┬────────┘ └──────┬────────┘ │  transition     │
      │           │                 │           └────────┬────────┘
      │           ▼                 ▼                    │
      │     ┌───────────────────────────────────────────┐│
      └────►│          plan_timeline_pro                ││
            └───────────────────┬───────────────────────┘│
                                ▼                        │
                        ┌──────────────┐                 │
                        │ render_video │◄────────────────┘
                        └──────┬───────┘
                               ▼
                           最终 MP4
```

---

## 十、如何新增一个 MCP 工具

1. **创建节点文件** `src/open_storyline/nodes/core_nodes/my_node.py`
2. **定义输入输出 Schema** 在 `node_schema.py` 中添加 Pydantic 模型
3. **实现节点类**：

```python
from open_storyline.nodes.core_nodes.base_node import BaseNode, NodeMeta
from open_storyline.utils.register import NODE_REGISTRY

@NODE_REGISTRY.register
class MyNode(BaseNode):
    meta = NodeMeta(
        name="my_tool",
        description="工具功能描述",
        node_id="my_tool",
        node_kind="my_kind",
        require_prior_kind=["load_media"],      # auto 模式的前置依赖
        default_require_prior_kind=[],           # default 模式的前置依赖
        next_available_node=["next_node"],       # 下游推荐节点
        priority=5,                              # 同类节点中的优先级
    )
    input_schema = MyInput

    async def process(self, node_state, inputs):
        """核心处理逻辑"""
        result = ...
        return {"my_output": result}

    async def default_process(self, node_state, inputs):
        """跳过/默认模式的处理逻辑"""
        return {"my_output": default_result}
```

4. **在 config.toml 中启用**：将类名添加到 `available_nodes` 列表

```toml
[local_mcp_server]
available_nodes = [
    ...,
    "MyNode"
]
```

5. **重启 MCP Server** 即可自动注册为 MCP 工具
