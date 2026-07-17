export const REPORT_VIEWER_THEME = `
  :root,html,body{
    color-scheme:light!important;
    --bg-primary:#f6f8fb!important;
    --bg-secondary:#ffffff!important;
    --bg-card:#ffffff!important;
    --bg-card-hover:#f8fbff!important;
    --text-primary:#0f172a!important;
    --text-secondary:#334155!important;
    --text-muted:#64748b!important;
    --border-color:#e2e8f0!important;
    --shadow:0 10px 28px rgba(15,23,42,.06)!important;
    --shadow-lg:0 18px 46px rgba(15,23,42,.08)!important;
  }
  html,body{
    margin:0!important;
    min-width:0!important;
    background:#f6f8fb!important;
    color:#0f172a!important;
    font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif!important;
    line-height:1.65!important;
    -webkit-font-smoothing:antialiased!important;
    text-rendering:optimizeLegibility!important;
  }
  body{
    box-sizing:border-box!important;
    padding:26px!important;
  }
  body::before,body::after{display:none!important}
  *{box-sizing:border-box!important}
  .container{max-width:1400px!important}
  .status-strip,.status-row,.summary,.overview,.executive-summary,.abstract,.key-points,.metrics,.kpi,.kpi-card,.metric-card,.stat-card,
  .verdict-banner,.finding,.finding-card,.check-card,.result-card,.audit-card,.status-card,[class*="summary"],[class*="overview"],[class*="metric"],[class*="stat"]{
    font-size:16px!important;
  }
  .status-strip span,.status-row span,.summary p,.overview p,.executive-summary p,.abstract p,.key-points p,.key-points li,
  .metrics p,.metrics span,.kpi p,.kpi span,.kpi-card p,.kpi-card span,.metric-card p,.metric-card span,.stat-card p,.stat-card span,
  .verdict-banner p,.verdict-banner span,.finding p,.finding li,.finding-card p,.finding-card li,.check-card p,.result-card p,.audit-card p,.status-card p{
    font-size:16px!important;
    line-height:1.75!important;
  }
  .kpi-label,.metric-label,.stat-label,[class*="label"]{
    font-size:15px!important;
  }
  .report-header{
    background:linear-gradient(135deg,#ffffff 0%,#f6faff 52%,#eef6ff 100%)!important;
    border-bottom:1px solid #dbeafe!important;
    color:#0f172a!important;
    box-shadow:0 18px 46px rgba(15,23,42,.07)!important;
  }
  .report-title{
    background:none!important;
    color:#0f172a!important;
    -webkit-text-fill-color:#0f172a!important;
  }
  .report-subtitle,.meta-item,.kpi-label,.content-list li,.svg-label,.svg-muted,.income-bridge-subtitle,.income-bridge-unit,.income-bridge-footnotes{
    color:#475569!important;
  }
  .section,.kpi-card,.chart-container:not(.income-bridge-panel),.risk-card,.table-wrapper,.evidence-panel,
  .summary,.overview,.executive-summary,.abstract,.key-points,.metrics,.kpi,.risk-summary,
  .card,.panel,.verdict-banner,.finding,.finding-card,.check-card,.result-card,.audit-card,.status-card,
  [class*="summary"],[class*="overview"],[class*="highlight"],[class*="insight"]{
    background:#ffffff!important;
    color:#0f172a!important;
    border-color:#e2e8f0!important;
    box-shadow:0 10px 28px rgba(15,23,42,.055)!important;
  }
  .header:not(.report-header){
    border-bottom-color:#e2e8f0!important;
    color:#0f172a!important;
  }
  .card p,.card li,.card .card-desc,.card .card-note,.verdict-banner p,.verdict-banner .stat-label,
  .finding p,.check-card p,.result-card p,.audit-card p{
    color:#475569!important;
  }
  .card-title,.verdict-text h2,.verdict-banner h2,.verdict-banner h3{
    color:#0f172a!important;
  }
  .header:not(.report-header),.hero:not(.chart-area),.cover,.opinion-header,.legal-header,.report-cover,[class*="hero"]:not(.chart-area),[class*="cover"]{
    background:linear-gradient(135deg,#ffffff 0%,#f8fbff 54%,#eef6ff 100%)!important;
    color:#0f172a!important;
    border-color:#dbeafe!important;
    box-shadow:0 12px 34px rgba(15,23,42,.06)!important;
  }
  .header:not(.report-header) h1,.header:not(.report-header) h2,.header:not(.report-header) h3,
  .hero:not(.chart-area) h1,.hero:not(.chart-area) h2,.cover h1,.cover h2{
    color:#0f172a!important;
    -webkit-text-fill-color:#0f172a!important;
  }
  .header:not(.report-header) p,.header:not(.report-header) span,.header:not(.report-header) .meta,
  .hero:not(.chart-area) p,.hero:not(.chart-area) span,.cover p,.cover span{
    color:#475569!important;
    opacity:1!important;
  }
  .verdict-banner{
    align-items:center!important;
    gap:18px!important;
    padding:20px 22px!important;
    overflow:visible!important;
  }
  .verdict-badge{
    width:58px!important;
    height:58px!important;
    min-width:58px!important;
    flex:0 0 58px!important;
    font-size:17px!important;
    line-height:1!important;
    border-width:2px!important;
    letter-spacing:0!important;
    transform:none!important;
  }
  .verdict-badge::before,.verdict-badge::after{display:none!important}
  .verdict-text{min-width:0!important;flex:1 1 auto!important}
  .verdict-stats{flex:0 0 auto!important}
  .card-icon,.finding-icon,.section-icon,.meta-icon,.source-icon,.risk-icon,.badge-icon,.status-icon,
  .header:not(.report-header) .badge,.icon:not(svg):not(path):not(circle):not(rect):not(line):not(polyline):not(polygon),
  [class*="icon"]:not(svg):not(path):not(circle):not(rect):not(line):not(polyline):not(polygon){
    background:#eff6ff!important;
    color:#1d4ed8!important;
    border:1px solid #bfdbfe!important;
    box-shadow:none!important;
  }
  .card-icon svg,.finding-icon svg,.section-icon svg,.meta-icon svg,.source-icon svg,.risk-icon svg,.badge-icon svg,.status-icon svg,
  .icon svg,[class*="icon"] svg{
    color:inherit!important;
    stroke:currentColor!important;
  }
  .card-icon.green,.finding-icon.green,.icon.green,[class*="icon"].green{background:#dcfce7!important;color:#15803d!important;border-color:#bbf7d0!important}
  .card-icon.yellow,.finding-icon.yellow,.icon.yellow,.card-icon.warning,.finding-icon.warning,.icon.warning,[class*="icon"].yellow,[class*="icon"].warning{background:#fef3c7!important;color:#b45309!important;border-color:#fde68a!important}
  .card-icon.red,.finding-icon.red,.icon.red,.card-icon.danger,.finding-icon.danger,.icon.danger,[class*="icon"].red,[class*="icon"].danger{background:#fee2e2!important;color:#b91c1c!important;border-color:#fecaca!important}
  .badge,.tag,.chip,.pill,[class*="badge"],[class*="tag"],[class*="chip"],[class*="pill"]{
    background:#eff6ff!important;
    color:#1d4ed8!important;
    border-color:#bfdbfe!important;
    box-shadow:none!important;
  }
  .card-status.pass,.status.pass,.badge.pass,.tag.safe,.tag.pass,.chip.pass,.pill.pass,.verified,.status.verified,.badge.verified,.tag.verified,[class*="verified"]{background:#dcfce7!important;color:#166534!important;border-color:#bbf7d0!important}
  .card-status.warn,.status.warn,.status.warning,.badge.warning,.tag.warn,.tag.warning,.chip.warning,.pill.warning{background:#fef3c7!important;color:#92400e!important;border-color:#fde68a!important}
  .card-status.fail,.status.fail,.status.error,.badge.risk,.tag.risk,.tag.fail,.chip.fail,.pill.fail{background:#fee2e2!important;color:#991b1b!important;border-color:#fecaca!important}
  @media(max-width:760px){
    .verdict-banner{flex-wrap:wrap!important}
    .verdict-stats{width:100%!important;justify-content:flex-start!important}
  }
  .verdict-badge.approve,.card-icon.green{background:#dcfce7!important;border-color:#22c55e!important;color:#15803d!important}
  .verdict-badge.request_changes,.card-icon.yellow{background:#fef3c7!important;border-color:#f59e0b!important;color:#b45309!important}
  .verdict-badge.block,.card-icon.red{background:#fee2e2!important;border-color:#ef4444!important;color:#b91c1c!important}
  .stat.critical .stat-num{color:#dc2626!important}
  .stat.warning .stat-num{color:#d97706!important}
  .stat.suggestion .stat-num{color:#0284c7!important}
  .card-status.pass,.status.pass{background:#dcfce7!important;color:#166534!important}
  .card-status.warn,.status.warn,.card-status.warning,.status.warning{background:#fef3c7!important;color:#92400e!important}
  .card-status.fail,.status.fail,.card-status.error,.status.error{background:#fee2e2!important;color:#991b1b!important}
  .chart-container:not(.income-bridge-panel){
    background:#f8fafc!important;
  }
  .section-header{
    background:#ffffff!important;
    border-bottom-color:#e2e8f0!important;
  }
  h1,h2,h3,h4,h5,h6,.section-title,.chart-title,.kpi-value,.svg-value,strong,b,th{
    color:#0f172a!important;
    letter-spacing:0!important;
  }
  p,li,td,label{
    color:#1f2937!important;
  }
  table{
    background:#ffffff!important;
    border-color:#e2e8f0!important;
    box-shadow:0 10px 30px rgba(15,23,42,.05)!important;
  }
  th{
    background:#f1f5f9!important;
    color:#1e293b!important;
  }
  td{
    background:#ffffff!important;
    color:#1f2937!important;
    border-color:#e2e8f0!important;
  }
  tr:nth-child(even) td{background:#fbfdff!important}
  pre,code,kbd,samp{
    background:#f8fafc!important;
    color:#0f172a!important;
    border-color:#e2e8f0!important;
  }
  blockquote{
    border-left-color:#0052ff!important;
    background:#f8fbff!important;
    color:#1f2937!important;
  }
  a{color:#0052ff!important}
  img,svg,canvas{max-width:100%!important}
  .dark,[data-theme="dark"],[class~="dark"]{
    background:#ffffff!important;
    color:#0f172a!important;
  }
  .stock-badge,.evidence-tag{
    background:#eff6ff!important;
    border-color:#bfdbfe!important;
    color:#1d4ed8!important;
  }
  .report-chart-tooltip,.income-bridge-tooltip{
    background:rgba(17,24,39,.96)!important;
    color:#ffffff!important;
    border-color:rgba(255,255,255,.14)!important;
    box-shadow:0 16px 34px -24px rgba(15,23,42,.9)!important;
  }
  .report-chart-tooltip strong,.income-bridge-tooltip strong{color:#ffffff!important}
  .report-chart-tooltip span,.income-bridge-tooltip span{color:#d1d5db!important}

  /* SIQ blue-white reading theme normalizes generated report templates. */
  :root,html,body{
    --bg-primary:#f5f7fb!important;
    --bg-secondary:#ffffff!important;
    --bg-card:#ffffff!important;
    --bg-card-hover:#f7faff!important;
    --text-primary:#0f172a!important;
    --text-secondary:#334155!important;
    --text-muted:#64748b!important;
    --border-color:#d8e1ec!important;
    --shadow:none!important;
    --shadow-lg:none!important;
  }
  html,body{
    background:#f5f7fb!important;
    color:#0f172a!important;
    font-family:Inter,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif!important;
  }
  h1,h2,h3,h4,h5,h6,.report-title,.section-title,.chart-title,.card-title,.verdict-text h2,.verdict-banner h2,.verdict-banner h3{
    color:#0f172a!important;
    font-family:"Noto Serif SC","Songti SC",Georgia,serif!important;
    font-weight:600!important;
  }
  .report-header,.header:not(.report-header),.hero:not(.chart-area),.cover,.opinion-header,.legal-header,.report-cover,[class*="hero"]:not(.chart-area),[class*="cover"]{
    background:#ffffff!important;
    border-color:#d8e1ec!important;
    box-shadow:none!important;
  }
  .section,.kpi-card,.chart-container:not(.income-bridge-panel),.risk-card,.table-wrapper,.evidence-panel,
  .summary,.overview,.executive-summary,.abstract,.key-points,.metrics,.kpi,.risk-summary,
  .card,.panel,.verdict-banner,.finding,.finding-card,.check-card,.result-card,.audit-card,.status-card,
  [class*="summary"],[class*="overview"],[class*="highlight"],[class*="insight"]{
    background:#ffffff!important;
    color:#0f172a!important;
    border-color:#d8e1ec!important;
    border-radius:12px!important;
    box-shadow:none!important;
  }
  .card-icon,.finding-icon,.section-icon,.meta-icon,.source-icon,.risk-icon,.badge-icon,.status-icon,
  .header:not(.report-header) .badge,.icon:not(svg):not(path):not(circle):not(rect):not(line):not(polyline):not(polygon),
  [class*="icon"]:not(svg):not(path):not(circle):not(rect):not(line):not(polyline):not(polygon){
    background:transparent!important;
    color:#005bb5!important;
    border-color:rgba(0,113,227,.35)!important;
  }
  .badge,.tag,.chip,.pill,[class*="badge"],[class*="tag"],[class*="chip"],[class*="pill"]{
    background:#eff6ff!important;
    color:#1d4ed8!important;
    border-color:rgba(0,113,227,.28)!important;
    border-radius:999px!important;
  }
  .section-header,th{
    background:#eff6ff!important;
    border-color:#d8e1ec!important;
  }
  table,td{
    background:#ffffff!important;
    border-color:#d8e1ec!important;
    box-shadow:none!important;
  }
  tr:nth-child(even) td{background:#f7faff!important}
  pre,code,kbd,samp,blockquote{
    background:#f7faff!important;
    border-color:#bfdbfe!important;
  }
  blockquote{border-left-color:#0071e3!important}
  a{color:#005bb5!important}
  .stock-badge,.evidence-tag{
    background:#eff6ff!important;
    border-color:rgba(0,113,227,.28)!important;
    color:#1d4ed8!important;
  }
  .siq-report-document,.siq-report-document body{
    overflow:hidden!important;
  }
  .siq-md-inline-heading{
    color:#0f172a!important;
    font-family:"Noto Serif SC","Songti SC",Georgia,serif!important;
    font-size:1.04em!important;
    font-weight:600!important;
  }
  .status-panel.status-alert,.attention-item{border-left-color:#b42318!important}
  .status-panel.status-watch{border-left-color:#b58a24!important}
  .status-panel.status-steady{border-left-color:#157347!important}
  .status-alert .status-label,.badge.alert,.priority-chip{
    background:#fff5f2!important;color:#b42318!important;border-color:#e8b7b0!important;
  }
  .status-watch .status-label,.badge.warning{
    background:#fffaf0!important;color:#8f6810!important;border-color:#d7bd7c!important;
  }
  .status-steady .status-label{
    background:#f1faf4!important;color:#157347!important;border-color:#afd8bd!important;
  }
  .stat-card.blue b,.stat-card.blue .number{color:#4b5968!important}
  .stat-card.red b,.stat-card.red .number{color:#b42318!important}
  .stat-card.yellow b,.stat-card.yellow .number{color:#8f6810!important}
  .stat-card.green b,.stat-card.green .number{color:#157347!important}
  .stat-card.purple b,.stat-card.purple .number{color:#69556f!important}

  /* Compact, unfilled status marks for generated reports. */
  .verdict-badge,
  .verdict-badge.approve{
    width:64px!important;
    height:64px!important;
    min-width:64px!important;
    flex:0 0 64px!important;
    border:1.5px solid #157347!important;
    background:transparent!important;
    color:#157347!important;
    font-family:"Noto Serif SC","Songti SC",Georgia,serif!important;
    font-size:16px!important;
    font-weight:600!important;
  }
  .card-status.pass,.status.pass,.badge.pass,.tag.safe,.tag.pass,.chip.pass,.pill.pass,
  .verified,.status.verified,.badge.verified,.tag.verified,[class*="verified"]{
    border:0!important;
    border-radius:0!important;
    background:transparent!important;
    color:#157347!important;
    box-shadow:none!important;
    padding:.15rem .2rem!important;
    font-weight:600!important;
  }
`
