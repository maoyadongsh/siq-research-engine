# TOOLS.md - SIQ 投委会风控委员

## 可用工具

### 信息采集
- `web_search` / `tavily_search` / `web_search_exa` — ESG、舆情、供应链、竞争格局
- `web_fetch` / `tavily_extract` / `crawling_exa` — 深度舆情监测、行业风险分析
- `tavily_research` — 综合性研究（适合风险全面分析）

### 企业信息
- `advSearch` / `getBasicInfo` / `getEnterpriseInfo` / `getAllRiskInfo` — 企业全面风险
- `sumLawsuit` — 诉讼统计
- `getExecutionListByName` / `getExecutedpersonListByName` — 被执行与失信
- `getEnterpriseCountInfo` — 企业风险扫描（含环保、行政处罚等）

### 协作
- Deal OS workflow API — 提交 R1 报告、读取 startup receipt、写入审计事件
- `read` / `write` / `edit` — 读写项目共享文件

## 报告输出路径
```
/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/
```
