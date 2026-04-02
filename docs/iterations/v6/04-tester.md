## TESTER 视角 -- QA工程师

### 一、V6测试策略总览

V6从"工具"进化为"自主智能体平台"，测试范式必须根本性转变：**从"验证功能是否正确"转向"验证AI决策是否合理 + 系统是否可靠"。**

### 二、测试分层

#### Layer 1: 单元测试（Node级）

**现有节点的回归测试**（保持现有16个核心节点的测试覆盖）：

```python
# tests/nodes/test_stream_render.py

class TestStreamRenderNode:
    async def test_segment_splitting_respects_cut_points(self):
        """流式渲染的片段切分不能在镜头中间切"""
        timeline = make_timeline(clips=10, transitions=9)
        segments = StreamRenderNode.segment_splitter.split(timeline)
        for seg in segments:
            assert seg.start_time in timeline.cut_points
    
    async def test_stream_output_is_valid_fmp4(self):
        """每个流式片段都是有效的fMP4"""
        async for chunk in stream_render_node.execute_stream(timeline, state):
            assert is_valid_fmp4_segment(chunk)
    
    async def test_delta_render_only_rerenders_changed_segments(self):
        """增量渲染只处理变化的片段"""
        original_segments = await render_all(timeline_v1)
        timeline_v2 = timeline_v1.replace_bgm(segment_idx=3, new_bgm="xxx")
        changed = detect_changes(timeline_v1, timeline_v2)
        assert changed == {3}
```

#### Layer 2: Agent行为测试

**这是V6测试的核心难点。** AI Agent的决策不像确定性函数，需要用"行为边界"而非精确输出来验证。

```python
# tests/agents/test_orchestrator.py

class TestAgentOrchestrator:
    
    async def test_director_decomposes_brief_into_valid_plan(self):
        """Director能将Brief拆解为合理的任务计划"""
        brief = "做一条3分钟的新能源汽车对比视频"
        plan = await orchestrator.agents[AgentRole.DIRECTOR].plan(brief, style)
        
        # 验证计划结构完整性（不验证具体内容）
        assert plan.has_script_phase()
        assert plan.has_visual_phase()
        assert plan.has_audio_phase()
        assert plan.has_edit_phase()
        assert plan.estimated_duration_sec == pytest.approx(180, abs=30)
    
    async def test_conflict_resolution_director_decides(self):
        """多Agent冲突时Director能做出决定"""
        # 模拟Musician要安静+Editor要高能的冲突
        conflict = AgentConflict(
            segment_id="seg_5",
            proposals={
                AgentRole.MUSICIAN: {"bgm_energy": 0.2},
                AgentRole.EDITOR: {"bgm_energy": 0.8},
            }
        )
        resolution = await orchestrator.resolve_conflict(conflict)
        assert resolution.bgm_energy is not None  # Director必须做出决策
        assert resolution.reasoning != ""  # 必须给出理由

    async def test_style_profile_affects_output_measurably(self):
        """不同StyleProfile产出的视频在可测量维度上不同"""
        fast_style = StyleProfile(narrative_pace=0.9, bgm_genre_weights={"electronic": 0.8})
        slow_style = StyleProfile(narrative_pace=0.2, bgm_genre_weights={"classical": 0.8})
        
        video_fast = await orchestrator.execute_brief("科技新闻", fast_style)
        video_slow = await orchestrator.execute_brief("科技新闻", slow_style)
        
        # 快节奏视频的平均镜头时长应该更短
        assert avg_shot_duration(video_fast) < avg_shot_duration(video_slow)
        # BGM风格应该匹配
        assert classify_bgm_genre(video_fast.bgm) in ["electronic", "edm", "synth"]
```

#### Layer 3: 端到端流水线测试

```python
# tests/e2e/test_autonomous_pipeline.py

class TestAutonomousPipeline:
    
    @pytest.mark.slow
    async def test_brief_to_publishable_video(self):
        """从Brief到可发布视频的全链路"""
        result = await pipeline.run(
            brief="做一条1分钟的猫咪合集视频",
            style=DEFAULT_STYLE,
            target_platforms=["douyin"],
        )
        
        assert result.status == "ready_to_publish"
        assert result.video_path.exists()
        assert 50 <= result.duration_sec <= 70  # 1分钟±10秒
        assert result.thumbnail_path.exists()
        assert result.metadata.title != ""
        assert result.metadata.description != ""
        assert result.cost_rmb < 5.0  # 成本约束
    
    @pytest.mark.slow
    async def test_living_content_auto_update(self):
        """活内容在数据变化时自动更新"""
        # 初始发布
        v1 = await pipeline.run(brief="2026年手机销量TOP5", data_source=mock_api)
        
        # 模拟数据变化
        mock_api.update({"rank_1": "iPhone 18", "rank_2": "Huawei Mate 80"})
        
        # 触发更新检测
        v2 = await living_content.check_and_update(v1.content_id)
        
        assert v2.version == 2
        assert v2.changed_segments > 0
        assert v2.total_segments > v2.changed_segments  # 不是全部重渲
```

#### Layer 4: 质量评估测试（AI Judge）

传统断言无法评估AI内容质量。引入**AI评审员**：

```python
# tests/quality/test_content_quality.py

class TestContentQuality:
    """使用独立LLM作为评审员评估AI产出内容质量"""
    
    async def test_narrative_coherence(self):
        """叙事连贯性评估"""
        video = await pipeline.run(brief="新能源汽车发展史")
        script = video.script_text
        
        score = await ai_judge.evaluate(
            criteria="narrative_coherence",
            content=script,
            rubric="评分1-5。1=逻辑混乱，5=叙事流畅、过渡自然、结构完整",
        )
        assert score >= 3.5  # 最低可接受分数
    
    async def test_style_fidelity(self):
        """风格忠实度评估"""
        reference_videos = load_reference_videos("科技老王", count=5)
        new_video = await pipeline.run(
            brief="AI芯片最新进展", 
            style=StyleProfile.learn_from_history("科技老王")
        )
        
        similarity = await ai_judge.style_similarity(reference_videos, new_video)
        assert similarity >= 0.7  # 风格相似度阈值
```

#### Layer 5: 非功能性测试

```python
class TestPerformance:
    async def test_stream_render_first_frame_latency(self):
        """流式渲染首帧延迟 < 3秒"""
        start = time.monotonic()
        async for chunk in stream_render(timeline):
            first_frame_latency = time.monotonic() - start
            assert first_frame_latency < 3.0
            break
    
    async def test_multi_agent_total_latency(self):
        """多Agent全链路延迟（1分钟视频 < 10分钟）"""
        start = time.monotonic()
        await orchestrator.execute_brief("1分钟产品介绍", style)
        total = time.monotonic() - start
        assert total < 600

class TestResilience:
    async def test_agent_failure_graceful_degradation(self):
        """单个Agent失败时系统降级而非崩溃"""
        # 模拟Musician Agent LLM超时
        with mock_agent_failure(AgentRole.MUSICIAN):
            result = await orchestrator.execute_brief("科技新闻", style)
            assert result.status in ["completed_degraded", "ready_to_publish"]
            assert result.has_bgm  # 应该有兜底BGM（规则选择而非AI选择）
    
    async def test_event_bus_message_durability(self):
        """EventBus消息不丢失（即使消费者暂时离线）"""
        await event_bus.emit(ArtifactEvent(type="script_ready", ...))
        # 模拟消费者5秒后上线
        await asyncio.sleep(5)
        events = await consumer.drain()
        assert len(events) == 1
```

### 三、测试基础设施需求

1. **AI评估基准集（Benchmark Suite）**：
   - 50个标准Brief + 人工标注的预期质量分数
   - 10个账号的风格参考集（每账号5条代表作品）
   - 覆盖不同题材：科技、生活、教育、娱乐、新闻

2. **Mock层**：
   - `MockLLMClient`：录制真实LLM响应并回放，降低CI成本
   - `MockPlatformPublisher`：模拟平台API（包括限流/审核拒绝等异常场景）
   - `MockDataSource`：活内容系统的可控数据源

3. **CI/CD集成**：
   - 快速套件（<5分钟）：单元测试 + Agent行为测试（Mock LLM）
   - 完整套件（<30分钟）：端到端测试（真实LLM，每日一次）
   - 质量评估套件（<2小时）：AI Judge评估（每周/发版前）

4. **可观测性测试仪表盘**：
   - 每次发版后自动对比关键指标：首帧延迟、端到端耗时、LLM调用次数、成本、AI Judge平均分
   - 异常自动告警

### 四、V6特有测试风险

| 风险 | 测试策略 |
|------|----------|
| AI输出不确定性导致测试 flaky | 使用行为边界断言 + 统计显著性检验（跑N次取分布） |
| 多Agent交互路径爆炸 | 聚焦关键路径+混沌测试（随机注入Agent失败/延迟） |
| 活内容的时间依赖 | 时间Mock+事件驱动测试（不依赖真实时间） |
| 平台API合规性 | 沙箱环境+录制回放+人工抽检 |
| 风格评估主观性 | 多AI Judge交叉评估+人工校准基准 |

---

**总结**：V6的本质是将FireRed-OpenStoryline从一个"AI视频编辑工具"变成一个"自主内容创作智能体平台"。架构上，核心变化是从单Agent/单Session演进为多Agent协作/事件驱动/流式处理。现有的NodeManager DAG编排、BaseNode抽象、ArtifactStore、MCP Server等基础设施可以复用并逐步扩展，无需推倒重来。关键是新增 AgentOrchestrator、EventBus、SharedArtifactStore、StreamRenderNode 四个核心组件，以及 StyleProfile 和 PlatformPublisher 两个领域模型。
