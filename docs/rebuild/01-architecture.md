# 整体架构 + 项目结构

---

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React)                       │
│  React Router → /session/{id}                            │
│  Zustand stores → session / chat / pipeline / media      │
│  WebSocket client → real-time events                     │
└────────────────────────┬────────────────────────────────┘
                         │ REST + WebSocket
┌────────────────────────┴────────────────────────────────┐
│                    API Layer (FastAPI)                    │
│  routers/sessions.py   routers/media.py                  │
│  routers/chat.py       routers/pipeline.py               │
│  middleware: rate limit / CORS / auth                     │
│  deps: get_db() / get_session() / get_llm()              │
└────────────┬───────────────────────┬────────────────────┘
             │                       │
┌────────────┴──────────┐ ┌─────────┴───────────────────┐
│   Agent (LangChain)    │ │    Orchestrator              │
│  LLM multi-provider    │ │  Planner (LLM-based)        │
│  Tool binding           │ │  Executor (layer-parallel)  │
│  Middleware (log/error) │ │  DAG (enhanced TaskDAG)     │
└────────────┬──────────┘ └─────────┬───────────────────┘
             │                       │
┌────────────┴───────────────────────┴────────────────────┐
│                    Skill Layer                            │
│  BaseSkill → execute() / split_into_subtasks()           │
│  16 base skills (LoadMedia, SplitShots, ASR, ...)        │
│  BatchExecutor → asyncio.gather + Semaphore              │
│  Custom skills (user-created, markdown-based)            │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────┐
│                    Data Layer                             │
│  SQLAlchemy Async → sessions, skill_executions,          │
│                      llm_interactions, chat_messages,     │
│                      media_files                          │
│  Artifact Store → filesystem (outputs/{session}/{skill}) │
│  Alembic → schema migrations                             │
└─────────────────────────────────────────────────────────┘
```

## 项目目录结构

```
firered-openstoryline/
├── pyproject.toml                # 项目元数据 + 依赖
├── config.toml                   # 运行配置
├── alembic.ini                   # DB 迁移配置
├── alembic/versions/             # 迁移脚本
│
├── src/open_storyline/
│   ├── config.py                 # Pydantic 配置模型
│   │
│   ├── core/                     # ── 核心抽象 ──
│   │   ├── skill.py              # BaseSkill ABC
│   │   ├── skill_registry.py     # Skill 发现 + 注册
│   │   ├── pipeline.py           # Pipeline 定义
│   │   ├── session.py            # Session 模型
│   │   └── concurrency.py        # BatchExecutor
│   │
│   ├── db/                       # ── 数据库层 ──
│   │   ├── engine.py             # SQLAlchemy async engine
│   │   ├── models.py             # ORM 模型
│   │   └── repositories.py       # 数据访问层
│   │
│   ├── skills/                   # ── Skill 实现 ──
│   │   ├── base_skills/          # 16 个核心 Skill
│   │   │   ├── load_media.py
│   │   │   ├── split_shots.py
│   │   │   ├── understand_clips.py   # 支持批量并行
│   │   │   ├── local_asr.py          # 支持批量并行
│   │   │   ├── generate_voiceover.py  # 支持批量并行
│   │   │   └── ...
│   │   └── custom_skills/        # 用户自建 Skill
│   │
│   ├── orchestrator/             # ── 编排层 ──
│   │   ├── planner.py            # LLM 意图规划
│   │   ├── executor.py           # 分层并行执行器
│   │   └── dag.py                # DAG 依赖图
│   │
│   ├── agent/                    # ── Agent 层 ──
│   │   ├── llm_client.py         # 多 Provider LLM 抽象
│   │   ├── agent.py              # LangChain Agent
│   │   └── middleware.py         # 日志 / 错误处理
│   │
│   ├── api/                      # ── API 层 ──
│   │   ├── app.py                # FastAPI 工厂
│   │   ├── deps.py               # 依赖注入
│   │   ├── middleware.py          # 限流 / CORS
│   │   └── routers/
│   │       ├── sessions.py       # Session CRUD
│   │       ├── media.py          # 媒体上传下载
│   │       ├── chat.py           # WebSocket 聊天
│   │       ├── pipeline.py       # Pipeline 状态 + 恢复
│   │       └── skills.py         # Skill 列表 + 详情
│   │
│   ├── rendering/                # ── 渲染 (保留) ──
│   │   ├── ffmpeg_renderer.py
│   │   ├── subtitle_templates.py
│   │   └── timeline_overview.py
│   │
│   ├── storage/                  # ── 文件存储 ──
│   │   ├── artifact_store.py
│   │   └── file.py
│   │
│   └── utils/                    # ── 工具 (保留) ──
│       ├── prompts.py
│       ├── parse_json.py
│       ├── ffmpeg_utils.py
│       ├── media_handler.py
│       └── logging.py
│
├── frontend/                     # ── 前端 (React) ──
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── routes/
│       │   ├── HomePage.tsx       # Session 列表
│       │   └── SessionPage.tsx    # /session/{id} 编辑工作台
│       ├── stores/                # Zustand
│       ├── components/
│       │   ├── chat/              # 聊天面板
│       │   ├── media/             # 媒体库
│       │   ├── pipeline/          # DAG 可视化
│       │   └── timeline/          # 时间线预览
│       ├── hooks/
│       │   └── useWebSocket.ts
│       └── api/
│           └── client.ts          # 类型化 API 客户端
│
├── prompts/                      # 保留
├── resource/                     # 保留
└── cli.py                        # 保留
```

## 与当前架构的关键差异

| 维度 | 当前 | 重建后 |
|------|------|--------|
| 入口 | `agent_fastapi.py` (3000行) | `api/app.py` + 5 个 router 文件 |
| 节点执行 | MCP Server 跨进程调用 | 进程内直接 async 函数调用 |
| 状态 | JSON 文件 + 内存 dict | SQLAlchemy ORM + Alembic 迁移 |
| Session | 内存对象，无 URL | DB 持久化，`/session/{id}` URL |
| 并发 | 无（节点内串行） | Skill 级别 subtask 拆分 + asyncio.gather |
| 前端 | 原生 HTML/JS (493行) | React + TypeScript + Vite |
| 日志 | 无持久化 | `llm_interactions` 表全量记录 |
