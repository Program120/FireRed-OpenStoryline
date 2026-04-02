# AI 视频剪辑开源竞品调研

> 调研时间：2026 年 4 月。聚焦 2025-2026 年间开源或发表的 AI 视频剪辑/创作项目，涵盖 Agent 驱动剪辑、短视频自动化、底层生成模型和学术前沿四个方向。

---

## 概述

FireRed-OpenStoryline（小红书，2026.2 开源）是目前为数不多的**端到端 AI 视频剪辑 Agent 产品**：对话式交互 + 完整流水线（素材搜索 → 分镜 → 理解 → 文案 → 配音 → 配乐 → 渲染）+ 技能归档复用。

本文梳理同赛道的竞品和相关项目，分为四个层次：

| 层次 | 说明 | 数量 |
|------|------|------|
| 第一梯队 | Agent 驱动的视频剪辑（最直接竞品） | 4 个 |
| 第二梯队 | 短视频自动化工具（简化流水线） | 4 个 |
| 第三梯队 | 底层视频生成模型（互补/可集成） | 8 个 |
| 学术前沿 | 论文级工作（代表未来方向） | 4 篇 |

---

## 第一梯队：Agent 驱动的视频剪辑

这些是与 OpenStoryline 最直接可比的项目——都使用 LLM Agent 来编排视频剪辑工作流。

### 1. VideoAgent — 多 Agent 视频理解、编辑与再创作框架

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/HKUDS/VideoAgent |
| **团队** | 香港大学数据智能实验室 (HKUDS) |
| **时间** | 2025 |
| **Stars** | ~550 |
| **许可** | MIT |

**核心特点：**
- 30+ 专业 Agent 协作的全流程框架
- 图结构工作流引导，支持自反思和自评估
- 智能意图分解：同时捕捉显式和隐式子意图
- 自适应反馈循环
- 工作流编排成功率 87-98%，API 成本降低 60%
- 支持 Claude 3.7、GPT-4o、Deepseek-v3

**对比 OpenStoryline：**
- 优势：多 Agent 架构比单 Agent ReAct 更高效，有图规划能力
- 不足：缺少产品化 UI、配乐/配音/字体推荐、技能归档系统，偏学术研究

---

### 2. UniVA — 通用视频 Agent

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/univa-agent/univa |
| **团队** | 新加坡管理大学 + 罗切斯特大学 + UCL + 新国大 + 港中文 + 斯坦福 |
| **时间** | 2025.11 |
| **Stars** | 新项目 |

**核心特点：**
- **Plan-and-Act 双 Agent 架构**：规划 Agent + 执行 Agent 分离
- 基于 MCP 的模块化工具服务器
- **三层记忆系统**：全局知识 / 任务上下文 / 用户偏好
- 统一视频理解、分割、编辑和生成
- 附带 UniVA-Bench 多步视频任务评测集

**对比 OpenStoryline：**
- 优势：有规划能力（直接补了 OpenStoryline「单 Agent 无规划」的短板）、有记忆持久化、用了 MCP 协议
- 不足：更偏研究框架，无产品化界面，无多轮对话式编辑体验

**值得关注的原因：** 架构设计上同时解决了 OpenStoryline 的两个核心局限——缺乏规划能力和缺乏会话持久化。

---

### 3. EditDuet — 多 Agent 非线性视频编辑

| 属性 | 信息 |
|------|------|
| **论文** | arXiv 2509.10761 |
| **团队** | TTI-Chicago + Adobe Research + CIIRC CTU |
| **时间** | 2025.9 (SIGGRAPH 2025) |
| **开源** | 未开源 |

**核心特点：**
- **Editor + Critic 双 Agent 架构**
- Editor Agent 使用 NLE 软件原生工具执行编辑
- Critic Agent 提供自然语言反馈，迭代优化直到满意
- 专注 B-roll 非线性编辑场景
- 自监督探索机制

**对比 OpenStoryline：**
- 优势：对接专业 NLE 工具（剪辑能力远超 MoviePy），Critic Agent 做质量把关是很好的思路
- 不足：Adobe 出品但未开源，仅限 B-roll 场景

**值得关注的原因：** Critic Agent 的反馈循环机制值得借鉴——当前 OpenStoryline 生成即最终结果，没有自动质检环节。

---

### 4. OpenMontage — 开源 Agent 视频制作系统

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/calesthio/OpenMontage |
| **团队** | 社区/独立 |
| **时间** | 2025-2026 |
| **Stars** | 新项目 |

**核心特点：**
- 号称「首个开源 Agent 视频制作系统」
- 11 条流水线、49 个工具、400+ Agent 技能
- Agent-first 架构：AI 编程助手即编排器
- 网络调研作为一等公民流水线阶段
- 支持本地和云端供应商

**对比 OpenStoryline：**
- 优势：工具和技能数量更多，设计野心大
- 不足：非常新，成熟度和稳定性远不及有小红书生产环境验证的 OpenStoryline

---

## 第二梯队：短视频自动化工具

这些项目的流水线更简化，目标是快速出片而非精细编辑。

### 5. MoneyPrinterTurbo — 一键短视频生成

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/harry0703/MoneyPrinterTurbo |
| **团队** | 独立开发者 (harry0703) |
| **时间** | 2024，持续更新至 2026 |
| **Stars** | **~50,000** |

**核心特点：**
- 输入一个关键词/话题，一键生成短视频
- 自动生成脚本、搜索素材、添加字幕和 BGM
- 支持多种 LLM 后端（OpenAI、DeepSeek、Gemini、Ollama 等）
- 社区极其活跃

**对比 OpenStoryline：**
- 定位差异：MoneyPrinterTurbo 是「批量内容工厂」，OpenStoryline 是「AI 导演助手」
- 50k stars 说明市场需求巨大，但没有对话式编辑、多轮修改、风格迁移等能力
- 适合快速出量，不适合追求质量

---

### 6. FunClip — ASR 驱动的智能剪辑

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/modelscope/FunClip |
| **团队** | 阿里达摩院 / 通义 |
| **时间** | 2024，持续更新至 2026 |
| **Stars** | ~5,400 |

**核心特点：**
- 基于 FunASR Paraformer 模型的语音识别驱动剪辑
- LLM 集成做智能编辑决策
- 说话人识别和热词定制
- Gradio UI + SRT 字幕生成

**对比 OpenStoryline：**
- 只覆盖了 OpenStoryline 的 LocalASR + SpeechRoughCut 子功能
- 缺乏端到端视频创作流程（无文案生成、配乐、渲染等）
- 在语音剪辑这个垂直方向上更专精

---

### 7. Kimu — 开源视频编辑器 + AI

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/trykimu/videoeditor |
| **团队** | 独立团队 |
| **时间** | 2025，更新至 2026.3 |
| **Stars** | ~1,300 |

**核心特点：**
- 开源 CapCut/Canva 替代品
- 传统多轨编辑器 + AI 辅助（描述需求，AI 生成编辑）
- 实时预览、协作、插件系统
- 使用 Gemini API，TypeScript 实现

**对比 OpenStoryline：**
- 完全不同的范式：传统编辑器 + AI Copilot vs 纯对话式 Agent
- Kimu 更适合有剪辑基础的用户，OpenStoryline 更适合零基础用户

---

### 8. short-video-maker — MCP 短视频服务

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/gyoridavid/short-video-maker |
| **团队** | 独立开发者 |
| **时间** | 2025.4 |
| **Stars** | ~1,000 |

**核心特点：**
- MCP Server + REST API 生成短视频
- 组合 TTS + 字幕 + 背景视频 + 音乐
- 无需 GPU，轻量部署
- 面向 TikTok / Reels / YouTube Shorts

**对比 OpenStoryline：**
- 流水线极其简化，无对话式交互
- 有趣的是也使用 MCP 协议，但能力远不及 OpenStoryline

---

## 第三梯队：底层视频生成模型

这些不是竞品，而是 OpenStoryline **未来可以集成的基座能力**。如果 OpenStoryline 接入这些模型，渲染和画面生成能力会产生质变。

### 9. VACE + Wan 2.1/2.2 (阿里通义)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/ali-vilab/VACE / https://github.com/Wan-Video/Wan2.2 |
| **Stars** | Wan2.2 ~14,400 |
| **时间** | 2025.3 (ICCV 2025) |

**亮点：** 统一的视频创作+编辑框架。通过 Video Condition Unit (VCU) 用单个模型处理参考视频生成、视频编辑、遮罩编辑等多种任务。VBench 榜首。14B 和 1.3B 两个尺寸。可做 Move-Anything / Swap-Anything / Animate-Anything。

---

### 10. Open-Sora 2.0 (HPC-AI Tech)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/hpcaitech/Open-Sora |
| **Stars** | ~27,600 |
| **时间** | 2025.3 |

**亮点：** 11B 参数，完全开源权重和训练代码。训练成本仅 $200K。与 Sora 的质量差距缩小到 0.69%。社区生态成熟。

---

### 11. HunyuanVideo 1.5 (腾讯)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/Tencent-Hunyuan/HunyuanVideo |
| **Stars** | 大量 |
| **时间** | 2025.11 |

**亮点：** 13B+ 参数。消费级 GPU 可推理（RTX 4090 上 480p 75 秒）。多变体：I2V（图生视频）、Avatar（数字人）、Foley（音效生成）。

---

### 12. SkyReels V2/V3 (昆仑万维)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/SkyworkAI/SkyReels-V2 / SkyReels-V3 |
| **Stars** | V2 ~5,700 |
| **时间** | V2 2025.4, V3 2026.1 |

**亮点：** 自回归扩散强制架构，支持无限时长视频生成。V3 对标闭源 SOTA。多主体、音频引导、视频到视频生成。

---

### 13. Waver 1.0 (字节跳动)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/FoundationVision/Waver |
| **Stars** | ~870 |
| **时间** | 2025.8 |

**亮点：** T2V + I2V + T2I 统一模型。支持 1080p。Hybrid Stream DiT 架构。Artificial Analysis 排行榜 T2V 和 I2V 双 Top 3。

---

### 14. Index-AniSora (B站)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/bilibili/Index-anisora |
| **Stars** | ~2,400 |
| **时间** | 2025, V3 2025.8 |

**亮点：** 动漫风格视频生成。支持多种动漫风格、角色 3D 视频生成、风格迁移、多模态引导。Apache 2.0 开源。

---

### 15. LTX-Video / LTX-2 (Lightricks)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/Lightricks/LTX-Video |
| **Stars** | ~9,300 |
| **时间** | 2025 末 |

**亮点：** 首个 DiT 架构的音视频联合生成模型。实时生成 30fps（1216x704）。最长 60 秒。单个模型同时生成音频和视频。

---

### 16. MAGI-1 (Sand AI)

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/SandAI-org/MAGI-1 |
| **Stars** | ~3,600 |
| **时间** | 2025.4 |

**亮点：** 自回归扩散架构。并发分块处理提升生成效率。4.5B 参数。开源模型中的 SOTA。Apache 2.0。

---

## 学术前沿

这些论文代表了 AI 视频剪辑的未来方向。

### 17. From Shots to Stories — LLM 辅助电影剪辑

| 属性 | 信息 |
|------|------|
| **论文** | arXiv 2505.12237 |
| **时间** | 2025.5 |

**核心创新：** 提出 **L-Storyboard** 中间表示，将离散视频镜头转为结构化语言描述供 LLM 处理。区分收敛型 vs 发散型任务，提出 StoryFlow 策略将发散推理转为收敛选择。**首个系统性研究 LLM 在电影剪辑中应用的工作。**

---

### 18. EditDuet (前文已述)

SIGGRAPH 2025，Adobe Research。Editor + Critic 双 Agent 迭代优化。

---

### 19. Preacher — 论文到视频的 Agent 系统

| 属性 | 信息 |
|------|------|
| **GitHub** | https://github.com/Gen-Verse/Paper2Video |
| **团队** | 北京大学 (凌阳等) |
| **时间** | 2025.8 (ICCV 2025) |

**核心创新：** 首个论文到视频的 Agent 系统。自上而下（分解、总结、重新表述）+ 自下而上（视频生成）。渐进式思维链（P-CoT）实现细粒度迭代规划。

---

### 20. OmniWeaving — 全能级统一视频生成

| 属性 | 信息 |
|------|------|
| **论文** | arXiv 2603.24458 |
| **团队** | 阿里（推测） |
| **时间** | 2026.3 |

**核心创新：** 全能级视频生成模型，支持自由组合多模态输入（交错的文本/图片/视频 + 时空绑定）和抽象推理（Agent 式推断复杂用户意图）。提出 IntelligentVBench，首个智能统一视频生成评测基准。

---

## 竞争格局总结

### OpenStoryline 的核心护城河

1. **端到端产品化**：唯一把完整剪辑流程跑通并产品化的开源项目（对话 UI + 素材搜索 + 分镜 + 文案 + 配音 + 配乐 + 字体 + 渲染）
2. **技能归档系统**：Skill 机制可保存剪辑工作流并复用，竞品没有
3. **Few-shot 风格迁移**：通过参考文本复制语气、节奏、句式结构
4. **生产环境验证**：小红书内部使用场景验证，非纯学术原型
5. **多轮对话式编辑**：即时修改，其他竞品多为一次性生成

### 最值得关注的威胁

| 项目 | 威胁维度 | 原因 |
|------|----------|------|
| **UniVA** | 架构 | Plan-and-Act 双 Agent + MCP + 三层记忆，直接补了 OpenStoryline「无规划」和「无持久化」两个痛点 |
| **VideoAgent** | 效率 | 多 Agent 图工作流 + 自反思，比单 Agent ReAct 更高效，成本低 60% |
| **VACE (Wan 2.1)** | 渲染 | 如果集成了阿里的统一视频编辑模型，画面能力会质变 |
| **MoneyPrinterTurbo** | 市场 | 50k stars 证明「一键出片」需求巨大，可能倒逼 OpenStoryline 做简化版 |
| **EditDuet** | 质量 | Critic Agent 反馈循环值得借鉴，当前 OpenStoryline 无自动质检 |

### 潜在演进方向

基于竞品分析，OpenStoryline 可能的优化路径：

1. **引入规划 Agent**（参考 UniVA 的 Plan-and-Act）— 解决单 Agent 串行决策问题
2. **引入质检 Agent**（参考 EditDuet 的 Critic）— 渲染前自动评估质量
3. **集成视频生成模型**（如 VACE/Open-Sora）— 突破 MoviePy 渲染天花板
4. **支持并行节点执行** — select_bgm 和 generate_voiceover 可同时进行
5. **添加 LLM 抽象层**（参考 MoneyPrinterTurbo 的多后端支持）— 摆脱 OpenAI 兼容接口锁定

---

## 中国大厂开源视频 AI 项目一览

| 公司 | 项目 | 类型 | Stars |
|------|------|------|-------|
| **小红书** | FireRed-OpenStoryline | 剪辑 Agent | ~2k |
| **阿里** | FunClip | ASR 剪辑 | ~5.4k |
| **阿里** | VACE / Wan 2.1-2.2 | 视频生成+编辑模型 | ~14.4k |
| **腾讯** | HunyuanVideo | 视频生成模型 | 大量 |
| **字节跳动** | Waver | 视频生成模型 | ~870 |
| **昆仑万维** | SkyReels V2/V3 | 视频生成模型 | ~5.7k |
| **B站** | Index-AniSora | 动漫视频生成 | ~2.4k |
| **智谱/清华** | CogVideoX | 视频生成模型 | ~12.5k |

目前大厂开源的主要是底层生成模型，小红书是唯一开源了**剪辑 Agent 产品**的。
