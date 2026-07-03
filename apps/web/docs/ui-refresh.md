# SIQ Research Engine UI 刷新说明

> 范围：`apps/web`。本说明记录本轮 UI 刷新引入/修改的设计 token、组件规范与移动端适配规则。

## 1. 设计 token

所有 token 定义在 `src/styles/tokens.css` 与 `src/index.css` 的 `@theme` 区块。

### 1.1 圆角层级

| Token | 值 | 用途 |
|-------|------|------|
| `--radius-control` | 10px | 按钮、输入框、小标签 |
| `--radius-card` | 14px | 卡片、列表行、提示框 |
| `--radius-panel` | 18px | 页面分区面板 |
| `--radius-modal` | 20px | 模态框、登录卡片 |

**原则**：外层大面板用 `--radius-panel`，内层卡片用 `--radius-card`，控件用 `--radius-control`。不再使用 `rounded-[20px]/[24px]/[28px]` 等硬编码值。

### 1.2 阴影层级

| Token | 用途 |
|-------|------|
| `--shadow-card` | 卡片、磁贴 |
| `--shadow-panel` | 面板、抽屉、sticky bar |
| `--shadow-popover` | 下拉、浮层 |
| `--shadow-focus` | 聚焦环 |

### 1.3 动画变量

- `--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1)` — 弹出/展开。
- `--duration-fast: 150ms` — hover、颜色过渡。
- `--duration-normal: 220ms` — 面板、抽屉、toast。

## 2. 页面外壳规范

统一使用 `src/components/page` 下的组件构建页面结构。

### 2.1 `PageShell`

```tsx
<PageShell>
  <PageHeader ... />
  <PageSection ... />
</PageShell>
```

- 所有页面均从 `PageShell` 开始。
- `variant="secondary"` 可用于需要更突出 hero 的页面（如报告页）。

### 2.2 `PageHeader`

- `icon`：Lucide 图标，显示在 eyebrow 前。
- `eyebrow`：英文/小标题，使用 `page-kicker` 样式。
- `title`：页面主标题。
- `description`：一句话说明。
- `meta`：状态 chips，使用 `StatusBadge`。
- `actions`：全局操作按钮（刷新、返回等）。

### 2.3 `PageSection`

- 标题、描述、actions 自动排列在面板头部。
- 内容区自动带 `p-4 sm:p-5` 内边距；可通过 `contentClassName` 覆盖。
- 支持 `id` 属性，用于锚点跳转。
- `compact` 用于高密度工作台，收紧标题、描述和内容区间距。

### 2.4 `Surface`

| kind | 用途 |
|------|------|
| `card` | 独立信息卡片 |
| `panel` | 页面大分区 |
| `row` | 列表行、定义列表项 |
| `muted` | 占位、空状态、加载态 |

### 2.5 `StatusBadge`

统一使用 `components/page/StatusBadge`，通过 `tone` 控制颜色：

```tsx
<StatusBadge tone="success">已通过</StatusBadge>
```

### 2.6 `EmptyState`

空/错误状态统一使用 `EmptyState`：

```tsx
<EmptyState
  icon={Inbox}
  title="暂无数据"
  description="完成解析后数据会出现在这里。"
  action={<Button ... />}
/>
```

### 2.7 `MobileActionBar`

底部固定操作栏使用 `MobileActionBar`：

```tsx
<MobileActionBar className="...">
  <div className="flex ...">...</div>
</MobileActionBar>
```

- `className` 会应用到内容区；需要改外层容器时使用 `rootClassName`。
- 桌面端为 sticky 面板，不遮挡页面阅读。
- 移动端为 fixed bottom sheet，带拖动小横条可收起。

## 3. 移动端适配规则

### 3.1 触控目标

- 图标按钮最小 `40×40px`（`min-h-10 min-w-10`）。
- 主要按钮最小 `44×44px`（`min-h-11`）。
- 表格行在移动端 >= 48px。

### 3.2 横向滚动提示

可横向滚动的容器统一加 `.scroll-hint`：

```tsx
<div className="scroll-hint overflow-x-auto">...</div>
```

`Layout` 会通过 `useScrollHintState` 自动维护 `is-scrollable-left/right` 状态类，容器内容异步变化时也会重新计算。

### 3.3 Tab / 市场选择器

- 桌面：网格或完整卡片。
- 平板：3 列网格。
- 手机：横向滚动 pill 条，使用 `.mobile-tab-strip`，带右侧渐变提示。

### 3.4 表格

- 桌面：保留表格，加 `.scroll-hint`。
- 移动端（<=1024px 或 <=md）：改为卡片列表，把被隐藏的字段以 chip/badge 形式展示。

## 4. 动画与过渡

- 页面切换：`Layout.tsx` 的 `<Outlet />` 已包裹 `animate-in fade-in slide-in-from-bottom-2 duration-200`。
- 卡片 hover：`transform translateY(-2px)` + 阴影加深，时长 200ms。
- Toast：进入 `slide-in-from-right fade-in zoom-in-95`，退出 `slide-out-to-right fade-out zoom-out-95`。

## 5. 报告页

- `ReportViewer` 已支持骨架屏加载与加载失败重试。
- 工具栏保持公司选择 + 报告选择 + 操作按钮在同一 sticky toolbar 内。

## 6. 后续可优化方向

- 完整 dark mode 审核与补全。
- 国际化（i18n）文案抽离。
- 组件级单元测试与视觉回归测试。
- 进一步收敛 `premium-*` 旧类名到 `surface-*` 体系。
