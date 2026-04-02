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

### OPT-004: understand_clips 逐 clip 串行调用 VLM（未实施）

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
