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

> **设计原则**：Skill 文件（SKILL.md、handler.py、脚本、资源、子目录等）维护在**本地文件系统**中，DB 只存元数据索引。Skill 可能是一个完整仓库（如 Anthropic 的 docx/pptx skill），不适合存入 DB。

```sql
CREATE TABLE skill_registry (
    skill_id     TEXT PRIMARY KEY,             -- "video-color-grading"
    name         TEXT NOT NULL,                -- "视频调色"
    description  TEXT NOT NULL DEFAULT '',
    version      TEXT NOT NULL DEFAULT '1.0.0',
    author       TEXT NOT NULL DEFAULT 'system',
    category     TEXT NOT NULL DEFAULT 'general', -- video / audio / text / effect / utility
    tags         TEXT NOT NULL DEFAULT '[]',    -- JSON array
    local_path   TEXT NOT NULL,                -- 本地路径 "skills/installed/video-color-grading"
    source       TEXT NOT NULL DEFAULT 'builtin', -- builtin / installed / custom
    status       TEXT NOT NULL DEFAULT 'active',  -- active / disabled
    installed_at REAL NOT NULL,
    installed_by TEXT REFERENCES users(user_id), -- 操作人（管理员）
    created_at   REAL NOT NULL
);

CREATE INDEX idx_skill_reg_category ON skill_registry(category);
CREATE INDEX idx_skill_reg_status ON skill_registry(status);
```

> **暂不设计用户级 Skill**：当前阶段 Skill 全局共享，由管理员统一管理安装/卸载。`user_installed_skills` 表作为后续迭代考虑。

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

> **后续迭代，当前不实现。** `/api/me/skills` 等用户级 Skill 管理 API 暂不开放，当前所有 Skill 由管理员统一管理。

## Agent 自动发现 Skill

Agent 在对话中发现需要某个能力时，先查已注册的 Skill：

```
用户: "帮我给视频加一个电影感的调色"

Agent 思考:
  1. 查看已启用 Skill → 找到 "video-color-grading"
  2. 调用该 Skill 执行调色
  
  或者：
  1. 查看已启用 Skill → 没有调色相关的
  2. 告知用户: "当前没有调色 Skill，请联系管理员安装"
```

## 权限模型

```
┌─────────────┬──────────┬──────────┬──────────┐
│   操作       │  管理员   │ 普通用户  │  Agent   │
├─────────────┼──────────┼──────────┼──────────┤
│ 安装 Skill  │   ✓      │   ✗      │   ✗     │
│ 卸载 Skill  │   ✓      │   ✗      │   ✗     │
│ 启用/禁用   │   ✓      │   ✗      │   ✗     │
│ 本地导入    │   ✓      │   ✗      │   ✗     │
│ 浏览 Skill  │   ✓      │   ✓      │   ✓     │
│ 使用 Skill  │   ✓      │   ✓      │   ✓     │
│ 模型生成    │   ✓      │   ✓      │   ✓     │
└─────────────┴──────────┴──────────┴──────────┘
```

## users 表 role 字段

> `role` 字段已直接包含在 `users` CREATE TABLE 定义中（见 `03-database-schema.md`），无需 ALTER TABLE。
>
> 值为 `'admin'` 或 `'user'`。

## Skill 加载优先级

当同一个 `skill_id` 在多个来源中存在时：

```
1. custom/     (用户自定义优先——可能是针对特定需求的定制版本)
2. installed/  (仓库安装版本)
3. builtin/    (内置版本兜底)
```

## 前端：管理员 Skill 管理页面

### `/admin/skills` — 管理员 Skill 管理（admin-only）

```
┌─────────────────────────────────────────────┐
│  Skill 管理 (管理员)          [导入本地Skill]  │
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
