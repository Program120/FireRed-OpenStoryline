# OpenStoryline 重建计划概览

> 基于当前 V0+V1 的能力和发现的架构问题，制定从零重建的系统性方案。

---

## 为什么重建

当前项目有几个**架构级**问题无法通过渐进式修补解决：

1. **`agent_fastapi.py` 是 3000 行的上帝文件** — 包含路由、限流、Session 管理、WebSocket、Agent 构建、媒体处理，无法维护
2. **MCP 协议层增加不必要的复杂度** — 节点在本地执行却走 HTTP + base64 编码传输，`report_progress(0,0)` 触发 `ClosedResourceError`
3. **节点内部串行处理** — `UnderstandClips` 20 个片段逐个调 VLM，`SpeechRoughCut` 59 句逐句调 LLM
4. **状态管理是 JSON 文件 + 内存** — 进程重启丢失一切，无法恢复到任意节点
5. **前端是原始 HTML/JS** — 无法支撑复杂交互（Pipeline 可视化、日志面板、Session 路由）
6. **Session 无 URL 隔离** — 刷新即丢失上下文

## 重建五大目标

| # | 目标 | 说明 |
|---|------|------|
| 1 | **前端全新设计** | React + TypeScript + Vite，Session URL 路由，Pipeline 可视化 |
| 2 | **节点 → Skill 并发** | 每个 Skill 可拆子任务并行执行，大任务不再串行 |
| 3 | **数据库驱动的状态管理** | 每个节点执行状态、LLM 交互全部持久化，支持任意节点恢复 |
| 4 | **Session 隔离 + URL 路由** | 每个会话独立 URL，刷新后精确恢复 |
| 5 | **前瞻性架构** | 预留多模型 Provider、团队协作、插件系统、API/SDK 接口 |
| 6 | **用户认证 + 数据隔离** | 用户注册/登录，会话/媒体/模型配置用户间完全隔离 |

## 文档结构

```
docs/rebuild/
├── 00-overview.md          ← 本文
├── 01-architecture.md      # 整体架构 + 项目结构
├── 02-core-abstractions.md # Skill、Pipeline、Session 核心抽象
├── 03-database-schema.md   # 数据库模型设计
├── 04-api-design.md        # REST + WebSocket API 设计
├── 05-frontend.md          # 前端架构 + 关键组件
├── 06-concurrency.md       # Skill 并发模型
├── 07-keep-vs-rewrite.md   # 保留 vs 重写 vs 删除清单
├── 08-migration-plan.md    # 分阶段实施路线
├── 09-user-auth.md         # 用户认证与数据隔离
└── 10-skill-registry.md    # Skill 仓库设计
```

## 技术栈选型

| 层级 | 当前 | 重建后 |
|------|------|--------|
| 前端 | 原生 HTML/JS/CSS | React + TypeScript + Vite + Tailwind |
| 路由 | 无 | React Router (`/session/{id}`) |
| 状态管理 | 无 | Zustand |
| 后端框架 | FastAPI (单文件) | FastAPI (模块化 routers) |
| ORM | 无 (raw SQLite) | SQLAlchemy Async + Alembic |
| 数据库 | SQLite 文件 | SQLite → 可升级 PostgreSQL |
| 工具协议 | MCP (跨进程) | 直接 async 函数调用 (进程内) |
| 节点抽象 | BaseNode + NodeMeta | BaseSkill + 并发拆分 |
| 渲染 | MoviePy + FFmpeg | FFmpeg 直接调用 (保留) |

## 时间线估算

| 阶段 | 内容 | 周数 |
|------|------|------|
| Phase 1 | 基础架构 (Skill ABC + DB + 项目结构) | 1-2 |
| Phase 2 | 16 个 Skill 迁移 (含并发改造) | 2-4 |
| Phase 3 | API 模块化 (从 3000 行拆分) | 3-5 |
| Phase 4 | 前端重建 (React 全套) | 4-7 |
| Phase 5 | 集成 + 切换 | 7-8 |
| Phase 6 | 前瞻性 hooks (多模型/插件) | 持续 |
