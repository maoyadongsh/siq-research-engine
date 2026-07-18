# 产品架构

## 整体架构

```mermaid
graph TB
    subgraph Inputs["输入层"]
        A1[官方披露<br/>CN/HK/US/EU/JP/KR]
        A2[尽调材料<br/>BP/财务模型/合同/访谈]
        A3[会议音频<br/>实时/导入]
        A4[本地文档<br/>PDF/Office/HTML/图片]
        A5[URL]
    end

    subgraph AppCenter["应用中心（材料生产）"]
        B1[document-parser<br/>通用文档解析]
        B2[pdf-parser<br/>财报PDF解析]
        B3[meeting speech<br/>会议转写]
        B4[vector ingest<br/>向量入库]
    end

    subgraph Evidence["证据层（事实底座）"]
        C1[(LLM Wiki<br/>evidence package)]
        C2[(PostgreSQL<br/>结构化索引)]
        C3[(Milvus<br/>语义索引)]
        C4[(artifacts<br/>构建产物)]
    end

    subgraph ControlPlane["控制面 apps/api"]
        D1[鉴权/任务/SSE]
        D2[source access]
        D3[Deal OS]
        D4[记忆服务]
        D5[运行面选择]
    end

    subgraph Agents["智能体集群 agents/hermes"]
        E1[二级市场<br/>analysis/factcheck<br/>tracking/legal/assistant]
        E2[一级市场 IC<br/>chairman/strategy/sector<br/>finance/legal/risk]
    end

    subgraph OpenShell["NVIDIA OpenShell 安全运行面"]
        F1[网关/沙箱/Provider]
        F2[Broker/策略/灰度/回滚]
    end

    subgraph Web["Web 工作台 apps/web"]
        G1[二级市场]
        G2[一级市场]
        G3[应用中心]
        G4[系统管理]
    end

    A1 --> B1
    A2 --> B2
    A3 --> B3
    A4 --> B1
    A5 --> B1
    B1 --> C1
    B2 --> C1
    B3 --> C1
    B4 --> C3
    C1 --> C2
    C1 --> C3
    C1 --> D1
    C2 --> D1
    C3 --> D1
    C4 --> D1
    D1 --> D2
    D1 --> D3
    D1 --> D4
    D1 --> D5
    D5 --> F1
    F1 --> F2
    F1 --> E1
    F1 --> E2
    E1 --> G1
    E1 --> G2
    E2 --> G2
    E2 --> G3
    E1 --> G4

```

## 五层架构

### 1. 输入层

- 官方披露（CN/HK/US/EU/JP/KR 六市场）
- 尽调材料（BP、财务模型、合同、访谈、第三方报告）
- 会议音频（实时/导入）
- 本地文档（PDF、Office、HTML、图片）
- URL

### 2. 应用中心（材料生产）

- `document-parser` —— 通用文档解析
- `pdf-parser` —— 财报 PDF 解析
- meeting speech —— 会议转写
- vector ingest —— 向量入库

### 3. 证据层（事实底座）

- **LLM Wiki evidence package** —— 文件型证据包，权威事实层
- **PostgreSQL** —— 结构化索引
- **Milvus** —— 可重建的语义索引
- **artifacts** —— 构建产物和脱敏证据

!!! note "核心原则"
    向量库失效可以重建，事实源不丢。Wiki package 是权威事实层，PostgreSQL 是结构化索引，Milvus 是可重建的语义索引。

### 4. 控制面（apps/api）

- 鉴权（JWT / HttpOnly cookie）
- 任务编排
- Agent stream（SSE）
- source access
- Deal OS（一级市场投委会工作流）
- 会议管理
- 记忆服务
- 运行面选择（Host / OpenShell）

### 5. 智能体集群（agents/hermes）

- **二级市场**：analysis / factcheck / tracking / legal / assistant
- **一级市场**：IC chairman / strategist / sector / finance / legal / risk / coordinator

智能体通过 NVIDIA OpenShell 安全运行面执行，所有行动可审计、可回放。

## 数据流

```mermaid
sequenceDiagram
    participant Input as 输入材料
    participant App as 应用中心
    participant Evidence as 证据层
    participant Control as 控制面
    participant Shell as OpenShell
    participant Agent as 智能体集群
    participant Web as Web 工作台

    Input->>App: 官方披露/材料/音频/文档
    App->>Evidence: 解析 + quality gates
    Evidence->>Control: 事实层就绪
    Control->>Shell: 运行面选择 + 公司上下文
    Shell->>Agent: 沙箱代际 + 资源租约
    Agent->>Evidence: 读取证据 + 写入结论
    Agent->>Web: SSE 流式输出
    Web->>Control: 用户交互 + 任务编排
```

## 跨层共享

三块产品（二级市场、一级市场、应用中心）共享：

- 同一个事实层（evidence package）
- 同一个权限模型（user_private / project_shared / system_shared）
- 同一个质量门禁（warning/fail package）
- 同一个审计语言（source page/table/line、artifact hash）

二级市场的披露证据、一级市场的尽调材料、会议陈述、智能体判断和最终决策可以在同一套 evidence / source / memory 体系中互相引用。

```mermaid
graph LR
    subgraph Shared["跨层共享"]
        S1[事实层<br/>evidence package]
        S2[权限模型<br/>user_private / project_shared / system_shared]
        S3[质量门禁<br/>warning/fail package]
        S4[审计语言<br/>source page/table/line]
    end

    P1[二级市场] --> S1
    P1 --> S2
    P1 --> S3
    P1 --> S4
    P2[一级市场] --> S1
    P2 --> S2
    P2 --> S3
    P2 --> S4
    P3[应用中心] --> S1
    P3 --> S2
    P3 --> S3
    P3 --> S4

```