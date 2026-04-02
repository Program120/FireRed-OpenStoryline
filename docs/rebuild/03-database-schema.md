# 数据库模型设计

> **本文档是所有数据库表的唯一权威来源（Single Source of Truth）。** 09-user-auth.md 和 10-skill-registry.md 中的表定义应视为说明性引用，以本文档为准。

---

## 设计原则

1. **DB 是唯一的状态真相源** — 不再有内存 dict 或 JSON 文件作为主存储
2. **每个 Skill 执行全量记录** — 支持从任意节点恢复
3. **LLM 交互全量记录** — 支持日志查看、成本分析、replay
4. **子任务级记录** — 并发 Skill 的每个 batch 独立追踪

## ER 图

```
users ──────┬──── sessions ─────────┬──── chat_messages
            │                       ├──── media_files
            │                       └──── pipeline_states ──── skill_executions ──── llm_interactions
            │                                                        │
            │                                                 subtask_executions (self-ref)
            ├──── user_model_configs
            ├──── user_preferences
            └──── refresh_tokens

skill_registry (管理员维护，元数据索引，文件在本地 skills/ 目录)
```

## 表结构

### sessions（会话）

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,          -- UUID
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    display_name TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',  -- active / paused / completed / archived
    lang         TEXT NOT NULL DEFAULT 'zh',
    config_json  TEXT NOT NULL DEFAULT '{}', -- 模型配置快照 (LLM/VLM/TTS)
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
```

### pipeline_states（管道状态版本）

每次执行计划变更时创建新版本，支持回溯。

```sql
CREATE TABLE pipeline_states (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    version      INTEGER NOT NULL,            -- 自增版本号
    dag_json     TEXT NOT NULL,                -- DAG 边列表 JSON
    dirty_skills TEXT NOT NULL DEFAULT '[]',   -- 本次变更标脏的 skill 列表
    trigger_text TEXT NOT NULL DEFAULT '',     -- 触发此次执行的用户消息
    created_at   REAL NOT NULL,
    UNIQUE(session_id, version)
);

CREATE INDEX idx_pipeline_session ON pipeline_states(session_id);
```

### skill_executions（Skill 执行记录）

**核心表——实现任意节点恢复的关键。**

```sql
CREATE TABLE skill_executions (
    execution_id       TEXT PRIMARY KEY,       -- UUID
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    pipeline_version   INTEGER NOT NULL,
    skill_id           TEXT NOT NULL,           -- "understand_clips"
    status             TEXT NOT NULL DEFAULT 'pending',
                       -- pending / running / completed / failed / skipped
    input_hash         TEXT,                    -- 输入参数摘要（用于变更检测）
    inputs_ref         TEXT,                    -- 输入制品路径
    outputs_ref        TEXT,                    -- 输出制品路径
    error_message      TEXT,
    started_at         REAL,
    completed_at       REAL,
    duration_ms        INTEGER,

    -- 子任务相关
    parent_execution_id TEXT REFERENCES skill_executions(execution_id),
    batch_index        INTEGER,                 -- 子任务批次号
    batch_total        INTEGER,                 -- 总批次数

    created_at         REAL NOT NULL
);

CREATE INDEX idx_skill_exec_session ON skill_executions(session_id, pipeline_version);
CREATE INDEX idx_skill_exec_parent ON skill_executions(parent_execution_id);
CREATE INDEX idx_skill_exec_skill ON skill_executions(skill_id);
```

### llm_interactions（LLM 交互记录）

**每次 LLM 调用全量记录，支持日志查看和成本统计。**

```sql
CREATE TABLE llm_interactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id     TEXT NOT NULL REFERENCES skill_executions(execution_id),
    session_id       TEXT NOT NULL,
    user_id          TEXT NOT NULL REFERENCES users(user_id),  -- 便于按用户统计成本
    provider         TEXT NOT NULL,             -- "openai" / "anthropic" / "qwen"
    model            TEXT NOT NULL,             -- "qwen3.5-plus"
    role             TEXT NOT NULL DEFAULT 'chat', -- chat / vlm / tts
    system_prompt    TEXT,
    user_prompt      TEXT,
    response_text    TEXT,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    latency_ms       INTEGER,
    created_at       REAL NOT NULL
);

CREATE INDEX idx_llm_exec ON llm_interactions(execution_id);
CREATE INDEX idx_llm_session ON llm_interactions(session_id, created_at);
CREATE INDEX idx_llm_user ON llm_interactions(user_id);
```

### chat_messages（聊天消息）

替代当前内存中的 `lc_messages` 列表。

```sql
CREATE TABLE chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    role         TEXT NOT NULL,                -- system / user / assistant / tool
    content      TEXT NOT NULL,
    tool_call_id TEXT,
    tool_name    TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',  -- 附件、模型信息等
    created_at   REAL NOT NULL
);

CREATE INDEX idx_chat_session ON chat_messages(session_id, created_at);
```

### media_files（媒体文件）

```sql
CREATE TABLE media_files (
    media_id     TEXT PRIMARY KEY,             -- UUID
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    filename     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'unknown', -- video / image / audio / unknown
    file_path    TEXT NOT NULL,
    thumb_path   TEXT,
    file_size    INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'uploaded', -- uploaded / processing / ready
    metadata_json TEXT NOT NULL DEFAULT '{}',  -- fps, width, height, duration
    created_at   REAL NOT NULL
);

CREATE INDEX idx_media_session ON media_files(session_id);
```

### users（用户）

```sql
CREATE TABLE users (
    user_id      TEXT PRIMARY KEY,          -- UUID
    username     TEXT NOT NULL UNIQUE,
    email        TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_url   TEXT,
    role         TEXT NOT NULL DEFAULT 'user',  -- 'admin' / 'user'
    status       TEXT NOT NULL DEFAULT 'active',  -- active / disabled
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
```

### refresh_tokens（刷新令牌）

```sql
CREATE TABLE refresh_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    token_hash   TEXT NOT NULL UNIQUE,        -- bcrypt hash of refresh token
    expires_at   REAL NOT NULL,
    revoked      BOOLEAN NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
```

### user_model_configs（用户模型配置）

```sql
CREATE TABLE user_model_configs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    config_type  TEXT NOT NULL,                -- 'llm' / 'vlm' / 'tts_302' / 'tts_minimax' / 'tts_bytedance' / 'pexels'
    provider     TEXT NOT NULL DEFAULT '',     -- 'openai' / 'anthropic' / 'qwen' / ...
    model        TEXT NOT NULL DEFAULT '',
    base_url     TEXT NOT NULL DEFAULT '',
    api_key      TEXT NOT NULL DEFAULT '',     -- 加密存储
    extra_json   TEXT NOT NULL DEFAULT '{}',   -- 扩展配置 (temperature, timeout 等)
    is_default   BOOLEAN NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    UNIQUE(user_id, config_type, provider, model)
);

CREATE INDEX idx_user_model_configs_user ON user_model_configs(user_id);
```

### user_preferences（用户偏好）

```sql
CREATE TABLE user_preferences (
    user_id              TEXT PRIMARY KEY REFERENCES users(user_id),
    default_lang         TEXT NOT NULL DEFAULT 'zh',
    default_aspect_ratio TEXT NOT NULL DEFAULT '16:9',
    subtitle_style       TEXT NOT NULL DEFAULT 'classic_white',
    audio_prefs_json     TEXT NOT NULL DEFAULT '{}',  -- bgm_volume, tts_volume 等
    learned_prefs_json   TEXT NOT NULL DEFAULT '{}',  -- 自动学习的偏好
    updated_at           REAL NOT NULL
);
```

### skill_registry（Skill 注册表——管理员维护）

> **设计原则**：Skill 的文件内容（SKILL.md、handler.py、脚本、资源等）维护在**本地文件系统**中（`skills/` 目录），DB 只存元数据索引。这是因为 Skill 可能是一个完整仓库（含子目录、依赖、二进制资源），不适合存入 DB。

```sql
CREATE TABLE skill_registry (
    skill_id     TEXT PRIMARY KEY,             -- "video-color-grading"
    name         TEXT NOT NULL,                -- "视频调色"
    description  TEXT NOT NULL DEFAULT '',
    version      TEXT NOT NULL DEFAULT '1.0.0',
    author       TEXT NOT NULL DEFAULT 'system',
    category     TEXT NOT NULL DEFAULT 'general', -- video / audio / text / effect / utility
    tags         TEXT NOT NULL DEFAULT '[]',    -- JSON array
    local_path   TEXT NOT NULL,                -- 本地文件系统路径 (e.g. "skills/installed/video-color-grading")
    source       TEXT NOT NULL DEFAULT 'builtin', -- builtin / installed / custom
    status       TEXT NOT NULL DEFAULT 'active',  -- active / disabled
    installed_at REAL NOT NULL,
    installed_by TEXT REFERENCES users(user_id), -- 操作人 (管理员)
    created_at   REAL NOT NULL
);

CREATE INDEX idx_skill_reg_category ON skill_registry(category);
CREATE INDEX idx_skill_reg_status ON skill_registry(status);
```

> **注意**：暂不设计 `user_installed_skills` 表（用户级 Skill 安装）。当前阶段 Skill 全局共享，由管理员统一管理。用户级 Skill 作为后续迭代考虑。

## 恢复逻辑

从任意节点恢复的查询流程：

```sql
-- 1. 获取最新 pipeline 版本
SELECT version, dag_json FROM pipeline_states
WHERE session_id = ? ORDER BY version DESC LIMIT 1;

-- 2. 获取所有 skill 的最新执行状态
SELECT skill_id, status, outputs_ref, input_hash
FROM skill_executions
WHERE session_id = ? AND pipeline_version = ?
ORDER BY skill_id;

-- 3. 找到可恢复点：最后一个 completed skill 之后的所有 skill
-- 4. 对每个 completed skill，检查 input_hash 是否变化
-- 5. input_hash 变化 → 标记 dirty → 重新执行
-- 6. input_hash 不变 → 跳过，使用 outputs_ref 加载缓存结果
```

## 成本统计查询

```sql
-- 按 session 统计 API 成本
SELECT
    session_id,
    provider,
    model,
    SUM(prompt_tokens) as total_prompt_tokens,
    SUM(completion_tokens) as total_completion_tokens,
    SUM(latency_ms) as total_latency_ms,
    COUNT(*) as call_count
FROM llm_interactions
WHERE session_id = ?
GROUP BY provider, model;
```

## 日志查看查询

```sql
-- 查看某个 skill 执行的所有 LLM 交互
SELECT
    system_prompt, user_prompt, response_text,
    prompt_tokens, completion_tokens, latency_ms, created_at
FROM llm_interactions
WHERE execution_id = ?
ORDER BY created_at;
```
