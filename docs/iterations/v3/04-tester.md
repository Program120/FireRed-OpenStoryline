# V3 测试方案

---

## 一、测试分层（沿用 V2 + 新增维度）

| 层级 | 范围 | 工具 | V3 新增 |
|------|------|------|---------|
| 单元测试 | 模块内部逻辑 | pytest + mock | Provider / Router / Template 序列化 |
| 集成测试 | 跨模块交互 | pytest + 真实 API | 多 Provider 端到端调用链 |
| 兼容性测试 | NLE 导出文件 | FCP / DaVinci 打开验证 | 新增 |
| 并发/压力测试 | 批量处理 | locust + pytest | 新增 |
| 安全测试 | 权限 / 审计 | pytest | 新增 |
| 视觉回归 | 渲染输出 | SSIM/PSNR | 沿用 |
| 性能基准 | 延迟/吞吐 | pytest-benchmark | 长视频场景扩展 |
| 端到端 | 全流程 | 自动化脚本 | 批量 + 团队场景 |

---

## 二、P0-1 多模型 Provider 测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-PROV-01 | 注册 OpenAI + Anthropic + Gemini 三个 provider | 全部健康检查通过 |
| TC-PROV-02 | node_routing 配置 UnderstandClips→Gemini | 该 node 实际调用 Gemini API |
| TC-PROV-03 | 主 provider 返回 429 rate limit | 自动 fallback 到备用 provider，延迟 < 3s |
| TC-PROV-04 | 主 provider 超时 (>30s) | circuit breaker 触发，后续请求直接走 fallback |
| TC-PROV-05 | fallback 也失败 | 明确错误信息，不静默吞掉 |
| TC-PROV-06 | 未配 provider 的 node | 走 default provider |
| TC-PROV-07 | Anthropic tool calling 格式 | 工具调用参数和返回正确解析 |
| TC-PROV-08 | Gemini vision 输入 | 图片/视频帧正确编码传入 |
| TC-PROV-09 | token 用量记录 | session 结束后 token_usage 表数据完整，费用计算准确 |
| TC-PROV-10 | config 热更新 provider | 不重启服务即生效 |

---

## 三、P0-2 批量处理测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-BATCH-01 | 从已完成 session 生成 EditTemplate | node_chain 完整，参数快照正确 |
| TC-BATCH-02 | 模板序列化 → 反序列化 round-trip | 完全一致 |
| TC-BATCH-03 | 5 条视频批量套用模板 | 全部成功，各自独立 session |
| TC-BATCH-04 | 20 条视频批量，并发=3 | 同时运行不超过 3 条，总耗时 < 单条 × 20 / 3 × 1.3 |
| TC-BATCH-05 | 批量中第 7 条失败 | 其余继续执行，第 7 条标记 failed + 错误日志 |
| TC-BATCH-06 | 失败任务单条重试 | 重试成功后状态更新 |
| TC-BATCH-07 | 单条覆盖模板参数 | 该条使用覆盖值，其余用模板默认值 |
| TC-BATCH-08 | 批量进度 WebSocket 推送 | 每条完成时推送进度百分比和状态 |
| TC-BATCH-09 | 批量执行中取消 | 未开始的任务取消，进行中的任务完成后不继续 |
| TC-BATCH-10 | 并发=3 时内存占用 | 峰值 < 4GB（3 个 session 同时渲染） |

---

## 四、P1-1 NLE 导出测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-NLE-01 | 10 segments + 转场 + 字幕 → FCPXML | FCP 10.7 可直接打开，时间线结构正确 |
| TC-NLE-02 | 同上 → EDL | DaVinci Resolve 18 可导入，片段时间点偏差 < 1 帧 |
| TC-NLE-03 | xfade 转场映射 | 8 种转场在 FCP 中有对应效果（部分降级为 Cross Dissolve） |
| TC-NLE-04 | 不可映射的 FFmpeg filter | FCPXML 中有 `<!-- unsupported -->` 注释 |
| TC-NLE-05 | 素材路径模式=relative | 所有 asset 引用为相对路径 |
| TC-NLE-06 | 素材路径模式=packaged | 输出 zip 包含工程文件 + 所有素材 |
| TC-NLE-07 | 时间码精度 | 30fps 视频中 rational time 计算正确 |
| TC-NLE-08 | 空字幕轨 | 不生成字幕 lane，不报错 |
| TC-NLE-09 | 多音轨（voiceover + BGM） | FCP/DaVinci 中两个独立音轨 |

---

## 五、P1-2 配音情感测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-EMO-01 | emotion=excited + speed=1.2 | TTS 输出音频有感知差异（人工盲测） |
| TC-EMO-02 | 段落级情感：segment 1=excited, segment 2=calm | 两段 TTS 情感不同 |
| TC-EMO-03 | 各 TTS provider 情感映射 | 302 / bytedance / minimax 各自参数正确传入 |
| TC-EMO-04 | 不支持情感的 TTS provider | 优雅降级为 neutral，日志 warning |
| TC-EMO-05 | speed 边界值 (0.69 / 0.7 / 1.5 / 1.51) | 合法值通过，非法值 ValueError |

---

## 六、团队协作测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-TEAM-01 | Viewer 尝试调用 RenderVideoNode | 403 权限拒绝 |
| TC-TEAM-02 | Editor 提交发布请求 | publish_requests 表有记录，状态 pending |
| TC-TEAM-03 | Owner 审批通过 | 状态更新为 approved |
| TC-TEAM-04 | 所有 node 调用产生审计日志 | audit_log 完整，包含 user_id / action / detail |
| TC-TEAM-05 | 非团队成员访问团队 session | 403 |
| TC-TEAM-06 | Owner 修改成员角色 | 即时生效 |

---

## 七、长视频性能测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-PERF-01 | 30min 视频 CLIP 嵌入 | < 3min（V2 基线 > 10min） |
| TC-PERF-02 | 30min 视频端到端渲染 | < 15min（V2 基线 > 40min） |
| TC-PERF-03 | 60min 视频渲染内存峰值 | < 2GB |
| TC-PERF-04 | 分段渲染 + concat 输出质量 | 与全量渲染 SSIM > 0.99 |
| TC-PERF-05 | 20 条批量 + 3 并发内存峰值 | < 4GB |

---

## 八、对研发方案的风险补充

### 1. Provider 抽象的"最小公约"陷阱
各厂商能力差异大：Anthropic 不支持 embedding，Gemini 的 tool calling 格式特殊，Ollama 不支持 vision。**建议：** Provider 声明 capability flags（`supports_vision`, `supports_embedding`, `supports_tools`），Router 分配时校验 node 需求与 provider 能力是否匹配，不匹配时报错而非静默降级。

### 2. 批量模板的版本漂移
模板创建时 node 参数结构是 V3.0 的，V3.1 改了 schema → 旧模板反序列化失败。**建议：** 模板携带 `schema_version`，加载时做 migration 或明确报错。

### 3. FCPXML 跨版本兼容
FCPXML 1.8 / 1.9 / 1.10 / 1.11 之间有 breaking change。**建议：** CI 中用 `fcpxml-validator`（Apple 提供）校验生成文件。至少支持 1.9（FCP 10.4+）和 1.11（FCP 10.7+）两个版本。

### 4. TTS 情感效果的主观性
"excited"在不同声音/不同厂商下效果差异极大，自动化测试无法验证"听感"。**建议：** 建立 golden audio 库 + 人工 A/B 盲测机制。每个 provider × 每种情感至少 3 段参考音频。

### 5. 团队协作的 SQLite 天花板
SQLite 不支持真正的并发写入。3-5 个并发用户没问题，但 MCN 15 人同时操作可能出现 `database is locked`。**建议：** V3 用 WAL + busy_timeout 兜底，监控锁等待时间。如果 P95 > 500ms，V3.1 迁移到 PostgreSQL（提前在 ORM 层用 SQLAlchemy 做好抽象）。

### 6. 批量处理 + 多 Provider 的 rate limit 叠加
20 条视频批量 × 每条 8 个 node 调用 = 160 次 API 请求。如果 3 并发 → 峰值 ~24 QPS。多数 API 限制 10-60 RPM。**建议：** Router 内置 per-provider TokenBucketRateLimiter，批量场景自动降并发。

---

## 九、测试执行优先级

| 周 | 内容 |
|----|------|
| W1-W2 | TC-PROV 全部（Provider 是后续所有功能的基座） |
| W3-W4 | TC-BATCH（模板序列化 + 并发 + 失败恢复） |
| W5 | TC-NLE（FCPXML 需要人工在 FCP 中验证） |
| W6 | TC-EMO + golden audio 建库 |
| W7 | TC-TEAM（权限 + 审计覆盖率 100%） |
| W8 | TC-PERF（长视频 + 批量压力） |
| W9 | 全链路端到端：批量 20 条 × 3 provider × NLE 导出 × 团队审批 |
| W10 | 回归 V1/V2 功能不降级 + MCN 用户验收 |
