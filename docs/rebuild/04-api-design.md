# API 设计

---

## REST API

### Session 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建新会话 |
| GET | `/api/sessions` | 会话列表（分页） |
| GET | `/api/sessions/{id}` | 会话详情 + 当前 pipeline 状态 |
| DELETE | `/api/sessions/{id}` | 归档/删除会话 |
| POST | `/api/sessions/{id}/clear` | 清除聊天记录（保留媒体） |
| POST | `/api/sessions/{id}/cancel` | 取消当前执行 |

### 媒体管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions/{id}/media` | 上传媒体文件 |
| GET | `/api/sessions/{id}/media` | 列出会话媒体 |
| DELETE | `/api/sessions/{id}/media/{mid}` | 删除媒体 |
| GET | `/api/sessions/{id}/media/{mid}/file` | 下载原始文件 |
| GET | `/api/sessions/{id}/media/{mid}/thumb` | 缩略图 |

### Pipeline 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions/{id}/pipeline` | 完整 pipeline 状态 |
| POST | `/api/sessions/{id}/pipeline/resume` | 从断点恢复执行 |
| GET | `/api/sessions/{id}/pipeline/executions/{eid}` | 执行详情 + LLM 日志 |
| GET | `/api/sessions/{id}/pipeline/executions/{eid}/logs` | LLM 交互详细日志 |

### Skill 信息

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 可用 Skill 列表 |
| GET | `/api/skills/{skill_id}` | Skill 详情（参数 schema、描述） |

### 模型配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | 已配置的模型 Provider 列表 |
| POST | `/api/models/validate` | 验证模型 API Key |
| GET | `/api/meta/tts` | TTS Provider UI schema |

---

## WebSocket 协议

### 连接

```
ws://host:port/ws/sessions/{session_id}/chat
```

### 客户端 → 服务端

```jsonc
// 发送消息
{ "type": "chat.send", "data": {
    "text": "帮我把脏话剪掉",
    "attachment_ids": ["media_0001"],
    "llm_model": "qwen3.5-plus",     // 可选
    "vlm_model": "qwen3.5-plus"      // 可选
}}

// 清除聊天
{ "type": "chat.clear" }

// 设置语言
{ "type": "session.set_lang", "data": { "lang": "en" } }

// 心跳
{ "type": "ping" }
```

### 服务端 → 客户端

```jsonc
// 会话快照（连接后立即发送，刷新恢复）
{ "type": "session.snapshot", "data": {
    "session": { "session_id": "...", "status": "active", ... },
    "pipeline": { "skills": [{ "skill_id": "load_media", "status": "completed" }, ...] },
    "history": [{ "role": "user", "content": "..." }, ...],
    "media": [{ "media_id": "...", "filename": "...", "thumb_url": "..." }, ...]
}}

// LLM 流式 token
{ "type": "chat.token", "data": { "text": "好的，" } }

// 完整消息
{ "type": "chat.message", "data": { "role": "assistant", "content": "..." } }

// Skill 开始执行
{ "type": "skill.start", "data": {
    "skill_id": "understand_clips",
    "execution_id": "exec_xxx",
    "display_name": "画面理解"
}}

// Skill 进度
{ "type": "skill.progress", "data": {
    "skill_id": "understand_clips",
    "execution_id": "exec_xxx",
    "pct": 0.45,
    "message": "已理解 9/20 个片段"
}}

// 子任务完成（并行 Skill）
{ "type": "skill.subtask_complete", "data": {
    "skill_id": "understand_clips",
    "batch_index": 2,
    "batch_total": 4,
    "message": "批次 2/4 完成"
}}

// Skill 日志（LLM 交互详情）
{ "type": "skill.log", "data": {
    "skill_id": "speech_rough_cut",
    "execution_id": "exec_xxx",
    "level": "info",
    "message": "📝 全部 59 句 ASR 识别结果",
    "detail": "[0] \"你是否在雪山上救过一只狐狸，\"\n[1] ..."
}}

// Skill 完成
{ "type": "skill.complete", "data": {
    "skill_id": "understand_clips",
    "execution_id": "exec_xxx",
    "summary": "成功理解 20 个片段"
}}

// Skill 失败
{ "type": "skill.error", "data": {
    "skill_id": "understand_clips",
    "execution_id": "exec_xxx",
    "error": "VLM API 超时"
}}

// Pipeline 整体状态更新
{ "type": "pipeline.state", "data": {
    "skills": [
        { "skill_id": "load_media", "status": "completed" },
        { "skill_id": "split_shots", "status": "completed" },
        { "skill_id": "understand_clips", "status": "running", "pct": 0.45 },
        { "skill_id": "filter_clips", "status": "pending" },
        ...
    ]
}}

// 心跳
{ "type": "pong" }

// 错误
{ "type": "error", "data": { "message": "..." } }
```

### 关键设计点

1. **`session.snapshot`** 在 WebSocket 连接后立即发送 — 浏览器刷新后前端据此恢复完整 UI 状态
2. **`skill.log`** 是独立的事件类型 — 不再 hack 在 progress 的 message 字段里
3. **`skill.subtask_complete`** 让前端能精确展示并行 Skill 的子任务进度
4. **`pipeline.state`** 在每个 Skill 状态变化时推送 — 前端据此更新 DAG 可视化
