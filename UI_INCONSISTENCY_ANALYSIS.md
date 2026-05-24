# PDF 解析前端 UI 一致性问题 - 深度检查与修改建议

**检查日期**: 2026-05-18  
**检查范围**: `douge_ai_agent` 与 `pdf2md_web` 的"优先复核表"功能对比  
**问题等级**: 🔴 **数据口径不一致** - 需要优先修复

---

## 📋 执行摘要

通过对两个项目的前端源代码进行深度对比分析，发现**"优先复核表"的显示数据口径严重不一致**：

| 项目 | 显示格式 | 包含信息 | 完整度 |
|------|--------|--------|--------|
| **douge_ai_agent** | `表 73 · 空单元格偏多` | 表号、理由 | ❌ 不完整 |
| **pdf2md_web** | `打开表 73 / 行 1790 / PDF 89页 · 空单元格偏多` | 表号、行号、页码、理由 | ✅ 完整 |

---

## 🔍 第一部分：问题详解

### 问题 1.1：用户观察到的两种显示格式

#### 格式 A（简洁版 - 优先复核表概览）
```
表 73 · 空单元格偏多
表 2 · 数字密度偏低
表 66 · 空单元格偏多
表 72 · 空单元格偏多
表 74 · 空单元格偏多
```

#### 格式 B（详细版 - 打开表时的说明）
```
打开表 73 / 行 1790 / PDF 89页 · 空单元格偏多
打开表 2 / 行 74 / PDF 5页 · 数字密度偏低
打开表 66 / 行 1713 / PDF 73页 · 空单元格偏多
打开表 72 / 行 1783 / PDF 85页 · 空单元格偏多
打开表 74 / 行 1792 / PDF 90页 · 空单元格偏多
打开表 75 / 行 1795 / PDF 90页(推断) · 空单元格偏多
```

**核心差异**：
- 格式 A 缺少：`/ 行 {line}` 和 `/ PDF {page}页` 的位置信息
- 格式 B 多了第 75 张表，且带有"(推断)"标记

---

### 问题 1.2：代码层面的实现对比

#### douge_ai_agent 源码位置
**文件**: `finall_all_front_0516/front/src/pages/PdfParsing.tsx`  
**行号**: 841-842  
**代码**:
```jsx
<div className="pdf-quality-section">
  <div className="pdf-quality-section-title">优先复核表</div>
  <ul className="list-disc pl-5 text-sm text-text">
    {susp.length?susp.map((s:any,i:number)=>(
      <li key={i}>
        {s.table_index?
          <button className="pdf-trace-btn" onClick={()=>showTableSource(s.table_index,s.line)}>
            表 {s.table_index}                           {/* ⚠️  只显示表号 */}
          </button>:
          <span className="text-text-muted">表 未定位</span>
        }
        {' · '}
        {suspectReasons(s.suspect_reasons||[])}
      </li>
    )):<li>未发现可疑表样本</li>}
  </ul>
</div>
```

**问题分析**：
- ❌ 仅显示表号 `s.table_index`
- ❌ 未显示行号 `s.line`（虽然传给了 showTableSource）
- ❌ 未显示 PDF 页码 `s.pdf_page_number`
- ❌ 未显示页码来源 `s.pdf_page_source`（推断标记）

#### pdf2md_web 源码位置
**文件**: `static/app.js`  
**行号**: 282-301（tableButton 函数）、1159（调用点）

**tableButton 函数**:
```javascript
function tableButton(item, labelPrefix) {
  if (!item || !item.table_index) {
    return '<span class="quality-muted">' + 
           escapeHtml((labelPrefix || "表") + " 未定位") + 
           "</span>";
  }
  
  // ✅ 构建页码部分
  const page = item.pdf_page_number
    ? " / PDF " + item.pdf_page_number + "页" + 
      (item.pdf_page_source === "markdown_marker_inferred" ? "(推断)" : "")
    : "";
  
  // ✅ 构建行号部分
  const line = item.line ? " / 行 " + item.line : "";
  
  // ✅ 完整标签
  const label = (labelPrefix || "表") + " " + item.table_index + line + page;
  
  return '<button class="trace-btn" data-table-index="' +
         escapeHtml(String(item.table_index)) +
         '" data-line="' +
         escapeHtml(String(item.line)) +
         '">' +
         escapeHtml(label) +
         "</button>";
}
```

**优点分析**：
- ✅ 完整显示：`表 {index} / 行 {line} / PDF {page}页{推断标记}`
- ✅ 支持推断标记：`pdf_page_source === "markdown_marker_inferred"`
- ✅ 安全转义：使用 escapeHtml
- ✅ 数据属性绑定：data-table-index、data-line

**调用点**（第1159行）:
```javascript
suspicious.map((item) => {
  return '<li>' +
         tableButton(item, "打开表") +      {/* 调用 tableButton，labelPrefix="打开表" */}
         " · " +
         escapeHtml(formatSuspectReasons(item.suspect_reasons || [])) +
         "</li>";
}).join("")
```

---

### 问题 1.3：相关辅助函数对比

#### suspectReasons / formatSuspectReasons（两项目实现一致）

**douge_ai_agent** (第251-253行):
```typescript
function suspectReasons(reasons: string[]): string {
  const m: Record<string,string> = {
    single_row:'单行/空壳',
    many_empty_cells:'空单元格偏多',
    low_numeric_density:'数字密度偏低',
    key_table_too_short:'关键表过短',
    low_confidence_core_candidate:'低置信核心候选',
    medium_confidence_core_candidate:'中置信核心候选'
  }
  return (reasons||[]).map(r=>m[r]||r).join('、')
}
```

**pdf2md_web** (第1121-1132行):
```javascript
function formatSuspectReasons(reasons) {
  const map = {
    single_row: "单行/空壳",
    many_empty_cells: "空单元格偏多",
    low_numeric_density: "数字密度偏低",
    key_table_too_short: "关键表过短",
    low_confidence_core_candidate: "低置信核心候选",
    medium_confidence_core_candidate: "中置信核心候选"
  };
  return (reasons || []).map((reason) => map[reason] || reason).join("、");
}
```

✅ **两个实现几乎相同**，无差异。

---

## ✨ 第二部分：候选元数据函数利用

### 已有但未充分利用的函数

#### douge_ai_agent 中的 candidateMeta 函数（第245-250行）

```typescript
function candidateMeta(item: any): string {
  if (!item || item.status==='missing') return '需复核：未在表格中定位'
  if (!item.table_index) return item._source==='financial_data'?'已抽取，暂无表格定位':'需复核：暂无表格定位'
  
  // 这部分逻辑正是 pdf2md_web tableButton 做的事情！
  const page = item.pdf_page_number 
    ? ` / PDF ${item.pdf_page_number}页${item.pdf_page_source==='markdown_marker_inferred'?'(推断)':''}` 
    : ''
  const cMap: Record<string,string> = {high:'高置信',medium:'中置信',low:'低置信'}
  const conf = item.confidence ? ` / ${cMap[item.confidence]||item.confidence}` : ''
  const line = item.line ? ` / 行 ${item.line}` : ''
  
  return `表 ${item.table_index}${line}${page}${conf}`
}
```

**现状**：
- 在"关键表候选"中被使用（第836-840行）
- 在"指标/经营分析候选"中被使用（第840行）
- ⚠️ **"优先复核表"中未使用该函数** ← 这是主要问题源头

---

## 🔧 第三部分：修改建议（执行方案）

### 建议 #1：创建 suspectTableMeta 函数（推荐方案）

在 `PdfParsing.tsx` 中，修改或创建专门的函数来处理可疑表的元数据显示：

```typescript
function suspectTableMeta(item: any): string {
  if (!item || !item.table_index) return '表 未定位'
  
  // 格式化行号
  const line = item.line ? ` / 行 ${item.line}` : ''
  
  // 格式化 PDF 页码和推断标记
  const page = item.pdf_page_number
    ? ` / PDF ${item.pdf_page_number}页${item.pdf_page_source==='markdown_marker_inferred'?'(推断)':''}`
    : ''
  
  return `表 ${item.table_index}${line}${page}`
}
```

**使用位置变更**（第841-842行）：

```jsx
{susp.length?susp.map((s:any,i:number)=>(
  <li key={i}>
    {s.table_index?
      <button className="pdf-trace-btn" onClick={()=>showTableSource(s.table_index,s.line)}>
        {suspectTableMeta(s)}    {/* 调用新函数，输出完整信息 */}
      </button>:
      <span className="text-text-muted">表 未定位</span>
    }
    {' · '}
    {suspectReasons(s.suspect_reasons||[])}
  </li>
)):<li>未发现可疑表样本</li>}
```

**输出示例**：
```
表 73 / 行 1790 / PDF 89页 · 空单元格偏多
表 2 / 行 74 / PDF 5页 · 数字密度偏低
```

---

### 建议 #2：复用已有的 candidateMeta 函数（替代方案）

不创建新函数，直接在"优先复核表"中使用现有的 `candidateMeta` 函数：

```jsx
{susp.length?susp.map((s:any,i:number)=>(
  <li key={i}>
    {s.table_index?
      <button className="pdf-trace-btn" onClick={()=>showTableSource(s.table_index,s.line)}>
        {candidateMeta(s)}       {/* 复用现有函数 */}
      </button>:
      <span className="text-text-muted">表 未定位</span>
    }
    {' · '}
    {suspectReasons(s.suspect_reasons||[])}
  </li>
)):<li>未发现可疑表样本</li>}
```

**优点**：
- ✅ 避免代码重复
- ✅ 一致的元数据格式
- ✅ 便于后续维护

---

### 建议 #3：按钮文本前缀调整（UI/UX 优化）

为了与 pdf2md_web 保持一致，可考虑：

**当前**：`表 73 / 行 1790 / PDF 89页 · 空单元格偏多`

**可选**：`打开表 73 / 行 1790 / PDF 89页 · 空单元格偏多`

修改方式 - 创建带前缀的函数：

```typescript
function suspectTableMeta(item: any, prefix: string = ''): string {
  if (!item || !item.table_index) return prefix + '表 未定位'
  
  const line = item.line ? ` / 行 ${item.line}` : ''
  const page = item.pdf_page_number
    ? ` / PDF ${item.pdf_page_number}页${item.pdf_page_source==='markdown_marker_inferred'?'(推断)':''}`
    : ''
  
  return prefix ? `${prefix} ${item.table_index}${line}${page}` 
                : `表 ${item.table_index}${line}${page}`
}
```

使用：
```jsx
<button className="pdf-trace-btn" onClick={()=>showTableSource(s.table_index,s.line)}>
  {suspectTableMeta(s, '打开表')}
</button>
```

---

## 📊 第四部分：数据结构验证

### 后端 API 返回的 suspicious_tables 应包含字段

检查后端 `quality` endpoint 返回的数据结构是否包含以下字段：

```json
{
  "suspicious_tables": [
    {
      "table_index": 73,           // ✅ 必需
      "line": 1790,                // ⚠️ 需确认存在
      "pdf_page_number": 89,       // ⚠️ 需确认存在
      "pdf_page_source": "markdown_marker_inferred",  // ⚠️ 需确认存在
      "suspect_reasons": ["many_empty_cells"],        // ✅ 必需
      "empty_ratio": 0.45,
      "numeric_ratio": 0.30
    }
  ]
}
```

### 验证清单

- [ ] 确认后端 API 返回 `line` 字段（用于"行号"显示）
- [ ] 确认后端 API 返回 `pdf_page_number` 字段（用于"PDF页码"显示）
- [ ] 确认后端 API 返回 `pdf_page_source` 字段（用于"推断"标记）
- [ ] 如果某字段缺失，需要后端补充或前端实现 fallback 逻辑

---

## 🎯 第五部分：跨项目一致性检查清单

| 检查项 | douge_ai_agent | pdf2md_web | 状态 |
|--------|----------------|-----------|------|
| 表号显示 | ✅ 是 | ✅ 是 | ✅ 一致 |
| 行号显示 | ❌ 否 | ✅ 是 | ❌ **不一致** |
| PDF 页码显示 | ❌ 否 | ✅ 是 | ❌ **不一致** |
| 推断标记 | ❌ 否 | ✅ 是 | ❌ **不一致** |
| 可疑原因显示 | ✅ 是 | ✅ 是 | ✅ 一致 |
| 按钮交互 | ✅ 是 | ✅ 是 | ✅ 一致 |
| 按钮样式 | `pdf-trace-btn` | `trace-btn` | ⚠️ 名称不同但功能相同 |

---

## 🛠️ 第六部分：实施步骤

### Phase 1: 验证与准备
1. **验证后端数据结构**
   - 检查 `/api/quality/{task_id}` endpoint 返回的 `suspicious_tables`
   - 确认所有必要字段都包含：`line`, `pdf_page_number`, `pdf_page_source`
   - 若缺失，需要联系后端团队补充

2. **文档化当前状态**
   - 记录实际返回的数据样本
   - 对比 pdf2md_web 的 API 返回格式

### Phase 2: 代码修改
1. **添加新函数或修改现有函数**
   ```typescript
   // Option A: 创建新函数 (推荐)
   function suspectTableMeta(item: any): string { ... }
   
   // Option B: 复用现有函数
   // 直接使用 candidateMeta(s)
   ```

2. **更新"优先复核表"渲染逻辑**
   - 位置：第841-842行
   - 修改按钮文本从 `表 {index}` 改为 `{suspectTableMeta(s)}`

3. **测试所有场景**
   - ✅ 表有完整信息时
   - ✅ 表缺少 line 或 pdf_page_number 时
   - ✅ pdf_page_source 为 "markdown_marker_inferred" 时
   - ✅ 未定位的表 (table_index 为空) 时

### Phase 3: UI/UX 优化
1. **按钮文本前缀**
   - 考虑是否需要添加"打开表"前缀
   - 验证与用户的操作直觉是否匹配

2. **响应式设计**
   - 在移动端测试长文本显示
   - 考虑 tooltip 或换行处理

3. **样式命名一致性**
   - 考虑统一 `pdf-trace-btn` 和 `pdf-chip trace-chip` 的使用场景

### Phase 4: 验收与发布
1. **对比输出**
   ```
   修改前: 表 73 · 空单元格偏多
   修改后: 表 73 / 行 1790 / PDF 89页 · 空单元格偏多
   ```

2. **与 pdf2md_web 功能对齐**
   - 确保两个项目"优先复核表"显示信息完全一致

3. **性能测试**
   - 确保在大量可疑表 (100+) 情况下不影响渲染性能

---

## ⚠️ 注意事项

### 数据完整性假设
本建议基于以下假设：
- 后端 API 返回的 `suspicious_tables` 包含 `line` 和 `pdf_page_number` 字段
- 若这些字段在后端不存在，需要先修改后端 API

### 向后兼容性
- 建议增加 fallback 逻辑：若某字段缺失，仍能正常显示
  ```typescript
  const line = item.line ? ` / 行 ${item.line}` : ''  // 如果没有就不显示
  ```

### 边界情况处理
- ✅ table_index 为空：已有处理
- ✅ line 为空：可选显示
- ✅ pdf_page_number 为空：可选显示
- ✅ pdf_page_source 异常值：使用精确的 === 比较

---

## 📈 预期效果

### 修改前
```
解析质量报告
├─ 关键表候选
│  └─ 表 73 / 行 1790 / PDF 89页 · 高置信
├─ 指标/经营分析候选
│  └─ 表 10 / 行 500 / PDF 30页 · 低置信
└─ 优先复核表
   ├─ 表 73 · 空单元格偏多              ❌ 信息不完整
   ├─ 表 2 · 数字密度偏低
   ├─ 表 66 · 空单元格偏多
   └─ ...
```

### 修改后
```
解析质量报告
├─ 关键表候选
│  └─ 表 73 / 行 1790 / PDF 89页 · 高置信
├─ 指标/经营分析候选
│  └─ 表 10 / 行 500 / PDF 30页 · 低置信
└─ 优先复核表
   ├─ 表 73 / 行 1790 / PDF 89页 · 空单元格偏多        ✅ 完整信息
   ├─ 表 2 / 行 74 / PDF 5页 · 数字密度偏低
   ├─ 表 66 / 行 1713 / PDF 73页 · 空单元格偏多
   └─ ...
```

---

## 📝 总结

| 问题 | 严重度 | 修复难度 | 建议优先级 |
|------|--------|---------|-----------|
| 缺少 line 字段显示 | 🔴 高 | 🟢 低 | **P0 - 立即修复** |
| 缺少 pdf_page_number 字段显示 | 🔴 高 | 🟢 低 | **P0 - 立即修复** |
| 缺少推断标记 (pdf_page_source) | 🟡 中 | 🟢 低 | **P1 - 近期修复** |
| 按钮文本前缀不一致 | 🟡 中 | 🟢 低 | **P2 - 后续优化** |

**建议实施时间**: 本周内完成 P0 问题修复，确保数据口径一致。

