# 保留 vs 重写 vs 删除

---

## 保留（直接复用，微调适配）

| 文件/模块 | 理由 |
|-----------|------|
| `config.py` | Pydantic 配置模型设计良好，新增 DB/前端配置段即可 |
| `state/task_dag.py` | DAG 实现干净，移到 `orchestrator/dag.py` |
| `rendering/ffmpeg_renderer.py` | 独立模块，接口清晰 |
| `rendering/subtitle_templates.py` | 8 种预设样式 |
| `rendering/timeline_overview.py` | 结构化时间线 |
| `utils/prompts.py` | Prompt 模板加载 |
| `utils/parse_json.py` | 健壮的 JSON 解析 |
| `utils/ffmpeg_utils.py` | FFmpeg 工具函数 |
| `utils/media_handler.py` | 媒体扫描 |
| `utils/logging.py` | 彩色日志 |
| `storage/file.py` | FileCompressor |
| `nodes/node_schema.py` | Pydantic Schema (Media, Clip, Timeline 等) |
| **16 个节点的核心处理逻辑** | 算法/业务逻辑是正确的，需要包装为 Skill 接口 |
| `prompts/` 目录 | 所有 prompt 模板 |
| `resource/` 目录 | BGM、字体、模板文件 |
| `cli.py` | CLI 入口，改为调用新 API |

## 重写（保留概念，重新实现）

| 文件/模块 | 原因 | 重建为 |
|-----------|------|--------|
| `agent_fastapi.py` (3000行) | 上帝文件无法维护 | `api/routers/*.py` 5 个模块化文件 |
| `nodes/core_nodes/base_node.py` | base64 打包/MCP 耦合 | `core/skill.py` BaseSkill ABC |
| `orchestrator/planner.py` | regex 意图匹配脆弱 | LLM 意图识别 + DAG 规划 |
| `storage/agent_memory.py` | JSON 文件存储 | DB repositories + artifact_store |
| `storage/session_db.py` | raw SQL | SQLAlchemy ORM + Alembic |
| `agent.py` | V0/V1 双路径 | 统一的 `agent/agent.py` |
| `web/` 前端 | 原生 HTML/JS | React + TypeScript + Vite |

## 删除（不再需要）

| 文件/模块 | 理由 |
|-----------|------|
| `mcp/server.py` | Skill 改为进程内直接调用，不需要独立 MCP Server |
| `mcp/register_tools.py` | Node → MCP Tool 转换不再需要 |
| `mcp/sampling_handler.py` | MCP 采样（LLM 代理调用）不再需要，Skill 直接调 LLM |
| `mcp/sampling_requester.py` | 同上 |
| `mcp/hooks/node_interceptors.py` | 依赖注入改为 SkillContext，不需要 before/after hook |
| `mcp/hooks/chat_middleware.py` | 中间件改为 FastAPI middleware + Skill 内部日志 |
| `storage/session_manager.py` | 线程清理改为 DB 生命周期管理 |
| `storage/user_profile.py` | 合并到 sessions DB 表 |
| `state/project_state.py` | 替换为 DB `skill_executions` 表 |
| `state/default_pipeline.py` | Pipeline 从 Skill 的 `depends_on` 自动构建 |
| `run.sh` | 不再需要启动两个进程 |
| `hf_space.sh` | 替换为 Docker Compose |

## 迁移注意事项

### 16 个节点 → 16 个 Skill 的迁移模式

每个节点的 `process()` 方法内部逻辑保留，包装层变化：

```python
# 当前 (BaseNode)
class UnderstandClipsNode(BaseNode):
    meta = NodeMeta(name="understand_clips", ...)
    input_schema = UnderstandClipsInput

    async def process(self, node_state: NodeState, inputs: Dict) -> Any:
        clips = inputs["split_shots"]["clips"]
        for clip in clips:  # 串行
            caption = await node_state.llm.complete(...)
        return {"clip_captions": captions}

# 重建后 (BaseSkill)
class UnderstandClipsSkill(BaseSkill):
    meta = SkillMeta(skill_id="understand_clips", supports_batching=True, ...)

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        clips = inputs["clips"]
        for clip in clips:
            caption = await ctx.llm.complete_vision(...)  # 直接调 LLM
            await ctx.db.add(LLMInteraction(...))         # 记录交互
        return SkillResult(success=True, data={"clip_captions": captions})

    async def split_into_subtasks(self, inputs):
        # 拆分为 batch
        ...
```

**核心业务逻辑（VLM 调用、caption 解析、错误处理）完全不变**。变化的只是：
1. `node_state.llm` → `ctx.llm`（直接调用，不走 MCP）
2. `ArtifactStore.save_result()` → `ctx.db.add()`（DB 记录）
3. 新增 `split_into_subtasks()` 和 `merge_subtask_results()`（并发支持）
