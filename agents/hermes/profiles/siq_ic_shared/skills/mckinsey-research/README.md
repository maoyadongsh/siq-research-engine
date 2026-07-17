# McKinsey Research（战略研究 Skill）

`mckinsey-research` 是 SIQ Hermes 一级市场投委会共享 skill，用来把战略咨询常见分析框架转化为可复用的结构化研究流程。它适合支持市场空间、竞争格局、增长路径、定价、GTM、风险场景和执行路线图分析，但必须受 `siq_ic_shared` 的 evidence、report 与 workflow contract 约束。

## SIQ 定位

- 所属产品：一级市场投研决策智能体集群。
- 主要服务对象：行业专家、战略专家、主席综合角色和 Deal OS 编排器。
- 证据边界：输出必须绑定项目 Evidence、市场数据、明确假设和不确定性，不作为无约束的战略生成器。
- 与记忆系统关系：可用 Hermes 记忆、项目全量记忆、本地临时记忆和 Milvus 背景知识提供连续上下文；涉及标的事实时仍必须回到项目 Evidence。

## 能力范围

该 skill 提供一组咨询级分析模块：

1. 市场规模与 TAM：结合 top-down 与 bottom-up 方法估算目标市场。
2. 竞争格局：按市场份额、收入、融资、产品定位和渠道能力比较关键竞争者。
3. 客户画像：构建买方 persona、采购动机、预算约束和决策链。
4. 行业趋势：梳理宏观、微观和技术趋势，并给出时间线。
5. SWOT 与 Five Forces：把内部优势劣势与外部行业结构交叉分析。
6. 定价策略：竞品价格审计、价值定价和分层套餐建议。
7. Go-To-Market：发布节奏、渠道策略、销售动作和 KPI 框架。
8. 客户旅程：从认知、评估、购买、交付到复购与推荐的全生命周期 mapping。
9. 财务建模：单位经济、三年预测、盈亏平衡和关键敏感性。
10. 风险评估：对市场、产品、组织、财务、监管和执行风险做分类场景分析。
11. 市场进入：扩张路径、12 个月 roadmap 和关键里程碑。
12. 高管综合建议：形成投委会可读的结构化战略建议。

## 在 SIQ 中如何工作

1. 上游 profile 或 API 编排器提供 deal 背景、项目 Evidence、市场材料和分析目标。
2. skill 根据任务类型选择对应咨询模块，生成假设、问题清单和分析框架。
3. 输出以结构化 memo、评分依据、风险事项和后续证据需求的形式返回。
4. 投委会 R1/R2/R3/R4 流程继续执行交叉验证、红蓝对抗和主席裁决。

## 项目内使用

本目录已经作为 `siq_ic_shared` 的共享 skill 纳入仓库。不要把它当作独立聊天机器人直接接管项目判断；应由 Hermes profile 在受控 workflow 中调用。

自然语言触发示例：

```text
为当前 deal 做市场规模和竞争格局分析
补充标的公司的 GTM 计划和 KPI 框架
对管理层战略假设做红队压力测试
生成进入新能源商用车后市场的 12 个月路线图
```

## 外部安装来源

原始通用 skill 可通过以下方式安装；在本仓库内通常不需要重复执行：

```bash
npx skills add Abdullah4AI/mckinsey-research
clawhub install mckinsey-research
```

手工同步时，可参考：

```bash
git clone https://github.com/Abdullah4AI/mckinsey-research.git
cp -r mckinsey-research /home/maoyd/siq-research-engine/agents/hermes/profiles/siq_ic_shared/skills/
```

## 目录结构

```text
mckinsey-research/
├── SKILL.md              # 主 skill 指令
├── references/
│   └── prompts.md        # 十二类战略分析 prompt 与变量
└── README.md             # 本说明文件
```

## 使用边界

- 不得把咨询框架输出直接当成项目事实。
- 不得在缺少市场数据、项目 Evidence 或明确假设时给出确定性投资结论。
- 对关键市场规模、竞争份额、价格和财务模型必须报告来源、口径和不确定性。
- 若与财务、法务、风控 profile 结论冲突，应进入 R1.5/R3 的分歧裁决流程。

## 作者与许可证

原始 skill 作者：[Abdullah4AI](https://x.com/Abdullah4AI)。许可证为 MIT。
