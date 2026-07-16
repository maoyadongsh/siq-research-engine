#!/bin/bash
# finsight_tracking 每日定时运行脚本（规则引擎版）
# 建议通过 cron 设置: 0 8 * * * /home/maoyd/wiki/tracking/scripts/daily_run.sh
#
# 工作规则:
#   1. 工作目录: companies/<stock>-<name>/tracking/
#   2. 脚本位置: tracking/scripts/（固定）
#   3. 报告命名: <stock>-<name>-跟踪报告-<date>.html
#   4. 单报告原则: 只生成合并报告
#   5. 前置检查: 只跟踪 finsight_analysis 已完成分析的公司
#   6. 目录结构: tracking/ 下必须包含 sentiment/metrics/alerts/updates/

WIKI_BASE="/home/maoyd/wiki"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$WIKI_BASE/tracking/_meta/logs"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "[$DATE] 开始每日跟踪任务"
echo "[$DATE] 工作目录规则: companies/<stock>-<name>/tracking/"

# 获取所有已配置跟踪的公司（从 companies/ 目录读取）
COMPANIES=()
if [ -d "$WIKI_BASE/companies" ]; then
    for entry in "$WIKI_BASE/companies"/\d\d\d\d\d\d-*; do
        if [ -d "$entry" ]; then
            basename=$(basename "$entry")
            # 检查是否存在 tracking 目录
            if [ -d "$entry/tracking" ]; then
                COMPANIES+=("$basename")
            fi
        fi
    done
fi

echo "[$DATE] 发现跟踪公司数量: ${#COMPANIES[@]}"

for company_dir in "${COMPANIES[@]}"; do
    # 解析 stock-name 格式
    STOCK=$(echo "$company_dir" | cut -d'-' -f1)
    COMPANY=$(echo "$company_dir" | cut -d'-' -f2-)

    echo ""
    echo "  处理: $STOCK - $COMPANY"

    # 前置检查: 确认 analysis/ 目录存在
    ANALYSIS_DIR="$WIKI_BASE/companies/$company_dir/analysis"
    if [ ! -d "$ANALYSIS_DIR" ] || [ -z "$(ls -A "$ANALYSIS_DIR" 2>/dev/null)" ]; then
        echo "    ⏭️ 跳过: finsight_analysis 尚未完成"
        continue
    fi

    # 确保目录结构
    TRACKING_DIR="$WIKI_BASE/companies/$company_dir/tracking"
    mkdir -p "$TRACKING_DIR"/{sentiment,metrics,alerts,updates}

    # 运行模块2+3+4（跳过模块1和5，除非有预警）
    cd "$SCRIPT_DIR"

    echo "    📰 运行舆情监控..."
    python module2_sentiment_monitor.py --stock "$STOCK" --company "$COMPANY" >> "$LOG_DIR/$DATE-sentiment.log" 2>&1

    echo "    📊 运行指标追踪..."
    python module3_metrics_tracker.py --stock "$STOCK" --company "$COMPANY" >> "$LOG_DIR/$DATE-metrics.log" 2>&1

    echo "    🚨 运行预警触发..."
    python module4_alert_trigger.py --stock "$STOCK" --company "$COMPANY" >> "$LOG_DIR/$DATE-alerts.log" 2>&1

    # 检查是否有 WARNING/CRITICAL 预警，有则运行模块5
    ALERT_DIR="$TRACKING_DIR/alerts"
    if ls "$ALERT_DIR"/$DATE-warning*.md "$ALERT_DIR"/$DATE-critical*.md 2>/dev/null | grep -q .; then
        echo "    ⚠️ 发现预警，运行报告更新..."
        python module5_report_updater.py --stock "$STOCK" --company "$COMPANY" >> "$LOG_DIR/$DATE-updates.log" 2>&1
    fi

    # 清理：确保单报告原则（删除非标准HTML）
    STANDARD_REPORT="${STOCK}-${COMPANY}-跟踪报告-${DATE}.html"
    for html_file in "$TRACKING_DIR"/*.html; do
        if [ -f "$html_file" ]; then
            basename=$(basename "$html_file")
            if [ "$basename" != "$STANDARD_REPORT" ]; then
                echo "    🗑️  清理违规HTML: $basename"
                rm "$html_file"
            fi
        fi
    done
done

echo ""
echo "[$DATE] 每日跟踪任务完成"
echo "[$DATE] 日志目录: $LOG_DIR"
