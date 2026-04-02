# V4 研发技术方案

---

## 一、架构演进总览

| 维度 | V3 | V4 |
|------|-----|-----|
| 用户模型 | 单用户 | 多用户 + RBAC 角色权限 |
| 数据库 | SQLite | PostgreSQL + Redis |
| 协作模式 | 无 | 项目锁 + 评论 + 审核工作流 |
| 视频素材 | 仅实拍导入 | 实拍 + AI 生成（VACE/Open-Sora/HunyuanVideo） |
| 合规能力 | 无 | 品牌安全检查引擎 |
| 接口层 | WebSocket 会话 | WebSocket + RESTful API v1 + SDK |
| 版本管理 | ProjectState 单版本 | 增量快照链 + 版本回退 |
| 扩展性 | 无 | 插件 Hook 机制 |

---

## 二、P0-1：团队协作与权限系统

### 数据模型

```python
class Organization(BaseModel):
    org_id: str
    name: str
    plan: Literal["free", "team", "enterprise"]
    created_at: datetime

class Member(BaseModel):
    user_id: str
    org_id: str
    role: Literal["admin", "director", "editor", "reviewer", "viewer"]
    invited_at: datetime

class Project(BaseModel):
    project_id: str
    org_id: str
    name: str
    created_by: str
    assigned_to: Optional[str]  # 当前编辑者
    status: Literal["draft", "editing", "in_review", "approved", "published"]
    locked_by: Optional[str]    # 编辑锁持有者
    lock_expires_at: Optional[datetime]

class Comment(BaseModel):
    comment_id: str
    project_id: str
    user_id: str
    timestamp_sec: float        # 视频时间线上的时间点
    content: str
    resolved: bool = False
    created_at: datetime

class AuditLog(BaseModel):
    log_id: str
    project_id: str
    user_id: str
    action: str                 # "edit_instruction", "submit_review", "approve", etc.
    detail: Dict[str, Any]
    created_at: datetime
```

### 数据库迁移：SQLite → PostgreSQL

```python
# alembic migration
class DatabaseConfig(BaseModel):
    driver: Literal["sqlite", "postgresql"] = "postgresql"
    dsn: str = "postgresql://user:pass@localhost:5432/openstoryline"
    pool_size: int = 20
    redis_url: str = "redis://localhost:6379/0"  # 分布式锁 + 缓存
```

迁移策略：
1. 使用 SQLAlchemy 2.0 + Alembic，保持与 SQLite 的双向兼容（个人用户仍可用 SQLite）
2. Redis 用于：编辑锁（分布式锁）、Session 缓存、WebSocket 消息广播（pub/sub）
3. 素材文件存储：本地模式用文件系统，云端模式用 S3 兼容对象存储（MinIO / 阿里云 OSS）

### 编辑锁机制

```python
class ProjectLockManager:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def acquire_lock(self, project_id: str, user_id: str, ttl: int = 1800) -> bool:
        """获取项目编辑锁，默认 30 分钟过期"""
        key = f"project_lock:{project_id}"
        return await self.redis.set(key, user_id, nx=True, ex=ttl)

    async def release_lock(self, project_id: str, user_id: str) -> bool:
        """释放锁（仅持有者可释放）"""
        # Lua 脚本保证原子性
        ...

    async def heartbeat(self, project_id: str, user_id: str) -> bool:
        """续期锁（客户端定期心跳）"""
        ...
```

### 通知系统

```python
class NotificationDispatcher:
    """项目状态变更通知"""
    channels: List[NotificationChannel]  # WebSocket, Email, Feishu, WeCom

    async def notify(self, event: ProjectEvent):
        for channel in self.channels:
            if channel.should_notify(event):
                await channel.send(event)
```

### 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `auth/models.py` | Organization, Member, RBAC 模型 |
| 新增 | `auth/permissions.py` | 权限校验装饰器 |
| 新增 | `auth/jwt_handler.py` | JWT 签发与验证 |
| 新增 | `collaboration/lock_manager.py` | Redis 分布式编辑锁 |
| 新增 | `collaboration/comments.py` | 评论 CRUD + 时间线标注 |
| 新增 | `collaboration/review_workflow.py` | 审核工作流状态机 |
| 新增 | `collaboration/audit_log.py` | 操作日志记录 |
| 新增 | `collaboration/notifications.py` | 多渠道通知分发 |
| 新增 | `db/postgres.py` | PostgreSQL 连接管理 |
| 新增 | `db/migrations/` | Alembic 迁移脚本 |
| 修改 | `storage/agent_memory.py` | 适配 PostgreSQL + 多用户隔离 |
| 修改 | `agent_fastapi.py` | 新增认证中间件 + 协作 API 路由 |
| 修改 | `config.toml` | 数据库、Redis、对象存储配置项 |

### 工时：40 人天

---

## 三、P0-2：AI 视频生成集成

### VideoGen Provider 抽象

```python
class VideoGenRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = None
    reference_image: Optional[Path] = None    # 参考图（用于风格对齐）
    reference_video: Optional[Path] = None    # 参考视频
    duration_sec: float = 4.0
    resolution: Tuple[int, int] = (1280, 720)
    fps: int = 24
    seed: Optional[int] = None

class VideoGenResult(BaseModel):
    video_path: Path
    duration_sec: float
    resolution: Tuple[int, int]
    model_name: str
    generation_time_sec: float
    metadata: Dict[str, Any]

class VideoGenProvider(ABC):
    @abstractmethod
    async def generate(self, request: VideoGenRequest) -> VideoGenResult: ...

    @abstractmethod
    async def check_availability(self) -> bool: ...

    @abstractmethod
    def estimated_time(self, request: VideoGenRequest) -> float: ...

class VACEProvider(VideoGenProvider):
    """Alibaba VACE — 支持参考图驱动、视频编辑、风格迁移"""
    def __init__(self, model_path: str, device: str = "cuda"): ...

class OpenSoraProvider(VideoGenProvider):
    """Open-Sora 2.0 — 高质量文生视频"""
    def __init__(self, model_path: str, device: str = "cuda"): ...

class HunyuanVideoProvider(VideoGenProvider):
    """HunyuanVideo — 中文场景优化"""
    def __init__(self, model_path: str, device: str = "cuda"): ...

class ComfyUIProvider(VideoGenProvider):
    """ComfyUI 远程调用 — 用户自定义 pipeline"""
    def __init__(self, comfyui_url: str, workflow_json: dict): ...

class CloudGPUProvider(VideoGenProvider):
    """云端 GPU 代理 — RunPod / 阿里云 PAI"""
    def __init__(self, api_url: str, api_key: str): ...
```

### B-roll 自动建议引擎

```python
class BRollSuggester:
    """分析脚本，识别需要 B-roll 的段落"""

    async def analyze_script(self, script: List[ScriptSegment],
                             available_materials: List[MaterialMeta]
                             ) -> List[BRollSuggestion]:
        """
        1. 对每个脚本段落，判断现有素材是否覆盖
        2. 未覆盖段落，生成 B-roll prompt（中/英双语）
        3. 根据实拍素材的视觉风格，附加 reference image
        4. 返回建议列表，包含优先级和预估生成时间
        """
        ...

class BRollSuggestion(BaseModel):
    segment_index: int
    reason: str                             # "脚本提到'冰川融化'但无相关素材"
    prompt: str                             # 生成 prompt
    reference_image: Optional[Path]         # 风格参考
    priority: Literal["required", "recommended", "optional"]
    estimated_gen_time_sec: float
    recommended_provider: str               # "vace" / "open_sora" / "hunyuan"
```

### Planner 集成

在现有 LangGraph Planner 中新增 `generate_broll` 节点：

```
understand_clips → generate_script → [suggest_broll → generate_broll] → select_bgm → plan_timeline → render_video
```

- `suggest_broll`：分析脚本与素材的匹配度，输出建议
- `generate_broll`：调用 VideoGenProvider 生成，输出带"待确认"标记的素材

### GPU 资源调度

```python
class GPUScheduler:
    """管理本地/云端 GPU 资源"""

    async def submit_job(self, request: VideoGenRequest,
                         provider: str = "auto") -> str:
        """
        auto 模式：
        - 本地 GPU 可用且空闲 → 本地执行
        - 本地 GPU 不可用或队列满 → 转云端
        返回 job_id
        """
        ...

    async def poll_job(self, job_id: str) -> JobStatus: ...
    async def cancel_job(self, job_id: str) -> bool: ...
```

### 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `videogen/provider_base.py` | VideoGenProvider 抽象基类 |
| 新增 | `videogen/vace_provider.py` | VACE 集成 |
| 新增 | `videogen/opensora_provider.py` | Open-Sora 2.0 集成 |
| 新增 | `videogen/hunyuan_provider.py` | HunyuanVideo 集成 |
| 新增 | `videogen/comfyui_provider.py` | ComfyUI 远程调用 |
| 新增 | `videogen/cloud_provider.py` | 云端 GPU 代理 |
| 新增 | `videogen/gpu_scheduler.py` | GPU 资源调度器 |
| 新增 | `videogen/broll_suggester.py` | B-roll 建议引擎 |
| 新增 | `core_nodes/suggest_broll.py` | DAG 节点：B-roll 建议 |
| 新增 | `core_nodes/generate_broll.py` | DAG 节点：B-roll 生成 |
| 修改 | `state/default_pipeline.py` | DAG 中增加 B-roll 节点 |
| 修改 | `orchestrator/planner.py` | Planner 感知 B-roll 建议 |
| 修改 | `config.toml` | 视频生成模型路径、GPU、云端配置 |

### 工时：35 人天

---

## 四、P0-3：内容合规与品牌安全检查

### 合规引擎架构

```python
class ComplianceRule(BaseModel):
    rule_id: str
    rule_type: Literal["logo_detection", "text_scan", "duration_check",
                        "face_check", "custom"]
    config: Dict[str, Any]      # 规则参数
    severity: Literal["block", "warn", "info"]

class BrandSafetyPolicy(BaseModel):
    policy_id: str
    org_id: str
    name: str
    rules: List[ComplianceRule]

class ComplianceResult(BaseModel):
    project_id: str
    policy_id: str
    passed: bool
    violations: List[ComplianceViolation]
    checked_at: datetime

class ComplianceViolation(BaseModel):
    rule_id: str
    severity: str
    timestamp_sec: float        # 违规出现的时间点
    description: str
    screenshot: Optional[Path]  # 违规画面截图
    auto_fixable: bool          # 是否可自动修复（如模糊处理）
```

### 检测实现

```python
class ComplianceEngine:
    def __init__(self):
        self.detectors = {
            "logo_detection": LogoDetector(),       # YOLO + 品牌 logo 库
            "text_scan": TextScanner(),             # 字幕/ASR 文本敏感词
            "duration_check": DurationChecker(),    # 产品展示时长统计
            "face_check": FaceChecker(),            # 人脸检测 + 授权库
        }

    async def check(self, project: Project,
                    policy: BrandSafetyPolicy) -> ComplianceResult:
        """
        1. 从渲染结果中按 1fps 提取关键帧
        2. 并行运行各检测器
        3. 汇总结果，按 severity 排序
        """
        ...

class LogoDetector:
    """基于 YOLO + 品牌 logo embedding 库的竞品 logo 检测"""
    def __init__(self, yolo_model: str = "yolov8n.pt",
                 logo_db_path: str = "brand_logos/"):
        # logo 库：品牌方上传竞品 logo → 提取 CLIP embedding → 建索引
        ...

    async def detect(self, frames: List[np.ndarray],
                     blocked_brands: List[str]) -> List[ComplianceViolation]:
        ...
```

### 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `compliance/engine.py` | 合规引擎主逻辑 |
| 新增 | `compliance/detectors/logo_detector.py` | 竞品 logo 检测 |
| 新增 | `compliance/detectors/text_scanner.py` | 敏感词检测 |
| 新增 | `compliance/detectors/duration_checker.py` | 时长合规 |
| 新增 | `compliance/detectors/face_checker.py` | 人脸授权检查 |
| 新增 | `compliance/models.py` | 合规数据模型 |
| 新增 | `compliance/policy_manager.py` | 品牌安全策略 CRUD |
| 修改 | `orchestrator/planner.py` | 渲染后自动触发合规检查 |

### 工时：25 人天

---

## 五、P1-1：RESTful API + Python SDK

### API 层

```python
# agent_fastapi.py 新增路由
app = FastAPI(title="OpenStoryline API", version="1.0.0")

# 认证
@app.middleware("http")
async def auth_middleware(request, call_next):
    # API Key 认证 / JWT Bearer Token
    ...

# 项目管理
@app.post("/api/v1/projects", response_model=ProjectResponse)
@app.get("/api/v1/projects/{project_id}", response_model=ProjectDetail)
@app.post("/api/v1/projects/{project_id}/materials")
@app.post("/api/v1/projects/{project_id}/instructions")
@app.get("/api/v1/projects/{project_id}/status")
@app.post("/api/v1/projects/{project_id}/render")
@app.get("/api/v1/projects/{project_id}/render/{render_id}")
@app.get("/api/v1/projects/{project_id}/timeline")
@app.get("/api/v1/projects/{project_id}/versions")
@app.post("/api/v1/projects/{project_id}/versions/{version_id}/restore")

# 合规
@app.post("/api/v1/projects/{project_id}/compliance/check")
@app.get("/api/v1/projects/{project_id}/compliance/report")

# 视频生成
@app.post("/api/v1/videogen/generate")
@app.get("/api/v1/videogen/jobs/{job_id}")

# Webhook 管理
@app.post("/api/v1/webhooks")
@app.get("/api/v1/webhooks")
@app.delete("/api/v1/webhooks/{webhook_id}")
```

### Python SDK

```python
# openstoryline-sdk (独立 PyPI 包)
class Client:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
        self.session = httpx.AsyncClient(...)

    def create_project(self, name: str, template_id: Optional[str] = None) -> Project: ...
    def list_projects(self, status: Optional[str] = None) -> List[Project]: ...

class Project:
    def upload_materials(self, paths: List[str]) -> List[MaterialMeta]: ...
    def instruct(self, text: str, wait: bool = True) -> InstructionResult: ...
    def render(self, quality: str = "1080p") -> RenderJob: ...
    def download_render(self, output_path: str) -> Path: ...
    def check_compliance(self, policy_id: str) -> ComplianceResult: ...
    def get_versions(self) -> List[VersionSnapshot]: ...
    def restore_version(self, version_id: str) -> None: ...
```

### 工时：20 人天

---

## 六、P1-2：数据分析与 A/B 测试

### 数据模型

```python
class ABTest(BaseModel):
    test_id: str
    project_id: str
    variants: List[ABVariant]       # 2-5 个变体
    status: Literal["draft", "running", "completed"]
    metrics: List[str]              # ["play_count", "completion_rate", "like_rate"]
    started_at: Optional[datetime]
    winner_variant_id: Optional[str]

class ABVariant(BaseModel):
    variant_id: str
    name: str                       # "版本A: 疑问句标题" / "版本B: 陈述句标题"
    thumbnail: Optional[Path]
    title: Optional[str]
    intro_segment: Optional[str]    # 开头片段的版本 ID
    platform_post_id: Optional[str] # 发布后的平台内容 ID

class ContentMetrics(BaseModel):
    project_id: str
    platform: str
    post_id: str
    play_count: int
    completion_rate: float
    like_count: int
    comment_count: int
    share_count: int
    collected_at: datetime
```

### 平台数据采集

```python
class MetricsCollector(ABC):
    @abstractmethod
    async def fetch_metrics(self, post_id: str) -> ContentMetrics: ...

class DouyinCollector(MetricsCollector):
    """抖音开放平台 API"""
    ...

class BilibiliCollector(MetricsCollector):
    """B站 API"""
    ...

class ManualCollector(MetricsCollector):
    """手动录入（降级方案）"""
    ...
```

### 工时：20 人天

---

## 七、P1-3：视频项目版本控制

### 增量快照

```python
class VersionSnapshot(BaseModel):
    version_id: str
    project_id: str
    parent_version_id: Optional[str]    # 链式结构
    version_number: int
    description: str                    # 自动生成的操作摘要
    node_diffs: Dict[str, NodeDiff]     # 仅记录变化的节点
    created_by: str
    created_at: datetime

class NodeDiff(BaseModel):
    node_id: str
    action: Literal["modified", "added", "removed"]
    old_artifact_id: Optional[str]
    new_artifact_id: Optional[str]
    params_diff: Optional[Dict[str, Any]]

class VersionManager:
    async def create_snapshot(self, project: Project,
                              description: str) -> VersionSnapshot:
        """对比当前状态与上一个快照，只存差异"""
        ...

    async def restore(self, project_id: str, version_id: str) -> ProjectState:
        """从链式快照重建指定版本的完整 ProjectState"""
        ...

    async def diff(self, version_a: str, version_b: str) -> VersionDiff:
        """两个版本之间的差异对比"""
        ...
```

### 存储优化

- artifact 文件使用内容寻址存储（content-addressable，按 hash 存储），相同内容不重复存
- 快照元数据存 PostgreSQL，artifact 文件存对象存储
- 自动清理策略：超过 90 天的中间版本可合并压缩

### 工时：15 人天

---

## 八、P2-1：自动封面生成

```python
class ThumbnailGenerator:
    async def generate_candidates(self, project: Project,
                                  count: int = 5) -> List[ThumbnailCandidate]:
        """
        1. 按 1fps 提取关键帧
        2. 每帧计算质量评分（清晰度 + 构图 + 人脸显著性）
        3. 去重聚类，取 top-N 候选
        4. 叠加标题文字（品牌字体 + 自动排版）
        5. 适配各平台尺寸
        """
        ...

class ThumbnailCandidate(BaseModel):
    frame_sec: float
    quality_score: float
    image_path: Path
    variants: Dict[str, Path]   # {"douyin": path, "bilibili": path, "xiaohongshu": path}
```

### 工时：10 人天

---

## 九、P2-2：插件架构

```python
class PluginHook(str, Enum):
    ON_PROJECT_CREATED = "on_project_created"
    ON_BEFORE_RENDER = "on_before_render"
    ON_AFTER_RENDER = "on_after_render"
    ON_COMPLIANCE_CHECK = "on_compliance_check"
    ON_TIMELINE_MODIFIED = "on_timeline_modified"
    ON_BEFORE_PUBLISH = "on_before_publish"

class PluginManifest(BaseModel):
    name: str
    version: str
    author: str
    description: str
    hooks: List[PluginHook]
    entry_point: str            # Python module path

class PluginManager:
    def __init__(self, plugin_dirs: List[Path]):
        self.plugins: Dict[str, PluginInstance] = {}

    def load_plugins(self) -> None:
        """扫描插件目录，加载 manifest，注册 hook"""
        ...

    async def trigger_hook(self, hook: PluginHook, context: Dict[str, Any]) -> None:
        """按优先级依次执行注册在该 hook 上的插件"""
        ...
```

### 安全隔离

- 插件运行在独立 subprocess 中，通过 JSON-RPC 通信
- 资源限制：最大执行时间 30s、最大内存 512MB
- 权限声明：插件需在 manifest 中声明所需权限（文件读写、网络访问等）

### 工时：15 人天

---

## 十、风险矩阵

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| PostgreSQL 迁移数据丢失 | 高 | 双写期 + 完整迁移测试 + 回滚脚本 |
| 视频生成模型 VRAM 不足 | 高 | 云端 fallback + 模型量化（INT8/FP16）+ 分辨率自适应 |
| VACE/Open-Sora API 不稳定 | 高 | 多 provider 自动切换 + 重试队列 + 生成结果缓存 |
| 合规检测误报率过高 | 中 | 先 warn 不 block + 人工反馈闭环 + 持续优化阈值 |
| 编辑锁死锁/超时 | 中 | TTL 自动释放 + 心跳机制 + 管理员强制解锁 |
| API 滥用/DDoS | 中 | Rate limiting + API Key 配额 + 请求队列 |
| 插件安全沙箱逃逸 | 中 | subprocess 隔离 + seccomp 限制 + 代码审查 |
| 平台数据 API 限流/下线 | 中 | 手动录入降级 + 本地缓存 + 多数据源冗余 |

---

## 十一、总工时

| 模块 | 人天 |
|------|------|
| P0-1 团队协作 + 权限 | 40 |
| P0-2 AI 视频生成集成 | 35 |
| P0-3 合规检查引擎 | 25 |
| P1-1 API + SDK | 20 |
| P1-2 数据分析 + A/B 测试 | 20 |
| P1-3 版本控制 | 15 |
| P2-1 封面生成 | 10 |
| P2-2 插件架构 | 15 |
| **总计** | **180 人天** |

建议 4 人团队，分两条线并行：
- **线 A（协作 + API）**：2 人，P0-1 → P1-1 → P1-3，约 10-12 周
- **线 B（AI 生成 + 合规）**：2 人，P0-2 → P0-3 → P1-2 → P2-1 → P2-2，约 12-14 周

总工期约 14-16 周（含集成测试和 buffer）。
