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
  [class*="summary"],[class*="overview"],[class*="highlight"],[class*="insight"]{
    background:#ffffff!important;
    color:#0f172a!important;
    border-color:#e2e8f0!important;
    box-shadow:0 10px 28px rgba(15,23,42,.055)!important;
  }
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
`
