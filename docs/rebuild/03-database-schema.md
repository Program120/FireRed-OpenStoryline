# 数据库模型设计

---

## 设计原则

1. **DB 是唯一的状态真相源** — 不再有内存 dict 或 JSON 文件作为主存储
2. **每个 Skill 执行全量记录** — 支持从任意节点恢复
3. **LLM 交互全量记录** — 支持日志查看、成本分析、replay
4. **子任务级记录** — 并发 Skill 的每个 batch 独立追踪

## ER 图

```
sessions ─────────┬──── chat_messages
                  ├──── media_files
                  └──── pipeline_states ──── skill_executions ──── llm_interactions
                                                    │
                                             subtask_executions (self-ref)
```

## 表结构

### sessions（会话）

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,          -- UUID
    display_name TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',  -- active / paused / completed / archived
    lang         TEXT NOT NULL DEFAULT 'zh',
    config_json  TEXT NOT NULL DEFAULT '{}', -- 模型配置快照 (LLM/VLM/TTS)
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
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
```

### llm_interactions（LLM 交互记录）

**每次 LLM 调用全量记录，支持日志查看和成本统计。**

```sql
CREATE TABLE llm_interactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id     TEXT NOT NULL REFERENCES skill_executions(execution_id),
    session_id       TEXT NOT NULL,
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
CREATE INDEX idx_llm_session ON llm_interactions(session_id);
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
