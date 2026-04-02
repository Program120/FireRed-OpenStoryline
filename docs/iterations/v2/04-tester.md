# V2 测试方案

---

## 一、测试分层

| 层级 | 范围 | 工具 |
|------|------|------|
| 单元测试 | 模块内部逻辑 | pytest + mock |
| 集成测试 | DAG + FFmpeg 渲染 | pytest + 真实 FFmpeg |
| 视觉回归 | 渲染输出帧对比 | SSIM/PSNR + 人工抽检 |
| 性能基准 | 延迟/吞吐量 | pytest-benchmark |
| 端到端 | 用户指令→最终视频 | 自动化脚本 + 手动验收 |

---

## 二、P0-1 多平台自适应 测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-ADAPT-01 | 人物偏左侧的 1080p 视频主体检测 | CropRegion.cx < 0.5, confidence > 0.5 |
| TC-ADAPT-02 | 纯风景视频无人物 | Fallback 到画面中心，无异常 |
| TC-ADAPT-03 | 主体从左走到右的裁切平滑度 | 相邻帧 cx 差分 < 0.05，无跳变 |
| TC-ADAPT-04 | 三比例输出尺寸 | 16:9→854x480, 9:16→270x480, 1:1→480x480, 时长一致 |
| TC-ADAPT-05 | 字幕安全区不越界 | 三种比例字幕均在安全区内 |
| TC-ADAPT-06 | 竖屏节奏调整 | 10s segment → 9:16 输出 7.5s，从开头截取 |
| TC-ADAPT-07 | 增量渲染——仅改一个比例裁切 | 执行计划只含该 adapt 节点 + 对应 concat |
| TC-ADAPT-08 | 场景切换处关键帧 | 切换点前后各有关键帧，裁切无异常漂移 |

---

## 三、P0-2 高级转场 测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-TRANS-01 | 8 种转场 × 3 种时长 filter 语法 | FFmpeg 解析无报错 |
| TC-TRANS-02 | 5 segments offset 累计精度 | 总时长 = Σseg_dur - Σtrans_dur，偏差 < 1 帧 |
| TC-TRANS-03 | 转场时长边界值 (0.29/0.3/1.5/1.51) | 合法值通过，非法值抛 ValueError |
| TC-TRANS-04 | 转场与音频同步 | acrossfade 与 xfade 时间点对齐 |
| TC-TRANS-05 | transition dirty → 执行计划 | 只含 concat 重渲，不含 segment:render |
| TC-TRANS-06 | 单 segment 无转场 | 无 xfade filter，正常输出 |
| TC-TRANS-07 | 转场时长 > 片段时长 | 明确错误提示 |

---

## 四、P1-1 语义理解 测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-CLIP-01 | 同视频两次 embedding | shape=(512,), cosine sim > 0.99 |
| TC-CLIP-02 | 10 clips 分 3 类聚类 | 同类片段在同一 cluster |
| TC-CLIP-03 | 同内容不同文件名重复检测 | similarity > 0.95 |
| TC-CLIP-04 | SQLite 缓存命中 | 不重复推理 |
| TC-CLIP-05 | 增量聚类更新 | 新 clip 入库，聚类更新合理 |

---

## 五、P1-2 调色 测试用例

| ID | 用例 | 预期 |
|----|------|------|
| TC-COLOR-01 | 6 个 LUT 输出验证 | 与 golden file SSIM > 0.95 |
| TC-COLOR-02 | 亮度/对比度/饱和度极值 | sat=0.0 输出灰度，不崩溃 |
| TC-COLOR-03 | 自动白平衡统一 | 输出色温向参考片段靠拢，通道差异 < 10% |

---

## 六、性能基准

| 指标 | 目标 |
|------|------|
| YOLO 关键帧检测 (30s, CPU) | < 3s |
| CLIP 嵌入 (单片段 5 帧, CPU) | < 2s |
| 100 clips 聚类 | < 100ms |
| xfade filter 生成 (20 segments) | < 10ms |
| 三比例预览 (单 segment, 480p) | < 5s |
| 增量渲染 (改 1 segment / 10 total) | 仅 1 segment + concat |

---

## 七、对研发方案的风险补充

### 1. FFmpeg 版本碎片化
xfade easing 需 7.0+，colortemperature 需 5.1+。**建议：** CI 测试 5.1/6.0/7.0 三版本，不支持时优雅降级。

### 2. YOLO COCO 类别局限
纯文字画面、动画素材、特定商品检测效果差。**建议：** 加入"无 COCO 类别主体"测试视频，评估 GroundingDINO 作为 V2.1 备选。

### 3. xfade 链错误恢复
20 segments 在第 15 个 transition 报错无法部分恢复。**建议：** 二分法定位出错 transition 或改为两两渲染再 concat。

### 4. CLIP 对中文场景的语义偏差
open_clip 主要英文训练。**建议：** 评估 Chinese-CLIP / EVA-CLIP，加入中文场景素材测试。

### 5. 三比例同时渲染资源消耗
20 segments × 3 ratios = 60 渲染任务。**建议：** 支持 lazy rendering（只渲选中比例），覆盖大项目性能测试。

### 6. 自动白平衡参考片段选择
极端色彩参考（如夕阳）会拉偏所有片段。**建议：** 自动模式取所有片段色温中位数而非单一参考；提供预览/警告。

---

## 八、测试执行优先级

| 周 | 内容 |
|----|------|
| W1 | TC-TRANS 全部（转场零容错） |
| W2 | TC-COLOR（LUT 确定性输出，易自动化） |
| W3-W4 | TC-ADAPT（重点：fallback + 增量渲染） |
| W5 | TC-CLIP |
| W6 | 性能基准 + 端到端集成 + 视觉回归基线 |
