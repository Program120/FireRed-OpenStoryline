## DEVELOPER 视角 -- 架构师/开发者

### 一、现有架构分析

当前FireRed-OpenStoryline的核心架构：

```
用户 → Agent (LangChain + ChatOpenAI) → MCP Server (FastMCP)
                                              ↓
                                        NodeManager
                                              ↓
                                   BaseNode子类（16个核心节点）
                                              ↓
                                      ArtifactStore (文件系统)
```

**关键架构特征**：
- `NodeManager` 管理节点DAG（有向无环图），通过 `node_kind`/`node_id`/`priority`/`require_prior_kind` 实现依赖编排
- `BaseNode` 抽象类提供统一的节点生命周期（输入校验 → 执行 → Artifact存储）
- `ArtifactStore` 基于文件系统的会话级Artifact持久化
- `MCP Server` 通过 `FastMCP` 暴露节点为工具，支持 `streamable-http` 传输
- `sampling_handler` 处理多模态输入（图片/视频帧采样）
- `skills_io` 通过 `SkillKit` 动态加载扩展技能

**V6架构挑战**：
1. 单Agent架构无法支撑多专业Agent协作
2. 文件系统ArtifactStore不支持跨Agent/跨会话共享
3. 同步渲染管线无法支撑实时生成
4. 缺少事件驱动机制（活内容系统需要）
5. 缺少统一的身份/风格表示层

### 二、V6架构演进方案

#### 2.1 多智能体编排层（Multi-Agent Orchestration Layer）

在现有 `agent.py` 的单Agent之上，引入Agent编排层：

```python
# 新增: src/open_storyline/agents/orchestrator.py

class AgentRole(Enum):
    DIRECTOR = "director"       # 总导演，全局决策
    STORYTELLER = "storyteller"  # 脚本/叙事
    CINEMATOGRAPHER = "cinematographer"  # 视觉/镜头
    MUSICIAN = "musician"        # 音乐/音效
    DESIGNER = "designer"        # 视觉包装/字幕/封面
    EDITOR = "editor"           # 时间线编排/转场
    PUBLISHER = "publisher"     # 发布/分发

class AgentOrchestrator:
    """
    多Agent编排器。
    每个Agent拥有独立的LLM实例、System Prompt、可访问的Node子集。
    Agent间通过EventBus + SharedArtifactStore通信。
    Director Agent做最终仲裁。
    """
    def __init__(self, cfg: Settings, roles: List[AgentRole]):
        self.event_bus = EventBus()
        self.shared_store = SharedArtifactStore(cfg)
        self.agents: Dict[AgentRole, CreativeAgent] = {}
        for role in roles:
            self.agents[role] = CreativeAgent(
                role=role,
                cfg=cfg,
                node_filter=ROLE_NODE_MAPPING[role],  # 每个角色只能调用相关节点
                event_bus=self.event_bus,
                shared_store=self.shared_store,
            )
    
    async def execute_brief(self, brief: str, style_profile: StyleProfile):
        """从Brief到成品的全自主编排"""
        # Phase 1: Director分解任务
        plan = await self.agents[AgentRole.DIRECTOR].plan(brief, style_profile)
        
        # Phase 2: 并行执行（可并行的阶段同时运行）
        tasks = self._build_execution_graph(plan)
        async for event in self._execute_dag(tasks):
            yield event  # 流式输出进度
        
        # Phase 3: Director审核整合
        final = await self.agents[AgentRole.DIRECTOR].review_and_finalize()
        return final
```

**关键设计决策**：
- 复用现有 `NodeManager` 的DAG编排能力，但将其提升为Agent级别的编排
- 每个 `CreativeAgent` 内部仍然使用 `NodeManager` 管理自己的节点执行流
- `ROLE_NODE_MAPPING` 限定每个角色可访问的节点，例如 Musician 只能访问 `SelectBGMNode`、`GenerateVoiceoverNode`

#### 2.2 共享Artifact Store升级

现有 `ArtifactStore` 是会话级文件系统存储。V6需要升级为：

```python
# 升级: src/open_storyline/storage/shared_artifact_store.py

class SharedArtifactStore:
    """
    跨Agent、跨会话的Artifact存储。
    底层仍可用文件系统（开发/单机），但抽象接口支持：
    - Redis/MinIO（团队协作场景）
    - 事件通知（Artifact变更时通知订阅的Agent）
    """
    
    async def publish(self, artifact_id: str, data: Any, 
                      producer: AgentRole, metadata: dict):
        """Agent产出Artifact并广播"""
        await self._persist(artifact_id, data, metadata)
        await self.event_bus.emit(ArtifactEvent(
            type="artifact_published",
            artifact_id=artifact_id,
            producer=producer,
            metadata=metadata,
        ))
    
    async def subscribe(self, artifact_kind: str, 
                        consumer: AgentRole) -> AsyncIterator[ArtifactEvent]:
        """Agent订阅特定类型Artifact的变更"""
        ...
```

**兼容性策略**：现有 `ArtifactStore` 作为 `SharedArtifactStore` 的单会话快捷包装，零破坏性改动。

#### 2.3 实时生成管线

当前 `RenderVideoNode` 是全片一次性渲染。V6需要引入流式渲染：

```python
# 新增: src/open_storyline/nodes/core_nodes/stream_render.py

class StreamRenderNode(BaseNode):
    """
    流式渲染节点。
    将Timeline拆为片段（Segment），每个Segment独立渲染，
    通过SSE/WebSocket流式推送给前端。
    
    架构：
    Timeline → SegmentSplitter → [Segment1, Segment2, ...] 
                                        ↓ (并行)
                                  FFmpeg片段渲染
                                        ↓
                                  StreamMuxer → SSE推送
    """
    
    async def execute_stream(self, timeline: Timeline, 
                              node_state: NodeState) -> AsyncIterator[bytes]:
        segments = self.segment_splitter.split(timeline)
        
        async for segment in self._render_parallel(segments):
            # 每个片段渲染完立即推送
            yield self.stream_muxer.package(segment)
```

**技术关键点**：
- 利用FFmpeg的 `-f segment` 或 fragmented MP4（fMP4）实现片段级输出
- 前端使用 MediaSource Extensions (MSE) 实现逐段播放
- 修改参数时只需重渲染受影响的Segment（增量渲染）

#### 2.4 Creator DNA / Style Profile

```python
# 新增: src/open_storyline/style/profile.py

@dataclass
class StyleProfile:
    """创作者/账号的风格DNA"""
    profile_id: str
    
    # 叙事维度
    narrative_pace: float          # 0.0(慢) ~ 1.0(快)
    humor_level: float             
    vocabulary_complexity: float   
    preferred_structures: List[str]  # ["hook-problem-solution", "chronological", ...]
    
    # 视觉维度
    color_palette: List[str]       # 主色调
    transition_preferences: Dict[str, float]  # 转场类型 -> 偏好权重
    shot_rhythm: List[float]       # 每个镜头的平均时长分布
    ken_burns_intensity: float
    
    # 音频维度
    bgm_genre_weights: Dict[str, float]
    voiceover_speed: float
    voiceover_emotion_range: Tuple[float, float]
    
    # 文字维度
    subtitle_style: dict
    thumbnail_style: dict
    
    @classmethod
    def learn_from_history(cls, artifact_store: SharedArtifactStore, 
                           account_id: str) -> "StyleProfile":
        """从历史作品中学习风格特征"""
        ...
    
    def blend(self, other: "StyleProfile", weight: float) -> "StyleProfile":
        """风格混合，用于风格探索"""
        ...
```

#### 2.5 事件驱动架构（Living Content支撑）

```python
# 新增: src/open_storyline/events/event_bus.py

class EventBus:
    """
    轻量级事件总线。
    V6内部通信基础设施。
    
    本地模式：asyncio.Queue
    分布式模式：Redis Streams / NATS
    """
    
    async def emit(self, event: Event): ...
    async def subscribe(self, event_type: str, handler: Callable): ...
    async def schedule(self, event: Event, cron: str): ...  # 定时触发（活内容用）
```

#### 2.6 平台发布层

```python
# 新增: src/open_storyline/publishers/

class PlatformPublisher(ABC):
    """平台发布抽象"""
    @abstractmethod
    async def publish(self, video_path: Path, metadata: PublishMetadata) -> PublishResult: ...
    @abstractmethod
    async def get_analytics(self, content_id: str) -> AnalyticsData: ...

class DouyinPublisher(PlatformPublisher): ...
class BilibiliPublisher(PlatformPublisher): ...
class YouTubePublisher(PlatformPublisher): ...
class XiaohongshuPublisher(PlatformPublisher): ...
```

### 三、技术路线图（分阶段交付）

**Phase 1（Month 1-4）：多Agent + 实时渲染基础**
- EventBus 实现（本地模式）
- SharedArtifactStore（兼容现有ArtifactStore）
- AgentOrchestrator + Director/Editor 两个角色先跑通
- StreamRenderNode 原型（fMP4 + MSE）
- 前端WebSocket流式预览

**Phase 2（Month 5-8）：Creator DNA + 全角色Agent**
- StyleProfile 数据模型 + 历史学习算法
- 补齐所有Agent角色（Storyteller / Cinematographer / Musician / Designer）
- Agent间冲突仲裁机制
- 风格条件生成（将StyleProfile注入各Node的prompt）

**Phase 3（Month 9-12）：自主流水线 + 活内容**
- Strategy Engine + Topic Discovery
- Quality Gate 自动评估
- PlatformPublisher 对接（至少抖音+B站）
- Living Content: Data Binding + Change Detection + Delta Rendering
- 内容矩阵仪表盘

**Phase 4（Month 13-18）：规模化 + 优化**
- 分布式EventBus（Redis Streams）
- 边缘部署优化（V5能力整合）
- 性能优化：并行Agent执行、渲染缓存
- 模板市场V2（Agent Workflow模板）
- API/SDK V2（面向第三方开发者的Agent编排API）

### 四、关键技术风险

| 风险 | 影响 | 缓解策略 |
|------|------|----------|
| 多Agent通信延迟累积 | 端到端时间过长 | Agent间异步通信+可并行阶段并行执行；Director做关键路径优化 |
| LLM调用成本爆炸（多Agent × 多轮） | 成本目标5元/条不可达 | 轻量任务用小模型（Qwen-Turbo）；引入推理缓存；非创意性判断走规则引擎 |
| 实时渲染质量与速度矛盾 | 流式预览画质差 | 两级渲染：低质量实时预览 + 最终高质量渲染 |
| 平台API变动/封禁风险 | 自动发布功能不稳定 | 抽象Publisher层+降级到导出文件+人工发布 |
| StyleProfile过拟合 | 风格僵化，失去创新 | 引入"风格探索"参数，定期注入随机扰动 |

---
