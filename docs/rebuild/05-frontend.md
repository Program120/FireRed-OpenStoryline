# 前端架构

---

## 技术栈

| 工具 | 说明 |
|------|------|
| React 18+ | 组件框架 |
| TypeScript | 类型安全 |
| Vite | 构建工具 |
| Tailwind CSS | 样式 |
| Zustand | 状态管理 |
| React Router v6 | 路由 (`/session/{id}`) |
| ReactFlow | Pipeline DAG 可视化 |

## 页面结构

### 首页 (`/`)

```
┌──────────────────────────────────────┐
│          OpenStoryline               │
├──────────────────────────────────────┤
│  [+ 创建新会话]                       │
│                                      │
│  最近会话                             │
│  ┌────────────────────┐              │
│  │ 旅行 Vlog 剪辑      │ 2min ago    │
│  │ 产品测评视频         │ 1h ago     │
│  │ 口播脏话剪切         │ 3h ago     │
│  └────────────────────┘              │
└──────────────────────────────────────┘
```

### 编辑工作台 (`/session/{id}`)

```
┌─────────┬────────────────────────┬──────────────┐
│ 左侧栏   │      聊天面板           │   右侧面板    │
│         │                        │              │
│ 会话列表  │ [AI] 好的，开始剪辑      │  媒体库      │
│ 模型配置  │                        │  ┌─────────┐│
│ TTS 配置 │ ┌──────────────┐      │  │ 📹 vid1 ││
│ Pexels  │ │ ✓ 读取素材    │      │  │ 📹 vid2 ││
│         │ │ ✓ 镜头分割    │      │  └─────────┘│
│         │ │ ◉ 画面理解    │ 45%  │              │
│         │ │   详细日志 (5) │      │  Pipeline   │
│         │ │ ○ 片段筛选    │      │  ┌─────────┐│
│         │ │ ○ ...        │      │  │ DAG 图   ││
│         │ └──────────────┘      │  │         ││
│         │                        │  └─────────┘│
│         │ [输入框]                │              │
└─────────┴────────────────────────┴──────────────┘
```

## 关键组件

### ToolCallCard — Skill 执行卡片

替代当前的简单进度条，展示完整的 Skill 执行状态：

```tsx
interface ToolCallCardProps {
  skillId: string;
  displayName: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress?: number;        // 0-1
  message?: string;
  logCount?: number;        // 详细日志条数
  subtasks?: {
    completed: number;
    total: number;
  };
  onViewLogs?: () => void;
}
```

功能：
- 进度条显示真实百分比
- 并行 Skill 显示子任务进度 `批次 3/4 完成`
- 「详细日志 (N)」按钮展开内联面板
- 失败时显示错误信息 + 重试按钮

### LogPanel — 详细日志面板

内联可展开面板，替代当前无法工作的 hack 方案：

```tsx
interface LogPanelProps {
  executionId: string;
  logs: LogEntry[];
}

interface LogEntry {
  level: 'info' | 'warn' | 'error';
  message: string;
  detail?: string;          // 可折叠的详细内容
  timestamp: number;
}
```

功能：
- 最大高度 400px，独立滚动条
- 每条日志可折叠展开详情（LLM prompt + response）
- 从 DB 加载（`GET /api/.../executions/{eid}/logs`），刷新后仍可查看
- 实时追加新日志（通过 `skill.log` WebSocket 事件）

### PipelineGraph — DAG 可视化

使用 ReactFlow 展示 Pipeline 的 DAG 图：

```tsx
// 每个节点显示
interface SkillNode {
  skillId: string;
  displayName: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  progress?: number;
}
```

功能：
- 节点颜色表示状态（灰=待执行，蓝=运行中，绿=完成，红=失败）
- 运行中的节点有脉冲动画
- 点击已完成的节点查看输出
- 点击"从此处恢复"可从任意节点重新执行

### MediaLibrary — 媒体库

```tsx
// 拖拽上传 + 缩略图网格 + 状态指示
```

## 状态管理 (Zustand)

```typescript
// stores/sessionStore.ts
interface SessionStore {
  currentSessionId: string | null;
  sessions: Session[];
  createSession: () => Promise<string>;
  loadSession: (id: string) => Promise<void>;
}

// stores/chatStore.ts
interface ChatStore {
  messages: ChatMessage[];
  streaming: boolean;
  sendMessage: (text: string, attachments?: string[]) => void;
  appendToken: (text: string) => void;
}

// stores/pipelineStore.ts
interface PipelineStore {
  skills: SkillStatus[];
  logs: Record<string, LogEntry[]>;
  updateSkillStatus: (skillId: string, status: SkillStatus) => void;
  appendLog: (skillId: string, log: LogEntry) => void;
}
```

## WebSocket 集成

```typescript
// hooks/useWebSocket.ts
function useWebSocket(sessionId: string) {
  // 连接 ws://host/ws/sessions/{sessionId}/chat
  // 收到 session.snapshot → 恢复所有 store
  // 收到 chat.token → chatStore.appendToken()
  // 收到 skill.start/progress/complete → pipelineStore.updateSkillStatus()
  // 收到 skill.log → pipelineStore.appendLog()
  // 断线自动重连
}
```

## Session URL 路由

```tsx
// App.tsx
<Routes>
  {/* 公开路由 */}
  <Route path="/login" element={<LoginPage />} />
  <Route path="/register" element={<RegisterPage />} />

  {/* 需要登录的路由 */}
  <Route path="/" element={<ProtectedRoute><HomePage /></ProtectedRoute>} />
  <Route path="/session/:sessionId" element={<ProtectedRoute><SessionPage /></ProtectedRoute>} />
  <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
  {/* Skill 管理页面仅管理员可见，作为 /admin/skills 路由 */}
  <Route path="/admin/skills" element={<ProtectedRoute adminOnly><AdminSkillsPage /></ProtectedRoute>} />
</Routes>
```

### ProtectedRoute 模式

```tsx
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token);
  if (!token) return <Navigate to="/login" />;
  return children;
}
```

```tsx
// 创建会话后
navigate(`/session/${newSessionId}`);

// 刷新后
// SessionPage 从 URL 获取 sessionId
// useWebSocket(sessionId) 连接并接收 snapshot
// 完整恢复 UI 状态
```
