# 核心抽象设计

---

## 1. Skill 体系

### 什么是 Skill

Skill 是一个**可分发、可安装的能力单元**，格式为 `SKILL.md`（遵循 skillkit 规范）。每个视频处理节点对应一个 Skill。

Skill 的三种来源：

| 来源 | 说明 | 示例 |
|------|------|------|
| **内置 Skill** | 项目自带的 16 个核心能力 | `skills/builtin/understand-clips/SKILL.md` |
| **模型生成 Skill** | LLM 在对话中动态创建 | 用户说"帮我保存这个剪辑工作流为技能"→ 生成 SKILL.md |
| **仓库安装 Skill** | 从 Skill 仓库下载 | `npx clawhub install video-color-grading` |

### Skill 目录结构

```
skills/
├── builtin/                        # 内置 Skill（随项目分发）
│   ├── load-media/
│   │   ├── SKILL.md                # Skill 定义（描述、触发条件、执行逻辑）
│   │   └── handler.py              # Python 执行器（可选，复杂逻辑用）
│   ├── split-shots/
│   │   ├── SKILL.md
│   │   └── handler.py
│   ├── understand-clips/
│   │   ├── SKILL.md
│   │   └── handler.py              # 含 split/merge 并发支持
│   ├── local-asr/
│   │   ├── SKILL.md
│   │   └── handler.py
│   ├── speech-rough-cut/
│   │   ├── SKILL.md
│   │   └── handler.py
│   ├── filter-clips/
│   │   └── SKILL.md
│   ├── group-clips/
│   │   └── SKILL.md
│   ├── generate-script/
│   │   └── SKILL.md
│   ├── generate-voiceover/
│   │   ├── SKILL.md
│   │   └── handler.py              # 含并发支持
│   ├── select-bgm/
│   │   ├── SKILL.md
│   │   └── handler.py
│   ├── plan-timeline/
│   │   ├── SKILL.md
│   │   └── handler.py
│   ├── render-video/
│   │   ├── SKILL.md
│   │   └── handler.py
│   └── ...
│
├── installed/                      # 从仓库安装的 Skill
│   ├── video-color-grading/
│   │   └── SKILL.md
│   └── ai-thumbnail-generator/
│       └── SKILL.md
│
└── custom/                         # 用户/模型 动态创建的 Skill
    ├── cutskill_fast_vlog/
    │   └── SKILL.md
    └── cutskill_product_review/
        └── SKILL.md
```

### SKILL.md 格式（扩展 skillkit 规范）

```markdown
---
name: understand-clips
description: 分析视频片段并生成画面描述。需要 VLM 模型支持。
version: 1.0.0

# 管道元数据（新增，用于 DAG 编排）
pipeline:
  skill_id: understand_clips
  display_name: 画面理解
  depends_on: [load_media, split_shots]
  next_skills: [filter_clips]
  
# 并发配置（新增）
concurrency:
  supports_batching: true
  batch_key: clips              # 按哪个输入字段拆分
  default_batch_size: 5
  max_concurrency: 4

# 执行器（新增）
handler: handler.py             # 指向同目录下的 Python 执行器
---

# 画面理解 Skill

## 功能
对每个视频片段调用 VLM 生成描述文字，用于后续的筛选和分组。

## 输入
- `media`: 已加载的媒体列表
- `clips`: 分割后的片段列表

## 输出
- `clip_captions`: 每个片段的描述列表
- `overall`: 整体内容摘要

## 执行逻辑
1. 对每个 clip 按 sample_fps 抽帧
2. 将帧发送给 VLM 生成描述
3. 汇总为整体摘要
```

### handler.py 格式

```python
"""
understand-clips skill handler.

当 SKILL.md 中指定了 handler 时，SkillExecutor 会加载此文件。
无 handler 的 Skill 由 LLM Agent 直接解释 SKILL.md 执行。
"""

from open_storyline.core.skill import SkillHandler, SkillContext, SkillResult


class Handler(SkillHandler):
    """必须有一个名为 Handler 的类。"""

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        clips = inputs.get("clips", [])
        captions = []
        for clip in clips:
            response = await ctx.llm.complete_vision(
                system_prompt=self.get_prompt("system", ctx.lang),
                user_prompt=self.get_prompt("user", ctx.lang, clip=clip),
                media=[{"path": clip["path"]}],
            )
            captions.append({"clip_id": clip["clip_id"], "caption": response})
            await ctx.log("info", f"片段 {clip['clip_id']} 描述完成")
        return SkillResult(success=True, data={"clip_captions": captions})

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        """按 clips 拆分为子任务。"""
        clips = inputs.get("clips", [])
        media = inputs.get("media", {})
        batch_size = self.meta.concurrency.get("default_batch_size", 5)
        return [
            {"clips": clips[i:i+batch_size], "media": media}
            for i in range(0, len(clips), batch_size)
        ]

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        all_captions = []
        for r in results:
            all_captions.extend(r.data.get("clip_captions", []))
        all_captions.sort(key=lambda c: c["clip_id"])
        return SkillResult(success=True, data={"clip_captions": all_captions})
```

## 2. SkillHandler 基类

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class SkillContext:
    session_id: str
    user_id: str
    execution_id: str
    db: Any                    # AsyncSession
    llm: Any                   # LLMClient (用户自己的 key)
    artifact_store: Any
    lang: str = "zh"

    async def log(self, level: str, message: str, detail: str = ""):
        """记录执行日志，实时推送到前端 + 存入 DB。"""
        ...

    async def progress(self, pct: float, message: str = ""):
        """上报进度，实时推送到前端。"""
        ...


class SkillHandler(ABC):
    """Python 执行器基类。有 handler.py 的 Skill 继承此类。"""

    def __init__(self, skill_dir: str, meta: dict):
        self.skill_dir = skill_dir
        self.meta = meta

    @abstractmethod
    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        ...

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        """默认不拆分。"""
        return [inputs]

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        """默认合并：拼接所有 list 类型的值。"""
        merged = {}
        for r in results:
            for k, v in r.data.items():
                if k in merged and isinstance(v, list):
                    merged[k].extend(v)
                else:
                    merged[k] = v
        return SkillResult(success=all(r.success for r in results), data=merged)

    def get_prompt(self, role: str, lang: str, **kwargs) -> str:
        """从 prompts/ 目录加载并渲染模板。"""
        ...
```

## 3. SkillLoader（发现 + 加载）

```python
class SkillLoader:
    """
    从三个目录发现 Skill：
    1. skills/builtin/     — 内置
    2. skills/installed/   — 仓库安装
    3. skills/custom/      — 用户/模型创建
    """

    def __init__(self, base_dirs: list[str]):
        self.base_dirs = base_dirs
        self.skills: dict[str, LoadedSkill] = {}

    async def discover(self):
        """扫描所有目录，解析 SKILL.md，加载 handler.py（如有）。"""
        for base_dir in self.base_dirs:
            for skill_dir in Path(base_dir).iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                meta = self._parse_skill_md(skill_md)
                handler = self._load_handler(skill_dir, meta)

                self.skills[meta["pipeline"]["skill_id"]] = LoadedSkill(
                    meta=meta,
                    handler=handler,          # SkillHandler instance or None
                    skill_dir=str(skill_dir),
                    source="builtin" | "installed" | "custom",
                )

    def _parse_skill_md(self, path: Path) -> dict:
        """解析 SKILL.md 的 YAML frontmatter。"""
        ...

    def _load_handler(self, skill_dir: Path, meta: dict) -> Optional[SkillHandler]:
        """如果 meta 中指定了 handler，动态加载 Python 模块。"""
        handler_file = meta.get("handler")
        if not handler_file:
            return None
        # importlib 动态加载
        module = importlib.import_module_from_path(skill_dir / handler_file)
        return module.Handler(skill_dir=str(skill_dir), meta=meta)

    def get_skill(self, skill_id: str) -> Optional[LoadedSkill]:
        return self.skills.get(skill_id)

    def list_skills(self) -> list[dict]:
        return [{"skill_id": k, **v.meta} for k, v in self.skills.items()]
```

## 4. 两种 Skill 执行模式

### 模式 A：有 handler.py（Python 执行器）

核心处理节点（LoadMedia, SplitShots, ASR, RenderVideo 等）需要精确控制的 Python 逻辑。

```
SkillExecutor:
  1. 加载 handler.py → Handler 实例
  2. 如果 supports_batching：
     subtasks = handler.split_into_subtasks(inputs)
     results = asyncio.gather(*[handler.execute(ctx, sub) for sub in subtasks])
     merged = handler.merge_subtask_results(results)
  3. 否则：
     result = handler.execute(ctx, inputs)
  4. 记录到 DB
```

### 模式 B：无 handler.py（LLM 解释执行）

简单的 Skill（FilterClips, GroupClips 等纯 LLM 调用）或用户自定义 Skill，由 LLM Agent 读取 SKILL.md 内容后自行调用工具执行。

```
SkillExecutor:
  1. 读取 SKILL.md 全文
  2. 将其作为 System Prompt 注入 Agent
  3. Agent 根据 SKILL.md 的执行逻辑描述，调用可用的工具完成任务
  4. 收集 Agent 输出作为 SkillResult
```

这也是**模型动态生成 Skill** 的执行方式——LLM 写的 SKILL.md 描述了执行逻辑，另一个 LLM 读取并执行。

## 5. Skill 安装/管理 API

```
GET    /api/skills                     # 列出所有已安装 Skill
GET    /api/skills/{id}                # Skill 详情
POST   /api/skills/install             # 从仓库安装 { source: "clawhub", name: "video-color-grading" }
POST   /api/skills/create              # 手动创建 { name: "...", content: "SKILL.md 内容" }
DELETE /api/skills/{id}                # 卸载（仅 installed/custom）
POST   /api/skills/{id}/enable         # 启用
POST   /api/skills/{id}/disable        # 禁用
```

## 6. Pipeline（从 Skill 自动构建 DAG）

```python
class Pipeline:
    def __init__(self, skill_loader: SkillLoader):
        self.skills = skill_loader.skills
        self.dag = self._build_dag()

    def _build_dag(self) -> TaskDAG:
        """从每个 Skill 的 pipeline.depends_on 自动构建 DAG。"""
        nodes = list(self.skills.keys())
        edges = []
        for skill_id, loaded in self.skills.items():
            for dep in loaded.meta.get("pipeline", {}).get("depends_on", []):
                edges.append(DAGEdge(source=dep, target=skill_id))
        return TaskDAG(nodes=nodes, edges=edges)
```

新安装的 Skill 如果声明了 `depends_on` 和 `next_skills`，会自动融入 DAG。这就是 Skill 体系可扩展的关键。

## 7. 与当前架构的对比

| 维度 | 当前 (BaseNode) | 重建后 (Skill) |
|------|----------------|---------------|
| 格式 | Python 类 + 装饰器注册 | SKILL.md + 可选 handler.py |
| 分发 | 代码内嵌 | 独立目录，可安装/卸载 |
| 来源 | 仅内置 | 内置 + 仓库安装 + 模型生成 |
| 扩展 | 改代码 + 重启 | 放文件 + 热加载 |
| 并发 | 无 | SKILL.md 声明 concurrency 配置 |
| 执行 | MCP 跨进程 | 进程内直接调用（handler.py 或 LLM 解释） |
| DAG | 硬编码 require_prior_kind | SKILL.md 声明 depends_on，自动构建 |
