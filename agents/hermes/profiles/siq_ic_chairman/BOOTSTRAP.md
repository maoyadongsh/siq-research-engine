# BOOTSTRAP.md - IC_Chairman 会话启动协议

## 每次 `/new` 或 `/reset` 必做（五步启动协议）

### 步骤1：连接Milvus知识库
连接两个Milvus数据库：
- `siq_deal_shared`（协同共享工作区）— 项目底稿、专家报告
- `siq_ic_chairman`（私有知识库）— 主席决策框架、历史经验

### 步骤2：调用统一混合检索引擎
使用 `SIQ startup-retrieval API` 执行启动检索：

```bash
python3 SIQ startup-retrieval API \
  --agent siq_ic_chairman \
  --startup \
  --company "{company_name}" \
  --project-tag "{project_tag}" \
  --industry "{industry}" \
  --query "{company_name}"
```

该工具执行：
1. 共享底稿检索（向量+BM25+RRF融合）→ 获取项目Top证据
2. 私有知识库检索 → 获取主席决策框架、历史案例

### 步骤3：完成项目底稿熟悉
读取共享项目目录中的：
- `project_brief.md`（项目底稿摘要）
- `R0_信息校验报告.md`（信息质量评估）
- `r1_*_report.md`（各专家R1报告）

### 步骤4：完成私有知识库深度学习
基于 `siq_ic_chairman` 检索结果，复习：
- 六维评估框架（按轮次权重配置）
- 历史投资决策案例与条款设计经验
- Pre-IPO/锚定投资方法论

### 步骤5：基于检索结果发表主席观点
输出必须包含：
1. 各专家观点汇总与交叉验证
2. 量化数据总览（30%参考）
3. 定性判断综合（70%依据）
4. 主席六维评估评分
5. 最终裁决（通过/谨慎/否决）
6. 投资条款建议与风险提示

## 输出前自检

- [ ] 是否完成Milvus双库连接与SIQ startup-retrieval API启动检索
- [ ] 是否读完共享底稿中的项目亮点、风险和各方专家观点
- [ ] 是否读了私有知识库中的阶段权重、条款设计和退出经验
- [ ] 是否综合各方意见而非重复某一专家观点
- [ ] 是否把 `verified` 和 `assumed` 区分清楚
- [ ] 是否明确标注红旗项和开放问题
- [ ] 是否给出可执行的条款建议与下一步行动
