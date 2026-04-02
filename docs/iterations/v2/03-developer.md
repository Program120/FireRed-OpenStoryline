# V2 研发技术方案

---

## 一、DAG 架构变更

V2 新增 5 种节点类型：`smart_crop`、`aspect_adapt`、`color_grade`、`transition`、`clip_embed`。

关键变化——多比例派生：
```
segment_1:render → segment_1:adapt_16_9 → concat_16_9
                 → segment_1:adapt_9_16 → concat_9_16
                 → segment_1:adapt_1_1  → concat_1_1
```

---

## 二、P0-1 多平台自适应渲染

### Smart Crop Engine

**模型选型：YOLOv8n (ultralytics)**
- CPU 推理 ~15ms/帧，模型 6MB
- 关键帧采样 2fps（30s 片段 = 60 帧推理 ≈ 1s）
- 无主体检出时 fallback 到画面中心

```python
class SmartCropEngine:
    def detect_keyframes(self, video_path) -> list[CropRegion]: ...
    def interpolate_crop_path(self, keyframes, target_fps) -> list[CropRegion]: ...
    def to_ffmpeg_crop_filter(self, regions, src_w, src_h, target_ratio) -> str: ...
```

**关键帧策略：** 2fps 采样 + 场景切换点额外插入关键帧（直方图差异检测）。裁切路径高斯平滑避免抖动。

### Layout Adaptation Rules

```python
LAYOUT_PRESETS = {
    "16:9": {"subtitle_safe_zone": {...}, "max_subtitle_width_ratio": 0.80},
    "9:16": {"subtitle_safe_zone": {...}, "shot_duration_multiplier": 0.75},
    "1:1":  {"subtitle_safe_zone": {...}, "max_subtitle_width_ratio": 0.85},
}
```

竖屏节奏调整：segment 时长乘以 `shot_duration_multiplier`，尾部裁掉（非加速）。语音被截断时保留原时长，标记 `needs_review`。

### 增量渲染

adapt 节点的 input_hash 包含 crop_region + layout_rule，参数变化才 dirty。手动调某个比例的裁切只标该 adapt 节点 dirty。

### 三画布预览

API：`GET /preview/{session_id}/{aspect_ratio}?segment=N`，复用 480p 预览管线。

---

## 三、P0-2 高级转场系统

### xfade 集成

```python
XFADE_TRANSITIONS = {
    "push_left": "slideleft", "push_right": "slideright",
    "slide_up": "slideup", "slide_down": "slidedown",
    "zoom_in": "smoothup", "rotate": "circleopen",
    "wipe": "wipeleft", "cross_dissolve": "fade",
}

@dataclass
class TransitionConfig:
    type: str              # key in XFADE_TRANSITIONS
    duration: float = 0.5  # 0.3 ~ 1.5s
    easing: str = "easeInOut"
```

### xfade 链生成

```python
def _build_xfade_chain(self, segments, transitions) -> str:
    # offset 计算: cumulative_offset + seg.duration - trans.duration
    # 每个 xfade "吃掉" duration 长度时间
    # 音频用 acrossfade 同步
```

### DAG 集成

逻辑上 transition 是独立节点（dirty 追踪 + 参数存储），物理渲染在 concat 阶段一次性生成 xfade chain。任一 transition dirty → 重渲整条 chain（concat 本身很快）。

---

## 四、P1-1 跨片段语义理解

### CLIP Embedding + 聚类

```python
class ClipEmbedder:
    # open_clip ViT-B-32
    def embed_video_clip(self, video_path, sample_count=5) -> np.ndarray:
        # 均匀采样 N 帧 → CLIP 编码 → 均值 → (512,)
    
    def cluster_clips(self, embeddings, threshold=0.3) -> dict[str, int]:
        # AgglomerativeClustering + cosine distance
    
    def find_duplicates(self, embeddings, sim_threshold=0.95) -> list[tuple]:
        # 余弦相似度矩阵 → 去重对
```

SQLite 缓存：`clip_embeddings` 表存 embedding blob，`clip_clusters` 表存聚类结果。增量更新：新 clip 入库后全量重聚类（100 clips < 50ms）。

### 跨片段指令

Planner 预处理：用户指令含跨片段引用 → 查询 cluster → 解析为 clip_id 列表 → 分发给 Worker。

---

## 五、P1-2 基础调色

```python
LUT_PRESETS = {"cinematic", "fresh", "vintage", "high_contrast", "japanese", "cyberpunk"}

class ColorGradingPipeline:
    def build_filter(self, config: ColorGradeConfig) -> str:
        # eq=brightness:contrast + hue=s + lut3d
    
    def _auto_wb_filter(self, ref_clip_id) -> str:
        # 参考片段 RGB 均值 → colorbalance 参数
```

自动白平衡：参考片段 10 帧 RGB 均值 → 目标片段均值 → 通道比值差 → `colorbalance` filter。

---

## 六、新增依赖

```
ultralytics>=8.0.0       # YOLOv8
open-clip-torch>=2.24.0  # CLIP embedding
scikit-learn>=1.3.0      # AgglomerativeClustering
```

YOLOv8 + CLIP 增加 ~200MB，设为 optional extras。

---

## 七、文件变更

| 操作 | 路径 |
|------|------|
| 新增 | `adaptation/smart_crop.py`, `layout_rules.py`, `aspect_renderer.py` |
| 新增 | `intelligence/clip_embedder.py`, `material_graph.py`（Phase 2 预留） |
| 新增 | `rendering/color_grading.py` + `assets/luts/*.cube` |
| 修改 | `state/task_dag.py` — 5 种新 NodeType |
| 修改 | `state/project_state.py` — aspect_adapt 派生节点逻辑 |
| 修改 | `rendering/ffmpeg_renderer.py` — xfade 链 + color filter + target_ratio |
| 修改 | `orchestrator/planner.py` — 跨片段指令预处理 + 竖屏节奏 |
| 修改 | `storage/session_db.py` — clip_embeddings/clusters 表 |

---

## 八、风险与工时

| 项目 | 风险 | 工时 |
|------|------|------|
| Smart Crop | YOLO 非人物主体效果差 → 提供手动覆盖 + fallback | 2 周 |
| xfade offset | 多段累计误差 → 单元测试全覆盖 | 1 周 |
| CLIP 聚类 | 阈值因素材类型而异 → 暴露参数 + 推荐默认 | 1 周 |
| 自动白平衡 | 极端光照不准 → 标注 beta + 手动微调 | 1 周 |
| FFmpeg 版本 | xfade easing 7.0+, colortemperature 5.1+ → 降级提示 | 0.5 周 |

**总工时：6-7 周（1 后端）。** 建议顺序：转场(1周) → 调色(1周) → 自适应(2.5周) → 语义理解(1.5周)。
