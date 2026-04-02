# V3 研发技术方案

---

## 一、架构全局变更

### 1.1 多模型 Provider 层（新增模块）

当前 `agent.py` 硬编码 `ChatOpenAI`，所有 node 共享同一 LLM 实例。V3 引入 Provider Registry + Router。

```
src/open_storyline/
├── providers/
│   ├── registry.py          # ProviderRegistry: 注册/发现/实例化
│   ├── base_provider.py     # BaseProvider ABC
│   ├── openai_provider.py   # OpenAI / 兼容接口
│   ├── anthropic_provider.py
│   ├── google_provider.py   # Gemini
│   ├── ollama_provider.py   # 本地推理
│   └── router.py            # NodeModelRouter: node_id → provider+model
```

**核心接口：**

```python
class BaseProvider(ABC):
    async def chat(self, messages, **kwargs) -> ChatResponse: ...
    async def embed(self, texts) -> list[np.ndarray]: ...  # optional
    def supports_vision(self) -> bool: ...
    def cost_per_1k_tokens(self) -> tuple[float, float]: ...  # (input, output)

class NodeModelRouter:
    def __init__(self, config: dict[str, ModelRouteConfig]):
        # { "UnderstandClipsNode": {"primary": "gemini/gemini-2.0-flash", "fallback": "qwen/qwen-plus"} }
    
    async def get_provider(self, node_id: str) -> BaseProvider:
        # 1. 查 node 级配置
        # 2. fallback 到全局默认
        # 3. 主模型健康检查失败 → 切 fallback（circuit breaker 模式）
```

**config.toml 变更：**

```toml
[providers.openai]
api_key = "sk-..."
base_url = "https://api.openai.com/v1"

[providers.anthropic]
api_key = "sk-ant-..."

[providers.gemini]
api_key = "AIza..."

[node_routing]
default = "openai/gpt-4o"
UnderstandClipsNode = "gemini/gemini-2.0-flash"
GenerateScriptNode = "anthropic/claude-sonnet-4-20250514"
GenerateVoiceoverNode = "openai/gpt-4o-mini"  # 仅用于文案润色，cost-sensitive

[node_routing.fallback]
"anthropic/*" = "openai/gpt-4o"
"gemini/*" = "openai/gpt-4o"
```

**对 agent.py 的改造：** `build_agent()` 不再直接创建 `ChatOpenAI`，改为从 `ProviderRegistry` 获取 default provider 传入 LangChain agent。各 node 的 `__call__` 方法通过 `NodeModelRouter.get_provider(self.meta.node_id)` 获取专属 provider。

**成本追踪：** 每次调用记录 `(session_id, node_id, provider, model, input_tokens, output_tokens, cost_usd, timestamp)` 到 SQLite `token_usage` 表。提供 `GET /api/usage/{session_id}` 查询接口。

### 1.2 批量处理引擎（新增模块）

```
src/open_storyline/
├── batch/
│   ├── template.py          # EditTemplate: 序列化/反序列化编辑流程
│   ├── queue.py             # BatchQueue: asyncio.Queue + worker pool
│   ├── executor.py          # BatchExecutor: 单任务执行（复用 agent 流程）
│   └── api.py               # FastAPI 路由
```

**EditTemplate 设计：**

```python
@dataclass
class EditTemplate:
    template_id: str
    name: str
    node_chain: list[NodeSnapshot]  # 有序 node 调用链 + 参数快照
    overridable_fields: list[str]   # 允许逐条覆盖的字段
    
    def serialize(self) -> dict: ...
    
    @classmethod
    def from_session(cls, session_id: str, store: ArtifactStore) -> "EditTemplate":
        # 从已完成 session 提取 node 调用链
```

**并发控制：** `BatchQueue` 使用 `asyncio.Semaphore(max_concurrent)` 控制并发。默认 `max_concurrent=3`（受限于 API rate limit 和内存）。每个 task 独立 session，共享 provider 连接池。

**失败处理：** 单条失败不阻塞队列，记录错误 + 支持单条重试。3 次重试后标记 `failed`。

### 1.3 NLE 导出模块

```
src/open_storyline/
├── export/
│   ├── base_exporter.py
│   ├── fcpxml_exporter.py   # Final Cut Pro XML 1.11
│   ├── edl_exporter.py      # CMX3600 EDL (DaVinci Resolve)
│   └── media_packager.py    # 可选：打包素材 + 工程文件为 zip
```

**FCPXML 映射：**

```python
class FCPXMLExporter:
    def export(self, timeline: TimelineData, media_root: str) -> str:
        # timeline segments → <asset-clip>
        # transitions → <transition name="Cross Dissolve" duration="...">
        # subtitles → <title> on separate lane
        # LUT → <filter-video> with 注释
        # voiceover + BGM → <audio> lanes
        # 不可映射项 → <!-- unsupported: ... -->
```

**关键技术难点：**
- FFmpeg xfade 转场名 → FCP/DaVinci 转场名的映射表（8 种手工映射）
- 时间码精度：内部 ms → FCPXML rational time (e.g., `1001/30000s`)
- 素材引用路径：支持 `absolute` / `relative` / `packaged` 三种模式

### 1.4 配音情感控制

**改造 `generate_voiceover.py`：**

```python
@dataclass
class VoiceoverEmotionConfig:
    emotion: str = "neutral"  # excited | calm | professional | casual | tense | warm
    speed: float = 1.0        # 0.7 ~ 1.5
    
class EmotionMapper:
    """将统一情感标签映射到各 TTS 厂商原生参数"""
    
    PROVIDER_MAP = {
        "302": {  # Fish Audio / 302.ai
            "excited": {"speed": 1.1, "emotion": "happy"},
            "professional": {"speed": 0.95, "emotion": "neutral"},
        },
        "bytedance": {  # 火山引擎
            "excited": {"emotion": "happy", "language": {"speed_ratio": 1.1}},
        },
        "minimax": {
            "excited": {"timber_weights": [{"timber_id": "...", "weight": 80}]},
        },
    }
```

段落级情感：`GenerateScript` 输出的 script 数据结构新增 `emotion` 字段，`PlanTimelineProNode` 传递到 TTS 调用。

---

## 二、团队协作

**最小化实现——不引入独立 auth 服务，基于 SQLite 扩展。**

```sql
CREATE TABLE teams (
    team_id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMP
);

CREATE TABLE team_members (
    team_id TEXT, user_id TEXT, role TEXT CHECK(role IN ('owner','editor','viewer')),
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT, user_id TEXT, session_id TEXT,
    action TEXT,  -- 'create_session' | 'edit_node' | 'render' | 'export' | 'approve' | 'reject'
    detail_json TEXT,
    created_at TIMESTAMP
);

CREATE TABLE publish_requests (
    request_id TEXT PRIMARY KEY,
    session_id TEXT, submitter_id TEXT, reviewer_id TEXT,
    status TEXT CHECK(status IN ('pending','approved','rejected')),
    comment TEXT, created_at TIMESTAMP, reviewed_at TIMESTAMP
);
```

**agent_fastapi.py 改造：** WebSocket 连接时携带 `user_id` + `team_id`，中间件检查权限后注入 `ClientContext`。Viewer 只能调用 preview / export 类 node，不能调用 render / edit 类。

---

## 三、长视频性能优化

### 3.1 CLIP 嵌入分片流水线

```python
class ChunkedClipEmbedder:
    CHUNK_DURATION = 300  # 5min per chunk
    
    async def embed_long_video(self, video_path, total_duration):
        chunks = self._split_by_duration(video_path, total_duration)
        # asyncio.gather 并行处理 chunks
        # 每个 chunk 内部：均匀采样 → CLIP 编码 → 均值
        # chunk 间结果 concat → 全局聚类
```

### 3.2 渲染分段合并

当前 `render_video.py` 使用 MoviePy 一次性加载所有 clip 到内存。改为：

1. 每个 segment 独立渲染为临时 mp4（已有 segment-level 架构）
2. 最终用 FFmpeg `concat demuxer` 合并（零拷贝，不重编码）
3. 内存占用从 O(total_duration) 降至 O(max_segment_duration)

```python
def _concat_segments_ffmpeg(self, segment_paths: list[str], output_path: str):
    # 生成 concat list file
    # ffmpeg -f concat -safe 0 -i list.txt -c copy output.mp4
```

### 3.3 SQLite WAL 模式

批量处理 + 团队协作场景下并发写入增加。启用 WAL 模式：

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
```

---

## 四、新增依赖

```
anthropic>=0.40.0          # Anthropic provider
google-genai>=1.0.0        # Gemini provider
ollama>=0.4.0              # 本地推理（optional）
lxml>=5.0.0                # FCPXML 生成
```

Provider 依赖设为 optional extras：`pip install openstoryline[anthropic,gemini,ollama]`

---

## 五、文件变更总览

| 操作 | 路径 |
|------|------|
| 新增 | `src/open_storyline/providers/` — 整个模块 |
| 新增 | `src/open_storyline/batch/` — 整个模块 |
| 新增 | `src/open_storyline/export/` — 整个模块 |
| 修改 | `src/open_storyline/agent.py` — Provider 集成 + ClientContext 扩展 team/user |
| 修改 | `src/open_storyline/config.py` — providers / node_routing / team 配置段 |
| 修改 | `config.toml` — 新增 providers / node_routing / batch / team 段 |
| 修改 | `src/open_storyline/nodes/core_nodes/generate_voiceover.py` — 情感参数 |
| 修改 | `src/open_storyline/nodes/node_schema.py` — VoiceoverEmotionConfig / BatchInput |
| 修改 | `src/open_storyline/nodes/core_nodes/render_video.py` — 分段合并渲染 |
| 修改 | `src/open_storyline/storage/session_db.py` — team / audit / publish 表 |
| 修改 | `agent_fastapi.py` — batch API + team middleware + export 路由 + usage 路由 |
| 新增 | `web/` 下批量管理面板、成本看板、审批面板 UI |

---

## 六、风险与工时

| 项目 | 风险 | 缓解 | 工时 |
|------|------|------|------|
| Provider 抽象 | 各厂商 API 差异大（streaming / tool calling / vision 支持不一） | 最小公约接口 + feature flag | 2.5 周 |
| Anthropic tool calling | Anthropic tool format 与 OpenAI 不同 | langchain-anthropic 已适配，验证即可 | 0.5 周 |
| 批量处理并发 | API rate limit / 内存 OOM | Semaphore + 内存监控 + 自动降级并发数 | 2 周 |
| FCPXML 兼容性 | FCP 版本间 schema 差异 | 锁定 FCPXML 1.11 + FCP 10.7+ 验证 | 1.5 周 |
| EDL 精度 | EDL 不支持复杂效果 | EDL 仅导出基础时间线，复杂效果用注释标记 | 0.5 周 |
| TTS 情感映射 | 各厂商情感参数体系完全不同 | 建映射表 + 标注"best effort" + 预听 | 1 周 |
| 长视频 OOM | MoviePy 全量加载 | 强制分段渲染 + concat demuxer | 1 周 |
| SQLite 并发写入 | 批量 + 多用户场景 | WAL + busy_timeout + 写操作排队 | 0.5 周 |

**总工时：8-9 周（1 后端 + 0.5 前端）。** 建议顺序：Provider 层 (W1-W3) → 批量处理 (W2-W5) → NLE 导出 (W4-W6) → 配音情感 (W5-W7) → 团队协作 (W6-W8) → 长视频优化 (W7-W9)。Provider 层是批量处理的前置依赖（批量场景下混合路由价值最大）。

---
