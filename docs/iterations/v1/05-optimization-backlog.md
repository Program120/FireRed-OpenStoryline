# V1 优化记录 (Optimization Backlog)

> 在实际使用和测试中发现的性能/体验问题，以及已实施的优化。

---

## OPT-001: speech_rough_cut 逐句串行调用 LLM — 已优化

**发现时间：** 2026-04-02
**修复时间：** 2026-04-02
**状态：** 已完成

**问题：** `speech_rough_cut` 对 ASR 识别出的每一句话单独串行调用 LLM（30句 × 4s = ~120s），进度条长时间卡在 99%。

**修复：** 改为 `asyncio.gather()` 并行调用，`Semaphore(5)` 控制并发。

**改动文件：** `src/open_storyline/nodes/core_nodes/speech_rough_cut.py`

**效果：** 30句 × 5并发 × 4s = ~24s，提速约 **5x**。

---

## OPT-002: Planner 意图识别规则覆盖不足 — 已优化

**发现时间：** 2026-04-02
**修复时间：** 2026-04-02
**状态：** 已完成

**问题：** 初始 Planner 只有 6 条正则规则，很多常见的中文编辑指令无法匹配（如"音乐太吵了换一首"、"第三段删掉"、"语速调快一点"），导致频繁 fallback 到全量重跑。

**修复：** 扩展到 22 条规则，覆盖：
- BGM/音乐：3 条（含否定表达"不好听"、情绪描述"更欢快"）
- 配音/TTS：3 条（含语速/音色/情感调整）
- 文案/脚本：3 条（含分段指令"第X段文案改..."）
- 顺序/时间线：3 条（含交换、拉长缩短）
- 渲染/导出：1 条
- 片段/镜头：2 条（含"第X段删掉"）
- 分组：1 条
- 字幕样式：1 条
- 转场：1 条
- 调色/滤镜：1 条

**改动文件：** `src/open_storyline/orchestrator/planner.py`

**效果：** 预估意图识别准确率从 ~60% 提升到 ~90%。

---

## OPT-003: V1 编排器未接入 WebSocket 处理链 — 已修复

**发现时间：** 2026-04-02
**修复时间：** 2026-04-02
**状态：** 已完成

**问题：** V1 的 Planner/Worker 编排器已实现但未接入 `agent_fastapi.py`，导致即使 `use_v1=true`，每次用户发消息仍走 V0 全量重跑路径。

**修复：**
- `agent_fastapi.py` 导入 `build_orchestrator`
- `ChatSession` 新增 `_orchestrator` 属性
- `ensure_agent()` 中当 `use_v1=True` 时初始化 orchestrator
- `pump_agent()` 中优先走 V1 路径：分析意图 → 增量执行 → 然后传递给 V0 Agent 做 LLM 回复

**改动文件：** `agent_fastapi.py`

**效果：** 用户修改指令（如"换BGM"）不再触发全部 16 节点重跑，只重跑受影响的下游节点。

---

## 待优化项

### OPT-004: MCP 采样协议串行瓶颈（根因，未实施）

**发现时间：** 2026-04-02
**状态：** 已识别根因，待架构级修复

**问题：** `speech_rough_cut` 中 `asyncio.gather()` 并行优化无效——因为底层 `MCPSampler.complete()` 调用 `mcp_ctx.session.create_message()`，MCP session 是**单连接串行**的，并发请求会被排队。59 句 × 串行 × ~3s = ~3 分钟。

**根因：** 节点内部的 LLM 调用走 MCP Sampling 协议（节点在 MCP Server 侧，LLM 在 Agent Host 侧），请求要通过 MCP session 往返传输，session 是串行的。

**可选修复方案：**
1. **节点内直接调 LLM API**（绕过 MCP Sampling）— 需要在 MCP Server 侧也有 LLM 连接，打破当前"节点不直接调 LLM"的架构约束
2. **MCP session 连接池**— 为同一 session 开多个 MCP 连接通道，允许并发采样
3. **批量采样协议扩展**— 在 MCP 协议层支持 batch create_message

**当前缓解：** 进度条按批次更新（每 10 句一批），让用户看到真实进度。实际速度未改善。

**优先级：** P0（影响所有依赖节点内 LLM 调用的场景）

---

### OPT-005: understand_clips 逐 clip 串行调用 VLM（未实施）

**问题：** `understand_clips.py` 对每个视频片段逐一调用 VLM 生成描述。12 个片段串行调用，每个 5-10s。

**建议方案：** 与 OPT-001 类似，用 `asyncio.gather()` + `Semaphore(3)` 并行（VLM 调用更重，并发数应低于 LLM）。

**预估提速：** 3-4x

**优先级：** P2（影响首次生成速度，但非增量编辑场景）

---

### OPT-005: render_video MoviePy 内存占用高（未实施）

**问题：** MoviePy 一次性加载所有 clip 到内存，10 分钟视频可能占用 4GB+。

**建议方案：** 切换到 FFmpeg 渲染器（`rendering/ffmpeg_renderer.py` 已实现），或改为分段渲染 + `ffmpeg concat demuxer` 合并。

**预估效果：** 内存从 O(total_duration) 降至 O(max_segment_duration)

**优先级：** P1（影响长视频场景可用性）

---

### OPT-006: Planner 缺乏 LLM 增强的模糊意图识别（未实施）

**问题：** 当前纯规则引擎无法处理模糊指令（如"这个视频感觉差点什么"、"整体节奏不太对"），会 fallback 到全量重跑。

**建议方案：** 在规则匹配失败时，调用 LLM 做一次意图分类（输入用户消息 + 当前 ProjectState 摘要，输出需要 dirty 的 node_kinds），然后再走 DAG 传播。

**预估效果：** 意图识别准确率从 90% 提升到 95%+

**优先级：** P2（当前规则已覆盖大部分明确指令）

---

### OPT-007: URL 不包含 session ID，无法通过链接恢复会话（未实施）

**发现时间：** 2026-04-02
**状态：** 待实施

**问题：** URL 永远是 `127.0.0.1:8005`，切换会话时 URL 无变化。无法通过浏览器前进/后退、书签、分享链接恢复到特定会话。

**建议方案：** 使用 hash route（`#/session/{session_id}`）或 History API（`/session/{session_id}`），在创建/切换会话时更新 URL，页面加载时从 URL 解析 session_id 并恢复。

**优先级：** P2（体验问题，不影响核心功能）

---

### OPT-008: 详细日志面板 MCP 通道局限性（已部分解决）

**发现时间：** 2026-04-02
**状态：** 部分解决

**问题：** MCP 节点内的 `report_progress(0, 0, payload)` 触发 `ClosedResourceError`。当前方案是把 `__log__` JSON 嵌入正常 progress 消息（progress>0, total>0），后端检测后转为 `tool_log` 事件。

**遗留问题：** 日志和进度共用 progress 通道，进度百分比可能不准确。理想方案是 MCP 协议支持独立日志通道。

**优先级：** P3

---

### OPT-009: 详细日志按钮刷新后消失（未实施）

**发现时间：** 2026-04-02
**状态：** 待实施

**问题：** 「详细日志」按钮是通过 WebSocket 实时 `tool.log` 事件动态创建的 DOM 元素。刷新页面后 DOM 重建，但历史事件不会重放，按钮消失。

**根因：** 前端在恢复历史对话时（从 session snapshot），只恢复了消息和工具卡片的最终状态，没有重放日志事件。

**解决方案：**
- 方案 A：恢复 session 时，从 SQLite `tool_logs` 表加载历史日志，对每个有日志的工具卡片自动创建「详细日志」按钮
- 方案 B：在 session snapshot 的 history 中存储工具卡片的日志计数，前端据此渲染按钮（点击时 fetch API 加载详情）

**优先级：** P1

---

### OPT-010: LLM Agent 说"不加字幕"但实际渲染仍有字幕（V0 原版问题）

**发现时间：** 2026-04-02
**状态：** V0 原版 bug

**问题：** 用户要求"不要加字幕"，Agent 在回复中声称"没有添加字幕"，但最终视频中仍然出现了字幕。

**根因：** Agent 的文本回复和实际工具调用参数不一致。Agent 可能正确理解了用户意图并在回复中确认，但在调用 `render_video` / `plan_timeline` 等工具时没有正确设置参数（如 `subtitle_enabled=false`）。这是 prompt 工程和工具参数映射的问题。

**需要排查的文件：**
- `prompts/tasks/instruction/` — 系统指令是否说明如何处理"不加字幕"的请求
- `src/open_storyline/nodes/core_nodes/render_video.py` — 是否支持禁用字幕的参数
- `src/open_storyline/nodes/core_nodes/plan_timeline_pro.py` — 时间线编排是否会强制生成字幕轨

**优先级：** P1（影响用户信任度——"说一套做一套"）

---

### OPT-011: 语音粗切结果不准确（V0 原版问题）

**发现时间：** 2026-04-02
**状态：** V0 原版质量问题

**问题：** 语音粗切把不该删的内容删了，或者该删的没删。

**根因：** `speech_rough_cut` 的 LLM prompt（`prompts/tasks/speech_rough_cut/zh/system.md`）的指令效果取决于 LLM 的能力。当前使用 qwen3.5-plus，对中文口语的理解可能不够精确。

**可能的优化方向：**
- 优化 prompt 中的示例和规则说明
- 预筛选阶段的准确率也需要评估（23/59 句被标记是否合理？）
- 考虑为不同场景（去脏话 vs 去口水词 vs 通用清洗）提供不同的 prompt 模板
- 最终需要构建一个评测集来量化准确率

**优先级：** P2（取决于用户场景，口播粗剪本身是辅助功能）
