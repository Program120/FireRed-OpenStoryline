# Skill 仓库设计

---

## 概览

系统内置一个 **Skill 仓库（Skill Registry）**，管理员维护可用 Skill 列表，模型/用户从中按需获取。

```
┌─────────────────────────────────────────────┐
│              Skill Registry (DB)             │
│  管理员上传/审核/上下架                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │画面理解   │ │色彩校正   │ │AI封面生成 │    │
│  │v1.2 内置  │ │v1.0 社区  │ │v0.3 实验  │    │
│  └──────────┘ └──────────┘ └──────────┘    │
└──────────┬──────────────────────────────────┘
           │ 安装/卸载
┌──────────┴──────────────────────────────────┐
│           用户工作空间                        │
│  skills/builtin/    ← 内置，随项目分发         │
│  skills/installed/  ← 从仓库安装               │
│  skills/custom/     ← 用户本地导入或模型生成     │
└─────────────────────────────────────────────┘
```

## Skill 的四种获取方式

| 方式 | 触发者 | 说明 |
|------|--------|------|
| **内置** | 系统 | 项目自带 16 个核心 Skill，开箱即用 |
| **仓库安装** | 模型/用户 | Agent 在对话中判断需要某个能力 → 从仓库获取并安装 |
| **本地导入** | 用户 | 上传一个 SKILL.md（+可选 handler.py）到系统 |
| **模型生成** | Agent | LLM 根据用户需求动态创建 SKILL.md，保存到 custom/ |

## 数据库表

### skill_registry（仓库 Skill 列表——管理员维护）

```sql
CREATE TABLE skill_registry (
    skill_id     TEXT PRIMARY KEY,             -- "video-color-grading"
    name         TEXT NOT NULL,                -- "视频调色"
    description  TEXT NOT NULL,
    version      TEXT NOT NULL DEFAULT '1.0.0',
    author       TEXT NOT NULL DEFAULT 'system',
    category     TEXT NOT NULL DEFAULT 'general', -- video / audio / text / effect / utility
    tags         TEXT NOT NULL DEFAULT '[]',    -- JSON array: ["调色", "LUT", "滤镜"]
    
    -- Skill 内容
    skill_md     TEXT NOT NULL,                -- SKILL.md 完整内容
    handler_code TEXT,                         -- handler.py 代码（可选）
    
    -- 元数据
    pipeline_json TEXT NOT NULL DEFAULT '{}',  -- pipeline 配置 (depends_on, next_skills)
    concurrency_json TEXT NOT NULL DEFAULT '{}', -- 并发配置
    
    -- 状态
    status       TEXT NOT NULL DEFAULT 'published', -- draft / published / deprecated
    install_count INTEGER NOT NULL DEFAULT 0,
    
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    created_by   TEXT NOT NULL REFERENCES users(user_id)  -- 上传者（管理员）
);

CREATE INDEX idx_skill_reg_category ON skill_registry(category);
CREATE INDEX idx_skill_reg_status ON skill_registry(status);
```

### user_installed_skills（用户已安装的 Skill）

```sql
CREATE TABLE user_installed_skills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL REFERENCES users(user_id),
    skill_id     TEXT NOT NULL,
    source       TEXT NOT NULL,                -- 'builtin' / 'registry' / 'custom' / 'generated'
    enabled      BOOLEAN NOT NULL DEFAULT 1,
    skill_md     TEXT NOT NULL,                -- 安装时的 SKILL.md 副本
    handler_code TEXT,                         -- handler.py 副本
    installed_at REAL NOT NULL,
    UNIQUE(user_id, skill_id)
);

CREATE INDEX idx_user_skills ON user_installed_skills(user_id, enabled);
```

## API 设计

### 仓库管理（仅管理员）

```
POST   /api/admin/skill-registry              # 上传新 Skill 到仓库
PUT    /api/admin/skill-registry/{id}          # 更新 Skill（新版本）
DELETE /api/admin/skill-registry/{id}          # 下架 Skill
PATCH  /api/admin/skill-registry/{id}/status   # 修改状态 (draft/published/deprecated)
GET    /api/admin/skill-registry               # 列出所有 Skill（含 draft）
```

### 仓库浏览（所有用户）

```
GET    /api/skill-registry                     # 浏览已发布的 Skill（分页+搜索+分类筛选）
GET    /api/skill-registry/{id}                # Skill 详情
GET    /api/skill-registry/search?q=调色&category=video  # 搜索
```

### 用户 Skill 管理

```
GET    /api/me/skills                          # 我已安装的 Skill 列表
POST   /api/me/skills/install                  # 从仓库安装 { skill_id: "video-color-grading" }
POST   /api/me/skills/import                   # 本地导入 { skill_md: "...", handler_code: "..." }
DELETE /api/me/skills/{skill_id}               # 卸载（仅 installed/custom）
PATCH  /api/me/skills/{skill_id}/toggle        # 启用/禁用
```

## Agent 自动安装流程

当 Agent 在对话中发现需要某个能力但用户未安装时：

```
用户: "帮我给视频加一个电影感的调色"

Agent 思考:
  1. 查看用户已安装 Skill → 没有调色相关的
  2. 搜索仓库: GET /api/skill-registry/search?q=调色
  3. 找到 "video-color-grading" Skill
  4. 询问用户: "我发现仓库中有「视频调色」Skill，需要安装后才能使用。是否安装？"
  5. 用户确认 → POST /api/me/skills/install { skill_id: "video-color-grading" }
  6. 安装完成 → Skill 自动加入 Pipeline DAG
  7. 执行调色
```

## 权限模型

```
┌─────────────┬──────────┬──────────┬──────────┐
│   操作       │  管理员   │ 普通用户  │  Agent   │
├─────────────┼──────────┼──────────┼──────────┤
│ 上传到仓库   │   ✓      │   ✗      │   ✗     │
│ 修改仓库Skill│   ✓      │   ✗      │   ✗     │
│ 下架Skill   │   ✓      │   ✗      │   ✗     │
│ 浏览仓库    │   ✓      │   ✓      │   ✓     │
│ 从仓库安装   │   ✓      │   ✓      │   ✓(需确认)│
│ 本地导入    │   ✓      │   ✓      │   ✗     │
│ 模型生成    │   ✓      │   ✓      │   ✓     │
│ 卸载自己的   │   ✓      │   ✓      │   ✗     │
│ 启用/禁用   │   ✓      │   ✓      │   ✗     │
└─────────────┴──────────┴──────────┴──────────┘
```

## users 表补充 role 字段

```sql
ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';
-- role: 'admin' / 'user'
```

## Skill 加载优先级

当同一个 `skill_id` 在多个来源中存在时：

```
1. custom/     (用户自定义优先——可能是针对特定需求的定制版本)
2. installed/  (仓库安装版本)
3. builtin/    (内置版本兜底)
```

## 前端：Skill 市场页面

### `/skills` — Skill 市场

```
┌─────────────────────────────────────────────┐
│  Skill 市场                    [搜索...]     │
├─────────────────────────────────────────────┤
│  分类: [全部] [视频] [音频] [文字] [特效]     │
│                                             │
│  ┌─────────────┐  ┌─────────────┐          │
│  │ 📹 视频调色   │  │ 🎵 AI配乐    │          │
│  │ v1.0 · 23安装 │  │ v0.8 · 5安装  │          │
│  │ [已安装]     │  │ [安装]       │          │
│  └─────────────┘  └─────────────┘          │
│                                             │
│  ┌─────────────┐  ┌─────────────┐          │
│  │ 🖼 AI封面    │  │ ✂️ 智能裁切   │          │
│  │ v0.3 实验    │  │ v1.1 · 12安装 │          │
│  │ [安装]       │  │ [安装]       │          │
│  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────┘
```

### `/settings/skills` — 我的 Skill 管理

```
┌─────────────────────────────────────────────┐
│  我的 Skill                  [导入本地Skill]  │
├─────────────────────────────────────────────┤
│  内置 (16)                                   │
│  ✓ 画面理解  ✓ 镜头分割  ✓ 语音识别 ...      │
│                                             │
│  已安装 (2)                                  │
│  ✓ 视频调色 v1.0       [卸载]               │
│  ✗ AI封面 v0.3 (已禁用) [启用] [卸载]        │
│                                             │
│  自定义 (1)                                  │
│  ✓ 快节奏Vlog模板       [卸载]               │
└─────────────────────────────────────────────┘
```
