# 用户认证与数据隔离

---

## 需求

1. 用户注册/登录能力
2. 用户间数据完全隔离：会话、媒体、制品、聊天记录
3. 用户独立的模型配置（LLM/VLM/TTS API Key、偏好模型）
4. 用户独立的风格模板和偏好

## 认证方案

### 选型：JWT + 本地账号

不引入第三方 OAuth（降低部署复杂度），支持未来扩展 OAuth。

```
注册: POST /api/auth/register  { username, password }
登录: POST /api/auth/login     { username, password } → { access_token, refresh_token }
刷新: POST /api/auth/refresh   { refresh_token } → { access_token }
登出: POST /api/auth/logout
当前用户: GET /api/auth/me
```

JWT payload:
```json
{
  "sub": "user_id",
  "username": "ethan",
  "exp": 1775200000
}
```

### 密码存储

`bcrypt` hash，不存明文。

## 数据库新增表

### users

```sql
CREATE TABLE users (
    user_id      TEXT PRIMARY KEY,          -- UUID
    username     TEXT NOT NULL UNIQUE,
    email        TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_url   TEXT,
    status       TEXT NOT NULL DEFAULT 'active',  -- active / disabled
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
```

### user_model_configs（用户模型配置）

每个用户独立配置自己的 LLM/VLM/TTS API Key。

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

## 现有表加 user_id 外键

```sql
-- sessions 表新增
ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL REFERENCES users(user_id);
CREATE INDEX idx_sessions_user ON sessions(user_id);

-- media_files 已通过 session_id 间接关联 user_id，无需改

-- 查询时永远带 user_id 过滤
SELECT * FROM sessions WHERE user_id = ? AND session_id = ?;
```

## 隔离模型

```
User A ──┬── Session 1 ──── media, pipeline, chat
         ├── Session 2 ──── media, pipeline, chat
         └── Model Config ── LLM: qwen / VLM: gpt-4o / TTS: minimax

User B ──┬── Session 3 ──── media, pipeline, chat
         └── Model Config ── LLM: deepseek / VLM: gemini / TTS: bytedance
```

- User A 看不到 User B 的任何会话、媒体、配置
- API 层每个请求从 JWT 提取 `user_id`，所有 DB 查询加 `WHERE user_id = ?`

## API 层改造

### 认证中间件

```python
# api/deps.py
async def get_current_user(request: Request) -> User:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    user = await db.get(User, payload["sub"])
    if not user or user.status != "active":
        raise HTTPException(401)
    return user

# 所有路由注入
@router.get("/api/sessions")
async def list_sessions(user: User = Depends(get_current_user), db = Depends(get_db)):
    return await db.query(Session).filter(Session.user_id == user.user_id).all()
```

### WebSocket 认证

```python
# WebSocket 连接时通过 query param 传 token
# ws://host/ws/sessions/{id}/chat?token=xxx
async def websocket_endpoint(websocket: WebSocket, session_id: str, token: str):
    user = verify_jwt(token)
    session = await get_session(session_id)
    if session.user_id != user.user_id:
        await websocket.close(code=4003)
        return
    ...
```

### 模型配置 API

```
GET    /api/me/models                    列出当前用户的模型配置
POST   /api/me/models                    添加/更新模型配置
DELETE /api/me/models/{config_id}        删除模型配置
POST   /api/me/models/validate           验证 API Key 有效性

GET    /api/me/preferences               获取用户偏好
PUT    /api/me/preferences               更新用户偏好
```

## 前端改造

### 登录页 (`/login`)

```tsx
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/register" element={<RegisterPage />} />
  <Route path="/" element={<ProtectedRoute><HomePage /></ProtectedRoute>} />
  <Route path="/session/:id" element={<ProtectedRoute><SessionPage /></ProtectedRoute>} />
  <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
</Routes>
```

### ProtectedRoute

```tsx
function ProtectedRoute({ children }) {
  const token = useAuthStore(s => s.token);
  if (!token) return <Navigate to="/login" />;
  return children;
}
```

### 设置页 (`/settings`)

- 模型配置管理（添加/删除/测试 API Key）
- 字幕偏好
- 音频偏好
- 语言选择

## Skill 执行时的模型获取

```python
# orchestrator/executor.py
async def get_llm_for_skill(user_id: str, skill_id: str, db) -> LLMClient:
    # 1. 查用户的 model config
    config = await db.query(UserModelConfig).filter(
        UserModelConfig.user_id == user_id,
        UserModelConfig.config_type == "llm",
        UserModelConfig.is_default == True,
    ).first()

    # 2. 用用户自己的 API Key 创建 LLM client
    return LLMClient(
        provider=config.provider,
        model=config.model,
        base_url=config.base_url,
        api_key=decrypt(config.api_key),
    )
```

## 安全考虑

| 风险 | 措施 |
|------|------|
| API Key 泄露 | DB 中加密存储（AES-256），内存中解密使用 |
| JWT 被盗 | access_token 短有效期 (30min)，refresh_token 长有效期 (7天) |
| 越权访问 | 每个 API 查询强制加 `user_id` 过滤 |
| 密码暴力破解 | 登录限流 (5次/分钟)，bcrypt 慢哈希 |
| Session ID 猜测 | UUID v4 随机生成，不可预测 |

## 架构图更新

```
                    ┌──────────────┐
                    │   Frontend    │
                    │  /login       │
                    │  /session/{id}│
                    └──────┬───────┘
                           │ JWT Bearer Token
                    ┌──────┴───────┐
                    │   API Layer   │
                    │  auth middleware│
                    │  user_id 注入  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴────┐ ┌────┴─────┐ ┌───┴──────┐
        │ Sessions  │ │  Models  │ │  Media   │
        │ user_id=? │ │ user_id=?│ │ user_id=?│
        └──────────┘ └──────────┘ └──────────┘
```
