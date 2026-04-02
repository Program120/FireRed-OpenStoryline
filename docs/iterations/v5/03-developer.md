# V5 研发技术方案

---

## 一、架构演进概览

V5 的核心架构变化是从"单 Agent 响应式执行"演进为"多 Agent 主动式创作"。新增三个顶层子系统：

```
                    ┌──────────────────────┐
                    │   Creative Director   │  ← 新增：自主创意层
                    │  (Trend + Strategy)   │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
     ┌────────────┐  ┌────────────────┐  ┌────────────────┐
     │  Planner   │  │  Performance   │  │  Cross-Project │  ← 新增
     │  Agent     │  │  Learning      │  │  Knowledge     │
     │  (V1 改造) │  │  Engine        │  │  Engine        │
     └─────┬──────┘  └───────┬────────┘  └───────┬────────┘
           │                 │                    │
           ▼                 ▼                    ▼
     ┌──────────────────────────────────────────────┐
     │            Existing V4 Pipeline               │
     │  (DAG + Nodes + MCP + Rendering + Collab)     │
     └──────────────────────────────────────────────┘
           │
           ▼
     ┌──────────────────────┐
     │   Advanced Audio      │  ← 新增：音频生成层
     │  (Voice Clone + Music │
     │   Gen + SFX Synth)    │
     └──────────────────────┘
```

---

## 二、DAG 架构变更

V5 新增节点类型：

| NodeType | 说明 | 前置依赖 |
|----------|------|----------|
| `trend_analyze` | 趋势分析 | 无（定时触发或用户触发） |
| `creative_propose` | 选题生成 | trend_analyze + account_profile |
| `performance_ingest` | 数据采集 | 外部 API |
| `performance_analyze` | 归因分析 | performance_ingest + render_video（历史） |
| `voice_clone_train` | 声音克隆训练 | 音频样本 |
| `voice_clone_infer` | 克隆声音推理 | voice_clone_train + generate_script |
| `music_generate` | AI 配乐 | plan_timeline（获取时长和情绪） |
| `sfx_synthesize` | 音效合成 | understand_clips（画面语义） |
| `branch_edit` | 互动分支编辑 | generate_script |
| `branch_render` | 分支视频渲染 | branch_edit + render_video |

关键变化——自主创意流（无需用户指令即可启动）：

```
[定时触发] → trend_analyze → creative_propose → [等待用户确认]
                                                       ↓
                                              正常 DAG 流水线
                                                       ↓
                                   performance_ingest → performance_analyze
                                                       ↓
                                              [反哺 creative_propose 权重]
```

---

## 三、P0-1 自主创意引擎

### 3.1 趋势分析服务

```python
class TrendAnalyzer:
    """多平台趋势聚合与分析"""
    
    def __init__(self, platforms: list[str], account_verticals: list[str]):
        self.collectors = {
            "douyin": DouyinTrendCollector(),    # 抖音热搜/热榜 API
            "bilibili": BilibiliTrendCollector(), # B站热门/搜索词
            "xiaohongshu": XHSTrendCollector(),   # 小红书热搜笔记
            "weibo": WeiboTrendCollector(),       # 微博热搜
            "youtube": YouTubeTrendCollector(),   # YouTube Trending API
        }
    
    async def collect_trends(self) -> list[TrendItem]:
        """并行采集所有平台趋势"""
        # 每个 collector 返回 TrendItem(keyword, heat_score, category, 
        #                               velocity, platform, timestamp)
    
    def filter_by_vertical(self, trends, verticals) -> list[TrendItem]:
        """按账号垂类过滤相关趋势"""
        # LLM 判断趋势与垂类的相关性评分
    
    def predict_window(self, trend: TrendItem) -> TimingPrediction:
        """预测趋势时效窗口"""
        # 基于历史同类话题的生命周期曲线（上升/峰值/衰退）
        # 返回 remaining_hours + confidence
```

数据存储：SQLite 新增 `trends` 表，保留 30 天历史用于趋势生命周期建模。

采集频率：每 2 小时一次（可配置），异步后台任务。

### 3.2 AI 选题推荐

```python
class CreativeProposer:
    """基于趋势 + 账号画像 + 历史表现生成选题"""
    
    def generate_proposals(
        self, 
        trends: list[TrendItem],
        account_profile: AccountProfile,  # 垂类、风格、受众画像
        performance_history: PerformanceStats,  # 历史数据表现
        n: int = 5
    ) -> list[CreativeProposal]:
        """
        CreativeProposal:
            title_direction: str       # 标题方向
            hook_type: str             # 前3秒类型（悬念/冲突/数据/情绪）
            script_skeleton: list[ScriptBlock]  # 脚本框架
            material_requirements: list[MaterialReq]  # 素材需求清单
            reference_videos: list[str]  # 参考爆款链接
            estimated_performance: PerformancePrediction
            reasoning: str             # 推荐理由
        """
        # Prompt 策略：
        # 1. 注入账号历史 TOP20 视频的剪辑特征 + 数据
        # 2. 注入当前趋势 + 垂类匹配分
        # 3. 注入账号受众画像
        # 4. 要求 LLM 生成有差异化的 N 个方向
```

### 3.3 概念到成片的自主推进

新增 `AutonomousOrchestrator`，封装从选题到成片的全自动流程：

```python
class AutonomousOrchestrator:
    """无人值守的端到端创作编排"""
    
    async def execute(self, proposal: CreativeProposal, 
                      session_id: str,
                      checkpoints: list[str] = ["script", "rough_cut"]):
        """
        checkpoints: 需要人工确认的节点，其余自动推进
        默认在脚本和粗剪两个节点暂停等待确认
        """
        # 1. 匹配现有素材（CLIP embedding 搜索）
        # 2. 标注缺失素材 → 触发 AI 生成（VACE）或素材搜索（Pexels）
        # 3. 执行标准 DAG 流水线
        # 4. 在 checkpoint 节点暂停，推送通知等待确认
        # 5. 确认后继续下一阶段
        # 6. 完成后触发 CriticAgent 自检
```

引入 `CriticAgent`（参考 EditDuet 的 Critic 机制）：

```python
class CriticAgent:
    """渲染前自动质检"""
    
    def evaluate(self, timeline: TimelineTracks, 
                 account_profile: AccountProfile) -> CriticReport:
        # 检查项：
        # - 前3秒吸引力评分（VLM 分析首帧 + 文案）
        # - 节奏一致性（BPM 曲线平滑度）
        # - 品牌合规（logo 位置、禁用词、竞品露出）
        # - 音画同步精度
        # - 字幕可读性（对比度、停留时长）
        # 返回 pass/warn/fail + 具体建议
```

---

## 四、P0-2 数据驱动优化引擎

### 4.1 数据采集管线

```python
class PerformanceIngester:
    """多平台数据采集"""
    
    PLATFORM_ADAPTERS = {
        "douyin": DouyinCreatorAPI,       # 抖音开放平台创作者 API
        "bilibili": BilibiliDataAPI,      # B站创作中心 API
        "xiaohongshu": XHSCreatorAPI,     # 小红书蒲公英 API
        "youtube": YouTubeAnalyticsAPI,   # YouTube Analytics API
        "manual": CSVImporter,            # 手动 CSV 导入（兜底）
    }
    
    async def ingest(self, project_id: str, platform: str) -> VideoMetrics:
        """
        VideoMetrics:
            views, likes, comments, shares, saves   # 基础指标
            completion_rate: float                    # 完播率
            retention_curve: list[float]              # 逐秒留存曲线
            audience_demographics: dict               # 观众画像
            traffic_sources: dict                     # 流量来源
        """
```

### 4.2 剪辑特征提取

```python
class EditFeatureExtractor:
    """从渲染后视频 + timeline 数据提取剪辑特征向量"""
    
    def extract(self, timeline: TimelineTracks, 
                rendered_video: str) -> EditFeatureVector:
        return EditFeatureVector(
            avg_shot_duration=...,         # 平均镜头时长
            cut_frequency=...,             # 切换频率（cuts/min）
            transition_types=...,          # 转场类型分布
            subtitle_density=...,          # 字幕覆盖率
            bgm_energy_curve=...,          # BGM 能量曲线
            hook_type=...,                 # 前3秒类型分类
            color_temperature=...,         # 整体色温
            pacing_variance=...,           # 节奏变化度
            voice_emotion_curve=...,       # 配音情绪曲线
            visual_complexity=...,         # 画面复杂度（VLM 评分）
        )
```

### 4.3 归因分析与策略生成

```python
class PerformanceAnalyzer:
    """特征归因 + 策略建议"""
    
    def analyze_account(self, account_id: str, 
                        window_days: int = 90) -> AccountStrategy:
        # 1. 拉取该账号所有项目的 EditFeatureVector + VideoMetrics
        # 2. 特征重要性分析（SHAP / 简单相关性）
        # 3. 聚类高表现视频 vs 低表现视频的特征差异
        # 4. LLM 总结为人类可读的策略建议
        
        return AccountStrategy(
            optimal_shot_duration=(2.5, 4.0),  # 最佳镜头时长区间
            recommended_hook_type="suspense",
            optimal_cut_frequency=8.5,          # cuts/min
            preferred_transitions=["push_left", "zoom_in"],
            color_preference="warm",
            confidence=0.78,
            reasoning="基于过去90天87条视频的分析..."
        )
    
    def inject_strategy_to_planner(self, strategy: AccountStrategy,
                                    planner_context: dict) -> dict:
        """将策略注入 Planner 的决策上下文"""
        # 作为 System Prompt 的一部分，影响 Planner 的剪辑决策
```

数据存储：新增 SQLite 表 `video_metrics`、`edit_features`、`account_strategies`。

---

## 五、P0-3 高级音频生成

### 5.1 声音克隆

**模型选型：CosyVoice 2（阿里通义）**
- 5 秒即可零样本克隆，5 分钟样本效果更佳
- 支持中英日韩多语言
- 情绪控制（开心/悲伤/愤怒/平静）
- Apache 2.0 开源

```python
class VoiceCloner:
    def train(self, audio_samples: list[str], 
              speaker_id: str) -> VoiceProfile:
        """
        训练声音配置文件
        - 提取说话人 embedding
        - 存储到 voice_profiles/{speaker_id}/
        - 返回 VoiceProfile(speaker_id, embedding_path, quality_score)
        """
    
    def synthesize(self, text: str, voice_profile: VoiceProfile,
                   emotion: str = "neutral") -> str:
        """
        用克隆声音合成语音
        - 返回 WAV 文件路径
        - 自动添加不可听水印（FrequencyWatermark）
        """
```

安全机制：
- 训练时需上传授权书图片（OCR 验证签名）
- 输出音频嵌入频域水印，可追溯来源
- `voice_profiles` 目录加密存储

### 5.2 AI 配乐生成

**模型选型：Stable Audio Open 2.0**
- 文本描述 → 44.1kHz 立体声音频
- 支持时长控制（精确到秒）
- 非商用 CC-BY-NC 4.0（商用需评估 MusicGen 替代）

```python
class MusicGenerator:
    def generate(self, 
                 duration_sec: float,
                 style_prompt: str,        # "upbeat electronic, 120 BPM"
                 emotion_curve: list[float], # 逐秒情绪强度 0-1
                 reference_audio: str = None # 可选参考曲目
                 ) -> GeneratedBGM:
        """
        GeneratedBGM:
            path: str
            bpm: float
            beats: list[float]  # 节拍点（ms）
            key: str            # 调性
            sections: list[MusicSection]  # 前奏/主歌/副歌/尾奏
        """
    
    def refine(self, bgm: GeneratedBGM, 
               instruction: str) -> GeneratedBGM:
        """迭代修改：'副歌部分再激昂一点'"""
        # img2img 式的音频修改：在现有音频基础上局部重生成
```

与 `plan_timeline` 的集成：`music_generate` 节点在 `plan_timeline` 之后执行，获取总时长和各段情绪标签，生成精确匹配的 BGM。

### 5.3 音效合成

```python
class SFXSynthesizer:
    """基于画面语义的自动音效"""
    
    def analyze_and_generate(self, 
                              clips: list[Clip],  # 带 caption 的片段
                              timeline: TimelineTracks
                              ) -> list[SFXEvent]:
        # 1. VLM 扫描每个 clip 的关键帧，识别音效触发事件
        #    (door_slam, rain, applause, footsteps, whoosh, ...)
        # 2. 对每个事件：
        #    - 优先从预置音效库检索（低延迟）
        #    - 库中无匹配时调用 AudioGen 生成
        # 3. 返回 SFXEvent(timestamp_ms, duration_ms, audio_path, type)
    
    SFX_LIBRARY_PATH = "resource/sfx/"  # 预置 200+ 常用音效
```

---

## 六、P1-1 跨项目学习引擎

```python
class CrossProjectKnowledge:
    """跨项目模式提取与推荐"""
    
    def extract_patterns(self, account_id: str) -> list[EditPattern]:
        """
        从该账号所有高表现项目中提取可复用模式
        EditPattern:
            name: str               # "美妆开箱测评节奏"
            shot_sequence: list      # 镜头序列模板
            transition_sequence: list
            pacing_template: list[float]  # 各段时长比例
            subtitle_style: dict
            bgm_style: str
            performance_score: float
            applicable_categories: list[str]
        """
    
    def recommend_pattern(self, 
                          project_context: dict,  # 新项目的内容类型/垂类
                          account_id: str
                          ) -> list[EditPattern]:
        """为新项目推荐最匹配的剪辑模式"""
        # 1. 从该账号 + 同垂类其他账号的 patterns 中检索
        # 2. 按相似度 + 表现分排序
        # 3. 返回 TOP 3 推荐
    
    def apply_pattern(self, pattern: EditPattern, 
                      timeline: TimelineTracks) -> TimelineTracks:
        """将模式应用到当前时间线"""
        # 调整节奏、转场、字幕风格以匹配 pattern
```

存储：新增 SQLite 表 `edit_patterns`、`pattern_performance`。跨账号共享需租户级权限控制（复用 V4 的 RBAC）。

---

## 七、P1-2 互动视频

### 数据模型

```python
@dataclass
class BranchNode:
    node_id: str
    video_segment_id: str        # 对应的视频片段
    prompt_text: str             # 选择提示（"选A看功能介绍"）
    children: list[BranchEdge]   # 分支出边
    is_terminal: bool = False

@dataclass  
class BranchEdge:
    label: str                   # 选项文字
    target_node_id: str
    condition: str = ""          # 可选条件表达式

@dataclass
class InteractiveProject:
    root_node_id: str
    nodes: dict[str, BranchNode]
    # 序列化为 JSON → 兼容 B站互动视频格式
```

### 渲染策略

- 每个 `BranchNode` 独立渲染为一个视频片段
- 打包时生成索引文件（B站 `.ivideo` 格式 / 通用 H5 播放器 JSON）
- 非互动平台：自动选择"最优路径"（历史选择率最高的分支）渲染为线性视频

---

## 八、国际化方案

### Prompt 模板

```
prompts/tasks/
├── generate_script/
│   ├── zh/system.md  ← 已有
│   ├── en/system.md  ← 已有（补全缺失的）
│   ├── ja/system.md  ← 新增
│   └── ko/system.md  ← 新增
```

`PromptBuilder` 改造：增加 `fallback_lang` 参数，缺失语言时 fallback 到 en。

### 多语言 TTS

在 `generate_voiceover` 节点中扩展语言路由：

```python
TTS_LANGUAGE_ROUTES = {
    "zh": ["minimax", "bytedance", "cosyvoice"],
    "en": ["cosyvoice", "openai_tts", "elevenlabs"],
    "ja": ["cosyvoice", "voicevox"],
    "ko": ["cosyvoice"],
    "es": ["openai_tts", "elevenlabs"],
}
```

### 字幕翻译

新增 `translate_subtitles` 辅助节点：LLM 翻译 + 本地化排版规则（日文竖排、阿拉伯语 RTL 等）。

---

## 九、移动端方案

采用 WebView + 后端渲染的轻量方案：

```
Mobile App (React Native / Flutter WebView)
    │
    ├── 素材上传（分片上传，复用 V4 断点续传）
    ├── 语音指令（端侧 ASR → 文本 → WebSocket chat.send）
    ├── 预览播放（HLS 流，后端实时转码）
    ├── 审片批注（时间戳 + 文字/语音标注）
    └── 推送通知（渲染完成、选题推荐、审核请求）
```

不在端侧跑任何 AI 模型，所有计算在服务端完成。移动端本质是一个"远程控制器 + 预览器"。

---

## 十、新增依赖

```
# 音频生成
cosyvoice>=2.0.0           # 声音克隆 + 多语言 TTS
stable-audio-tools>=0.2.0  # AI 配乐生成
audiocraft>=1.3.0           # MusicGen 备选 + AudioGen 音效

# 数据分析
shap>=0.45.0               # 特征归因（可选，fallback 到简单相关性）

# 趋势采集
httpx>=0.28.0              # 异步 HTTP（已有）
apscheduler>=3.10.0        # 定时任务调度
```

音频模型总计 ~5GB，需 GPU 推理（最低 RTX 3060 12GB）。CPU fallback 可用但延迟 10x+。

---

## 十一、文件变更

| 操作 | 路径 |
|------|------|
| 新增 | `intelligence/trend_analyzer.py` — 趋势采集与分析 |
| 新增 | `intelligence/creative_proposer.py` — AI 选题推荐 |
| 新增 | `intelligence/autonomous_orchestrator.py` — 自主编排 |
| 新增 | `intelligence/critic_agent.py` — 自动质检 |
| 新增 | `intelligence/performance_engine.py` — 数据采集+归因+策略 |
| 新增 | `intelligence/cross_project_knowledge.py` — 跨项目学习 |
| 新增 | `audio/voice_cloner.py` — 声音克隆 |
| 新增 | `audio/music_generator.py` — AI 配乐 |
| 新增 | `audio/sfx_synthesizer.py` — 音效合成 |
| 新增 | `interactive/branch_editor.py` — 互动分支编辑 |
| 新增 | `interactive/branch_renderer.py` — 分支视频渲染 |
| 新增 | `i18n/prompt_i18n.py` — 多语言 Prompt 路由 |
| 新增 | `i18n/subtitle_translator.py` — 字幕翻译节点 |
| 新增 | `platform_adapters/douyin.py`, `bilibili.py`, `xhs.py`, `youtube.py` — 数据采集适配器 |
| 修改 | `state/task_dag.py` — 10 种新 NodeType |
| 修改 | `orchestrator/planner.py` — 注入 AccountStrategy + CreativeProposal 上下文 |
| 修改 | `nodes/generate_voiceover/` — 多语言路由 + 声音克隆分支 |
| 修改 | `nodes/select_bgm/` — AI 配乐生成分支 |
| 修改 | `nodes/plan_timeline/` — 音效轨道 + 互动分支标记 |
| 修改 | `storage/session_db.py` — 新增 trends/metrics/patterns 等表 |
| 修改 | `server.py` — 注册新节点 |
| 修改 | `prompts/tasks/` — 补全多语言模板 |

---

## 十二、风险与工时

| 项目 | 风险 | 缓解 | 工时 |
|------|------|------|------|
| 趋势 API | 各平台 API 不稳定/限流/变更 | 多源冗余 + 手动导入兜底 + 缓存 | 2 周 |
| 声音克隆 | 法律合规风险 | 强制授权流程 + 水印 + 审计日志 | 2 周 |
| AI 配乐 | 商用版权不明确 | MusicGen（MIT）作为商用备选 | 2 周 |
| 数据归因 | 样本量不足时结论不可靠 | 最低 30 条视频才输出策略 + 置信度标注 | 3 周 |
| CriticAgent | 自检标准与用户预期不一致 | 可配置检查项 + 严格/宽松模式 | 1 周 |
| 跨项目学习 | 不同账号风格差异大 | 仅推荐同垂类 + 用户可拒绝 | 2 周 |
| 互动视频 | B站格式文档不完整 | 先支持通用 H5，B站格式标记实验性 | 2 周 |
| 移动端 | WebView 性能 + 各机型适配 | MVP 只做 iOS + 主流 Android | 2 周 |

**总工时：12-14 周（2 后端 + 1 前端 + 1 AI 工程师）。**

建议并行分组：
- **组 A（后端 + AI）：** 音频套件(W1-W4) → 数据引擎(W5-W9) → 跨项目(W10-W12)
- **组 B（后端）：** 趋势分析(W1-W3) → 自主创意(W3-W7) → 互动视频(W8-W11) → 国际化(W11-W13)
- **组 C（前端）：** 移动端 MVP(W1-W6) → 创意市场 UI(W7-W10) → 互动编辑器 UI(W10-W13)
- **W13-W14：** 全员集成测试 + 用户验收
