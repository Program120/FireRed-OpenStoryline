# V1 研发技术方案

---

## 一、架构演进总览

| 维度 | V0 | V1 |
|------|-----|-----|
| Agent 架构 | 单 ReAct Agent | Planner-Worker (LangGraph StateGraph) |
| 状态管理 | ArtifactStore (线性 JSON) | ProjectState (结构化 + DAG 版本快照) |
| 依赖管理 | NodeManager + 拦截器递归补全 | DAG TaskGraph + dirty 传播 + 并行 Worker |
| 用户配置 | 无持久化 | UserProfile + StyleTemplate + SQLite |
| 渲染引擎 | MoviePy | FFmpeg CLI (filter_complex) |
| 预览 | 无 | Timeline 概览 + 480p 快预览 |

---

## 二、P0-1: 增量编辑核心数据结构

### ProjectState

```python
class NodeStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    DIRTY = "dirty"
    RUNNING = "running"
    FAILED = "failed"

class NodeSnapshot(BaseModel):
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    artifact_id: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    completed_at: Optional[float] = None
    error_msg: Optional[str] = None

class ProjectState(BaseModel):
    project_id: str
    session_id: str
    version: int = 0
    nodes: Dict[str, NodeSnapshot] = {}
    user_overrides: Dict[str, Dict[str, Any]] = {}

    def mark_dirty(self, node_id, dag) -> List[str]:
        """标记 dirty 并传播到所有下游"""
        ...

    @staticmethod
    def compute_input_hash(params) -> str:
        """输入参数哈希，判断是否需重跑"""
        ...
```

### TaskDAG

```python
class TaskDAG(BaseModel):
    nodes: List[str]
    edges: List[DAGEdge]

    def topological_sort(self) -> List[str]: ...
    def get_downstream(self, node_id) -> List[str]: ...
    def compute_execution_plan(self, dirty_nodes) -> List[List[str]]:
        """返回分层并行执行计划: [[可并行节点], ...]"""
        ...
```

### Planner-Worker StateGraph

```python
class OrchestratorState(TypedDict):
    project_state: ProjectState
    dag: TaskDAG
    user_message: str
    execution_plan: List[List[str]]
    current_layer: int
    results: Dict[str, Any]
    final_response: str

def build_orchestrator_graph() -> StateGraph:
    graph = StateGraph(OrchestratorState)
    graph.add_node("planner", planner_node)        # LLM 分析意图 → dirty 传播 → 生成计划
    graph.add_node("worker_dispatcher", worker_node) # 按层并行执行
    graph.add_node("summarizer", summarizer_node)    # 汇总结果
    # planner → worker (循环) → summarizer → END
    ...
```

### 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `state/project_state.py` | ProjectState + NodeSnapshot |
| 新增 | `state/task_dag.py` | TaskDAG + 增量计划 |
| 新增 | `state/default_pipeline.py` | 默认 DAG 定义 |
| 新增 | `orchestrator/planner.py` | Planner-Worker StateGraph |
| 新增 | `orchestrator/worker.py` | Worker 执行器 |
| 修改 | `agent.py` | `build_agent()` → `build_orchestrator()` |
| 修改 | `storage/agent_memory.py` | 增加 ProjectState 存取 |
| 修改 | `mcp/hooks/node_interceptors.py` | 适配 ProjectState |
| 修改 | `agent_fastapi.py` | ChatSession 持有 ProjectState |
| 保留 | 所有 `core_nodes/*.py` | 节点实现不改动 |

### 迁移策略

- 阶段一：新增 ProjectState/DAG，`build_agent()` 保持可用，增加 `USE_V1_ORCHESTRATOR` 开关
- 阶段二：新路由 `/api/v1/sessions` 走 StateGraph，旧路由不变
- 阶段三：默认 V1，V0 标记 deprecated

### 工时：25 人天

---

## 三、P0-2: 风格模板 + 会话持久化

### UserProfile + StyleTemplate

```python
class StyleTemplate(BaseModel):
    template_id: str
    name: str
    subtitle: SubtitlePreference
    audio: AudioPreference
    bgm_style: Dict[str, Any]
    rhythm_preference: str
    intro_template: Optional[str]
    outro_template: Optional[str]

class UserProfile(BaseModel):
    user_id: str
    default_template_id: Optional[str]
    templates: Dict[str, StyleTemplate]
    learned_preferences: Dict[str, Any]
```

### Session 持久化：SQLite

```python
class SessionDB:
    def __init__(self, db_path):
        # SQLite + WAL mode
    def save_session(self, session_id, data): ...
    def load_session(self, session_id) -> Optional[dict]: ...
    def cleanup_old(self, max_age_seconds): ...
```

### 工时：12 人天

---

## 四、P1-1: 轻量预览

- Timeline 概览 API（结构化 JSON，无需渲染）
- 480p 快预览模式（CRF=28, FPS=15, 跳过字幕烧录）
- Planner 支持分段指令（交换/删除/移动/重选素材）

### 工时：8 人天

---

## 五、P1-2: 渲染质量提升

### FFmpeg 直接渲染

```python
class FFmpegRenderer:
    def build_filter_complex(self, segments, subtitles, audio, w, h) -> str: ...
    def render(self, ..., output_path, crf=23, fps=25) -> Path: ...
```

### 动态音频闪避

```python
def build_audio_ducking_filter(bgm_idx, vo_idx, ...) -> str:
    # sidechaincompress 实现配音段自动压低 BGM
```

### 8 种预设字幕模板

经典白字、电影黄字、极简灰、粗描边、柔和底框、Vlog活力、暗色模式、优雅衬线

### 工时：15 人天

---

## 六、风险矩阵

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| LangGraph 与 MCP tool 集成 | 高 | Worker 层仍通过 MCP Client 调 tool |
| Planner LLM 意图识别不准 | 高 | 规则引擎 baseline + LLM 增强; fallback 全量重跑 |
| DAG 并行资源竞争 | 中 | Worker 信号量控制最大并发 |
| FFmpeg filter_complex 调试难 | 高 | 分层构建+单元测试; 保留 MoviePy fallback |
| LangChain Message 序列化 | 中 | 使用标准 `messages_to_dict`; 增加回归测试 |

---

## 七、总工时

| 模块 | 人天 |
|------|------|
| P0-1 增量编辑 | 25 |
| P0-2 风格模板 + 持久化 | 12 |
| P1-1 轻量预览 | 8 |
| P1-2 渲染质量 | 15 |
| **总计** | **60 人天** |

建议 2 人并行，总工期约 5-6 周。
