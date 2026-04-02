# V1 测试方案

---

## 一、测试分层策略

| 层级 | 范围 | 工具 |
|------|------|------|
| 单元测试 | 数据结构、DAG 算法、filter_complex 拼接 | pytest + pytest-asyncio |
| 集成测试 | Planner-Worker 流程、Session 持久化回环 | pytest + mock MCP |
| 端到端测试 | 完整 WebSocket 会话流程 | playwright + WS client |
| 性能测试 | DAG 执行时间、渲染性能、并发 | locust + benchmark |
| 回归测试 | V0 功能不退化 | V0/V1 路径对比 |

---

## 二、P0-1 增量编辑 测试用例

| ID | 用例名 | 操作 | 预期 |
|----|--------|------|------|
| TC-P01-01 | DAG 拓扑排序 | 调用 topological_sort() | load_media 在前，render_video 在后 |
| TC-P01-02 | dirty 传播范围 | group_clips 标记 dirty | 下游 5 节点变 DIRTY，上游和无关节点不变 |
| TC-P01-03 | 增量计划最小性 | 仅 select_bgm dirty | 计划只含 select_bgm → plan_timeline → render_video |
| TC-P01-04 | 并行层级 | split_shots dirty | asr 和 understand_clips 在同一层 |
| TC-P01-05 | 版本自增幂等 | 连续两次 mark_dirty 同一节点 | 已 dirty 的节点不重复标记 |
| TC-P01-06 | input_hash 防重跑 | 相同参数再次触发 | 检测 hash 未变，跳过执行 |
| TC-P01-07 | Planner: 换 BGM | "换一首更欢快的背景音乐" | 仅 select_bgm dirty |
| TC-P01-08 | Planner: 改文案 | "把第二段文案改成..." | 仅 generate_script dirty |
| TC-P01-09 | DAG 环检测 | 构建含环 DAG | 抛出异常 |
| TC-P01-10 | Worker 失败一致性 | plan_timeline 执行失败 | 上游 COMPLETED，失败节点 FAILED + error_msg，下游保持 DIRTY |

---

## 三、P0-2 风格模板 + 持久化 测试用例

| ID | 用例名 | 操作 | 预期 |
|----|--------|------|------|
| TC-P02-01 | UserProfile CRUD | 创建/添加/修改/删除模板 | 数据一致，template_id 唯一 |
| TC-P02-02 | 模板应用到渲染 | 指定 template_id 渲染 | 字幕样式与模板定义一致 |
| TC-P02-03 | Session 正常恢复 | 3 轮对话 → 关闭 → 重连 | history 完整，ProjectState 正确 |
| TC-P02-04 | Session 过期清理 | 4 天前的 session + cleanup | session 和对应文件均被删除 |
| TC-P02-05 | 并发写入安全 | 同 session 两个协程同时 save | 不报错，后写入的版本生效 |
| TC-P02-06 | Message 序列化回环 | System/Human/AI/ToolMessage 序列化→反序列化 | 类型正确，content 一致，tool_call_id 保留 |
| TC-P02-07 | 偏好学习收敛 | 5 次渲染均用 bgm_volume=0.3 | learned_preferences 中值接近 0.3 |

---

## 四、P1-1 预览 测试用例

| ID | 用例名 | 操作 | 预期 |
|----|--------|------|------|
| TC-P11-01 | Timeline 概览完整性 | 构建 overview | segment_count 正确，每段 duration > 0 |
| TC-P11-02 | 480p 预览速度 | preview_mode=True 渲染 30s 视频 | 尺寸 ≤480p，耗时 < 正式渲染 50% |
| TC-P11-03 | 分段指令: 交换 | "交换第3段和第4段" | 3、4 段互换，仅 plan_timeline + render 重跑 |
| TC-P11-04 | 分段指令: 删除 | "删除第2段" | segment_count -1，时长减少 |
| TC-P11-05 | 越界指令 | "交换第0段和第99段" | 友好错误提示，不触发执行 |

---

## 五、P1-2 渲染质量 测试用例

| ID | 用例名 | 操作 | 预期 |
|----|--------|------|------|
| TC-P12-01 | FFmpeg 输出正确性 | 混合比例视频 + BGM + 字幕渲染 | 可播放，画布尺寸正确 |
| TC-P12-02 | 音频闪避效果 | 10s BGM + 5s voiceover | 配音期间 BGM RMS 明显降低 |
| TC-P12-03 | FFmpeg fallback | 模拟 filter_complex 失败 | 自动降级 MoviePy，日志记录原因 |
| TC-P12-04 | 字幕模板一致性 | 8 个模板分别渲染 | 每个输出字幕样式与定义一致 |
| TC-P12-05 | 长视频内存控制 | 10 分钟视频 FFmpeg 渲染 | 内存峰值 < 2GB |
| TC-P12-06 | CRF 参数生效 | CRF=18 vs CRF=28 | 文件大小差异明显，均可播放 |

---

## 六、性能基准

| 指标 | V0 基线 | V1 目标 |
|------|---------|---------|
| 全量 Pipeline (30s 视频) | 3-5 min | < 3 min |
| 增量编辑 (换 BGM) | 3-5 min (全量) | < 40s |
| 增量编辑 (改文案) | 3-5 min | < 90s |
| 480p 预览 | N/A | < 15s |
| Session 恢复 | 无法恢复 | < 2s |
| 内存 (10min 视频) | > 4GB | < 2GB |

---

## 七、对研发方案的风险补充

1. **Planner LLM Prompt 工程复杂度被低估** — 建议先用规则引擎做 baseline，再叠加 LLM；维护 ≥50 条意图识别评测集
2. **LangGraph 与现有中间件不兼容** — `middleware=[log_tool_request, handle_tool_errors]` 和 `on_progress` 需重写为 StateGraph 节点/回调，研发方案应明确改造量
3. **lc_messages 序列化方案未明确** — ToolMessage 含非标准字段，建议用 `messages_to_dict` + schema version
4. **MoviePy → FFmpeg 迁移 15 人天偏紧** — 建议分步：无字幕版(5天) → 字幕烧录(4天) → 音频闪避(3天) → buffer(3天)
5. **缺少前端改造计划** — Timeline 概览、预览播放、模板选择器均需前端配合
6. **Planner 准确率需 ≥90%** — 否则增量编辑体验比全量更差
7. **FFmpeg 版本兼容性** — 需测试 5.x/6.x/7.x 的 filter 差异
