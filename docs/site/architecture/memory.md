# 拟人化记忆系统

SIQ 的智能体记忆不是简单聊天摘要，而是让研究助手具备"长期共事感"的拟人化记忆系统。通过分层沉淀、衰减召回和证据优先的设计，让助手在长周期投研协作中既能保持连续性，又不会把过时偏好当作既定事实。

## 系统全景

智能体记忆系统由四层存储 + 三种召回路径 + 三类 scope 隔离组成。下方全景图展示记忆的写入、召回、衰减、全量检索四条主路径，以及与证据层之间的优先级关系。

```mermaid
graph TB
    subgraph Inputs["输入源"]
        U1[用户对话<br/>偏好/纠错/项目上下文]
        U2[智能体结论<br/>分析/核查/决议]
        U3[IC 阶段产物<br/>R0-R4 投委会签核]
    end

    subgraph Runtime["运行时层（短期）"]
        R1[Hermes 原生记忆<br/>会话/响应/checkpoint]
        R2[本地临时任务记忆<br/>工作目录/草稿/临时artifacts]
    end

    subgraph LongTerm["长期记忆层（可审计）"]
        L1[(PostgreSQL<br/>权威长期记忆<br/>偏好/纠错/项目结论)]
        L2[(Milvus<br/>语义索引<br/>profile 知识/memory item 向量)]
    end

    subgraph Scopes["scope 隔离"]
        SC1[user_private<br/>用户私有]
        SC2[project_shared<br/>项目共享]
        SC3[system_shared<br/>系统共享]
    end

    subgraph Ev["证据层（决定事实）"]
        EV1[(evidence package)]
        EV2[(原始材料)]
    end

    U1 --> R1
    U2 --> R1
    U3 --> R1
    R1 --> R2
    R1 -->|写入权威| L1
    R2 -->|沉淀产物| L1
    L1 -->|向量化| L2
    L1 -.->|按 scope 归属| Scopes

    R1 -->|默认召回<br/>半衰期 30 天衰减| L2
    R1 -->|按需全量召回<br/>绕过衰减| L1
    L2 -.->|语义召回| R1

    R1 -->|查询事实| EV1
    R1 -->|回溯原始| EV2

    EV1 ==>|优先级最高| R1
    L1 ==>|次优先级| R1
    L2 ==>|辅助召回| R1

    style EV1 fill:#f5f5f5,stroke:#000
    style EV2 fill:#f5f5f5,stroke:#000
    style L1 fill:#fff,stroke:#000
    style L2 fill:#fff,stroke:#000
```

## 四层架构

| 记忆层 | 保存内容 | 作用 |
| --- | --- | --- |
| Hermes 原生记忆 | 会话、响应、profile runtime、checkpoint、短期上下文 | 保持同一 profile 的对话连续性和工具执行状态 |
| 本地临时任务记忆 | 当前任务工作目录、报告草稿、临时 evidence、intermediate artifacts | 支撑长任务分阶段推理、重试和恢复 |
| PostgreSQL 权威长期记忆 | 用户明确偏好、纠错、项目结论、IC 阶段产物、权限、来源和有效期 | 作为可审计、可删除、可授权的长期记忆账本 |
| Milvus 语义索引 | profile 知识 chunk、动态 memory item 向量、scope metadata | 用于语义召回和泛化检索，可从权威层重建 |

```mermaid
graph TB
    subgraph Runtime["运行时（短期）"]
        L1[Hermes 原生记忆<br/>会话/响应/checkpoint]
        L2[本地临时任务记忆<br/>工作目录/草稿/临时artifacts]
    end

    subgraph LongTerm["长期（可审计）"]
        L3[(PostgreSQL 权威长期记忆<br/>偏好/纠错/项目结论/IC产物)]
        L4[(Milvus 语义索引<br/>profile 知识/memory item 向量)]
    end

    User[用户对话] --> L1
    L1 --> L2
    L2 -->|写入权威| L3
    L3 -->|向量化| L4
    L4 -->|语义召回| L1
    L3 -->|按需全量召回| L1

    L3 -.->|可重建| L4
    L4 -.->|失效后| L3
```

## 记忆生命周期

每条记忆项从写入到召回遵循统一的生命周期。半衰期作用于召回权重而非物理删除，超过衰减窗口仍可通过全量检索访问。

```mermaid
stateDiagram-v2
    [*] --> 写入: 用户偏好/纠错/项目结论
    写入 --> PostgreSQL: 权威层 + scope/有效期/来源
    PostgreSQL --> Milvus: 向量化 + scope metadata
    Milvus --> 召回权重: 半衰期 30 天衰减
    召回权重 --> 默认召回: 近期优先
    召回权重 --> 全量召回: 绕过衰减
    默认召回 --> 智能体判断
    全量召回 --> 智能体判断
    智能体判断 --> 证据校验: 当前 evidence 优先
    证据校验 --> [*]: 结论沉淀回权威层

    note right of PostgreSQL
        scope 标签:
        user_private
        project_shared
        system_shared
    end note

    note right of 召回权重
        衰减作用于权重
        不物理删除
    end note
```

## 三个 scope 隔离

记忆按可见性隔离成三个 scope，跨 scope 访问需要显式授权。

```mermaid
graph TB
    subgraph UP["user_private（用户私有）"]
        UP1[个人偏好]
        UP2[个人纠错]
        UP3[个人协作履历]
    end

    subgraph PS["project_shared（项目共享）"]
        PS1[项目结论]
        PS2[项目上下文]
        PS3[协作约定]
    end

    subgraph SS["system_shared（系统共享）"]
        SS1[系统级知识]
        SS2[模板]
        SS3[规范]
    end

    U[用户 A] -->|仅自己可见| UP
    U -->|项目内可见| PS
    U -->|跨项目可见| SS

    UP x--x PS
    UP x--x SS
    PS x--x SS

    style UP fill:#fff,stroke:#000
    style PS fill:#f5f5f5,stroke:#000
    style SS fill:#e5e5e5,stroke:#000
```

- **user_private**（用户私有）：仅对当前用户可见的偏好、纠错和协作履历
- **project_shared**（项目共享）：项目范围内共享的结论、上下文和协作约定
- **system_shared**（系统共享）：跨项目和用户共享的系统级知识、模板和规范

## 关键能力

### 1. 拟人化连续性

助手能记住用户偏好、历史纠错、项目上下文和角色协作方式，但不会把记忆当作未经验证的事实。模型可以引用过往经验来提供建议，但在涉及数字、条款和结论时必须回到当前证据链路，避免"我记得就是这样"覆盖真实材料。

### 2. 全量记忆

长期记忆不是只保留最近几轮摘要，而是按用户、项目、profile、agent group 和可见性沉淀完整记忆项。每条记忆项带有来源、写入时间、scope、有效期和权限标签，构成一份可追溯的协作履历，而非一段被压缩过的对话片段。

### 3. 记忆半衰期 30 天

动态记忆默认按时间衰减，近期经验自然优先，旧偏好不会永久污染新任务。半衰期作用于召回权重而非物理删除——超过衰减窗口的记忆项仍可通过显式全量检索访问，但在默认对话中权重下降，避免陈旧偏好对当前判断产生不当影响。

### 4. 按需全量召回

当用户明确要求"全量检索""完整历史""不要遗忘"时，系统绕过半衰期，但仍保留 ACL、scope 和上下文长度保护。全量召回不等于无差别回灌，系统仍会按可见性和上下文预算裁剪，只是不再用时间衰减作为过滤条件。

## 核心原则

!!! note "记忆提供连续性，证据决定事实"
    对财务数字、法律条款、投资判断和投委会结论，当前 evidence package、数据库事实和原始材料始终优先于模型记忆。记忆提供连续性，证据决定事实。

## 记忆与证据的关系

```mermaid
graph LR
    Q[用户问题] --> M{记忆召回}
    M -->|连续性/上下文| C[历史偏好/纠错/项目上下文]
    M -->|半衰期衰减| R[近期经验优先]
    Q --> E{证据查询}
    E --> P[(evidence package)]
    E --> D[(PostgreSQL 事实)]
    E --> S[(原始材料)]
    C & R --> MemMerged[记忆输入]
    P & D & S --> EviMerged[证据输入]
    MemMerged --> Judge[智能体判断]
    EviMerged --> Judge
    Judge --> Output[结论]
```

**记忆提供连续性，证据决定事实。当记忆与证据冲突时，以证据为准。**
