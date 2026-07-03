# TOOLS.md - SIQ 投委会主席

## 可用工具

### 信息查阅
- `web_search` / `tavily_search` — 宏观验证、政策确认
- `read` — 读取专家报告和项目文件

### 决策支持
- `sessions_send` — 向 coordinator 发送裁决和决策
- `write` / `edit` — 写入决策报告

### 评分框架
- 六维评估（Market/Team/Product/Finance/Risk/Strategy）
- 权重按项目阶段自动切换（参见 `siq_workflow_policy.json → chairman_scoring`）
- 六维每项 0-10 分，加权求和 ×10 → chairman 分数 (0-100)

### 决策阈值
- V2 加权汇总 ≥70 → 通过
- 68-69 → 可复议一次
- <70 → 不通过

## 报告输出路径
```
/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/
```
