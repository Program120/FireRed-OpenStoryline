# 分阶段实施路线

---

## Phase 1: 基础架构（第 1-2 周）

### 目标
搭建新项目骨架，核心抽象可用，DB 就绪。

### 交付物
- [ ] 新目录结构 (`core/`, `db/`, `skills/`, `api/`, `agent/`, `orchestrator/`)
- [ ] `core/skill.py` — BaseSkill ABC + SkillMeta + SkillContext + SkillResult
- [ ] `core/concurrency.py` — BatchExecutor
- [ ] `core/skill_registry.py` — Skill 注册表
- [ ] `db/engine.py` — SQLAlchemy async engine
- [ ] `db/models.py` — 所有 ORM 模型（含 users、refresh_tokens、user_model_configs、user_preferences、skill_registry、user_installed_skills 等认证/Skill 仓库相关表）
- [ ] `alembic/` — 初始迁移脚本
- [ ] `orchestrator/dag.py` — 从 V1 `task_dag.py` 迁移
- [ ] `api/middleware.py` — 认证中间件（JWT 验证 + `get_current_user` 依赖注入）
- [ ] 管理员种子用户初始化脚本（首次启动时自动创建 admin 账号）
- [ ] 单元测试：DAG 拓扑排序、BatchExecutor 并发、DB CRUD

> **关键决策**：认证中间件和 DB 表在 Phase 1 就绑定，确保 Phase 3 的所有 router 从第一天起就使用 `user_id` 过滤，避免后期补认证的改造成本。

### 验收标准
```python
# 能跑通
skill = LoadMediaSkill()
result = await BatchExecutor().execute_skill(skill, ctx, inputs)
assert result.success
# DB 中有对应的 skill_execution 记录
```

---

## Phase 2: Skill 迁移（第 2-4 周）

### 目标
将 16 个节点逐一迁移为 Skill，验证输入输出一致。

### 顺序（按依赖关系）
1. **Week 2**: LoadMedia, SearchMedia, SplitShots（简单，无 LLM）
2. **Week 3**: LocalASR（+并发）, SpeechRoughCut, UnderstandClips（+并发）
3. **Week 3-4**: FilterClips, GroupClips, GenerateScript, ScriptTemplateRec
4. **Week 4**: GenerateVoiceover（+并发）, SelectBGM, RecommendTransition, RecommendText
5. **Week 4**: PlanTimeline, RenderVideo

### 每个 Skill 的迁移步骤
1. 创建 `skills/base_skills/{name}.py`
2. 复制 `process()` 核心逻辑
3. 替换 `node_state.llm` → `ctx.llm`（直接调用）
4. 添加 DB 记录（`ctx.db.add(LLMInteraction(...))`）
5. 对支持并发的 Skill 实现 `split_into_subtasks()` + `merge_subtask_results()`
6. 验证：输入输出与旧节点一致

### 验收标准
```python
# 16 个 Skill 全部注册且可执行
assert len(SkillRegistry.list()) == 16

# UnderstandClips 并发执行
result = await executor.execute_skill(understand_clips, ctx, {"clips": 20_clips})
assert len(result.data["clip_captions"]) == 20
# DB 中有 4 个 subtask execution 记录（batch_size=5, 20/5=4）
```

---

## Phase 3: API 模块化（第 3-5 周）

### 目标
将 `agent_fastapi.py` 拆分为模块化 API，去除 MCP 依赖。

### 交付物
- [ ] `api/app.py` — FastAPI 工厂，lifespan 管理
- [ ] `api/deps.py` — 依赖注入（get_db, get_session, get_llm）
- [ ] `api/middleware.py` — 限流、CORS
- [ ] `api/routers/sessions.py` — Session CRUD
- [ ] `api/routers/media.py` — 媒体上传/下载/缩略图
- [ ] `api/routers/chat.py` — WebSocket 聊天（集成 Agent + Orchestrator）
- [ ] `api/routers/pipeline.py` — Pipeline 状态、恢复、日志
- [ ] `api/routers/skills.py` — Skill 列表/详情
- [ ] `api/routers/auth.py` — 注册/登录/刷新/登出（使用 Phase 1 的认证中间件）
- [ ] `api/routers/admin.py` — 管理员 Skill 仓库管理
- [ ] `agent/llm_client.py` — 多 Provider LLM 抽象
- [ ] `agent/agent.py` — LangChain Agent（tools = Skills）
- [ ] `orchestrator/planner.py` — LLM 意图规划
- [ ] `orchestrator/executor.py` — 分层并行执行

### 关键改造
- Agent 的 tools 不再通过 MCP 注册，而是直接绑定 Skill 函数
- WebSocket handler 从 3000 行中提取为独立的 `chat.py`
- Session 状态从内存 dict 改为 DB 查询

### 验收标准
```bash
# 单进程启动（不需要 MCP Server）
uvicorn open_storyline.api.app:create_app --factory --port 8005

# REST API 可用
curl http://localhost:8005/api/sessions
curl -X POST http://localhost:8005/api/sessions

# WebSocket 可用
wscat -c ws://localhost:8005/ws/sessions/{id}/chat
```

---

## Phase 4: 前端重建（第 4-7 周）

### 目标
React 前端完整替代当前 HTML/JS。

### 交付物
- [ ] Week 4-5: 脚手架 + 路由 + 首页 + Session 列表
- [ ] Week 4-5: 登录页 (`/login`) + 注册页 (`/register`) + ProtectedRoute
- [ ] Week 5-6: 聊天面板 + WebSocket 集成 + 消息渲染
- [ ] Week 6: 媒体库 + 拖拽上传 + 缩略图
- [ ] Week 6-7: Skill 执行卡片 + 进度条 + 详细日志面板
- [ ] Week 7: Pipeline DAG 可视化（ReactFlow）
- [ ] Week 7: 恢复控制（从任意节点重新执行）
- [ ] Week 7: 设置页 (`/settings`) + 模型配置管理
- [ ] Week 7: Skill 管理 (`/settings/skills`) + Skill 市场 (`/skills`)

### 验收标准
- 浏览器打开 `/session/{id}` → WebSocket 连接 → 收到 snapshot → UI 完整恢复
- 上传视频 → 发消息 → 看到 Skill 卡片实时进度 → 展开详细日志
- 刷新页面 → URL 不变 → 自动恢复到刷新前的状态

---

## Phase 5: 集成 + 切换（第 8-10 周）

### 目标
替换旧系统，端到端验证。

### 交付物
- [ ] 删除 `agent_fastapi.py`（旧入口）
- [ ] 删除 `mcp/` 目录（MCP 层）
- [ ] 删除 `web/` 目录（旧前端）
- [ ] 更新 `Dockerfile` 和 `docker-compose.yml`
- [ ] 更新 `README.md`
- [ ] 端到端测试：上传视频 → 对话剪辑 → 增量编辑 → 导出视频

---

## Phase 6: 前瞻性 Hooks（第 10-11 周+，持续）

| 功能 | 预留位置 | 需要时实现 |
|------|----------|-----------|
| 多模型 Provider | `agent/llm_client.py` 接口已抽象 | V3 填入 Anthropic/Gemini/Ollama adapter |
| 团队协作 | `db/models.py` 预留 `team_id` 字段 | V4 加 RBAC + 审计表 |
| 插件系统 | `core/skill_registry.py` 支持外部包扫描 | V4 开放第三方 Skill 注册 |
| API/SDK | `api/routers/` 已是标准 REST | V4 加认证 + rate limit + 文档 |
| 实时预览 | `rendering/timeline_overview.py` 已有 | 前端加 video player 组件 |

---

## 风险管理

| 风险 | 缓解 |
|------|------|
| 16 个 Skill 迁移工作量大 | 核心逻辑复制粘贴，只改接口层 |
| 前端重建耗时 | Phase 3 API 完成后可用 Postman/curl 测试后端，前端不阻塞后端 |
| SQLAlchemy 学习曲线 | 使用 async session + 简单 ORM pattern |
| Agent 集成复杂度 | 保留 LangChain 用法，只改 tool 注册方式 |
| 旧功能丢失 | 每个 Phase 都有验收标准，确认功能不退化 |
