export const PDF_CSS = `
.pdf-workbench-main { display: grid; gap: 1rem; min-width: 0; }
.pdf-workbench-main > * { min-width: 0; }
.pdf-stage { border: 1px solid var(--border); background: var(--card); border-radius: 24px; padding: 1rem; box-shadow: 0 8px 24px rgba(15, 23, 42, .06); }
.pdf-pbar-wrap { height: .55rem; overflow: hidden; border-radius: 999px; background: rgba(148, 163, 184, .18); }
.pdf-pbar { height: 100%; border-radius: inherit; background: linear-gradient(90deg, #2563eb, #14b8a6); transition: width .2s ease; }
.pdf-pbar.done { background: linear-gradient(90deg, #16a34a, #14b8a6); }
.pdf-status-badge { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; border: 1px solid rgba(216, 225, 236, .92); border-radius: 999px; background: rgba(255, 255, 255, .78); color: #475569; padding: 4px 10px; font-size: .75rem; font-weight: 750; text-transform: none; letter-spacing: 0; white-space: nowrap; }
.pdf-status-badge.queued, .pdf-status-badge.pending { border-color: rgba(202, 138, 4, .2); background: rgba(202, 138, 4, .085); color: #a16207; }
.pdf-status-badge.uploading, .pdf-status-badge.processing, .pdf-status-badge.submitting { border-color: rgba(0, 113, 227, .18); background: rgba(0, 113, 227, .075); color: #0071e3; }
.pdf-status-badge.uploaded, .pdf-status-badge.submitted, .pdf-status-badge.cancelled { border-color: rgba(216, 225, 236, .92); background: rgba(255, 255, 255, .78); color: #475569; }
.pdf-status-badge.completed { border-color: rgba(22, 163, 74, .18); background: rgba(22, 163, 74, .075); color: #15803d; }
.pdf-status-badge.failed, .pdf-status-badge.error { border-color: rgba(220, 38, 38, .18); background: rgba(220, 38, 38, .075); color: #b91c1c; }
.pdf-log { max-height: 240px; overflow: auto; border: 1px solid var(--border); border-radius: 14px; background: #fbfdff; padding: .75rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .78rem; line-height: 1.6; }
.pdf-mobile-result-gate { display: grid; gap: .75rem; border: 1px solid rgba(37, 99, 235, .18); background: linear-gradient(180deg, #eff6ff, #fff); border-radius: 18px; padding: 1rem; box-shadow: 0 8px 22px rgba(37, 99, 235, .06); }
.pdf-mobile-result-gate h3 { margin: 0; color: var(--text); font-size: 1rem; font-weight: 750; }
.pdf-mobile-result-gate p { margin: 0; color: var(--text-muted); font-size: .88rem; line-height: 1.55; }
.pdf-small-action, .pdf-trace-btn { display: inline-flex; align-items: center; justify-content: center; gap: .35rem; min-height: 38px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); padding: 0 .75rem; font-size: .82rem; font-weight: 700; color: var(--text); text-decoration: none; cursor: pointer; transition: border-color .16s ease, background .16s ease, color .16s ease, box-shadow .16s ease; white-space: nowrap; }
.pdf-small-action:hover, .pdf-trace-btn:hover { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
.pdf-small-action.primary, .pdf-task-action.primary { border-color: #2563eb; background: #2563eb; color: white; }
.pdf-small-action.primary:hover, .pdf-task-action.primary:hover { border-color: #1d4ed8; background: #1d4ed8; color: white; }
.pdf-task-action.danger { border-color: #fecaca; background: #fff1f2; color: #b91c1c; }
.pdf-task-action.danger:hover { background: #ffe4e6; }
.pdf-small-action:disabled, .pdf-icon-btn:disabled, .pdf-task-action:disabled { opacity: .55; cursor: not-allowed; }
.pdf-health-strip { display: inline-flex; align-items: center; gap: .5rem; width: fit-content; border: 1px solid var(--border); border-radius: 16px; background: rgba(255, 255, 255, .78); padding: .45rem .5rem; box-shadow: 0 8px 22px rgba(15, 23, 42, .035); }
.pdf-health-label { display: inline-flex; align-items: center; min-height: 30px; padding: 0 .5rem; color: var(--text-muted); font-size: .78rem; font-weight: 800; white-space: nowrap; }
.pdf-source-choice { border: 1px solid var(--border); border-radius: 18px; background: linear-gradient(180deg, #fff, #fbfdff); padding: 1rem; box-shadow: 0 8px 22px rgba(15, 23, 42, .035); }
.pdf-source-choice-head { display: flex; align-items: flex-end; justify-content: space-between; gap: .875rem; flex-wrap: wrap; margin-bottom: .875rem; }
.pdf-source-choice-head h3 { margin: 0; color: var(--text); font-size: 1rem; }
.pdf-source-choice-head p { margin: .25rem 0 0; color: var(--text-muted); font-size: .88rem; line-height: 1.5; }
.pdf-download-search { display: grid; grid-template-columns: minmax(260px, 1fr) auto auto; align-items: center; gap: .625rem; width: 100%; border: 1px solid #e2e8f0; border-radius: 16px; background: #f8fafc; padding: .625rem; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .74); }
.pdf-download-search label { position: relative; display: block; min-width: 0; }
.pdf-download-search label > svg { position: absolute; left: .75rem; top: 50%; transform: translateY(-50%); color: var(--text-muted); }
.pdf-download-search input { width: 100%; height: 44px; border: 1px solid var(--border); border-radius: 12px; background: #fff; padding: 0 .75rem 0 2.35rem; color: var(--text); font: inherit; font-size: .9rem; outline: none; box-shadow: 0 1px 0 rgba(15, 23, 42, .02); }
.pdf-download-search input:focus { border-color: #2563eb; background: #fff; box-shadow: 0 0 0 3px rgba(37, 99, 235, .1); }
.pdf-download-count { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; border: 1px solid #bfdbfe; border-radius: 999px; background: #eff6ff; color: #1d4ed8; padding: 0 .75rem; font-size: .78rem; font-weight: 800; white-space: nowrap; }
	.pdf-icon-btn { display: inline-flex; align-items: center; justify-content: center; gap: .45rem; min-width: 88px; height: 44px; border: 1px solid var(--border); border-radius: 12px; background: #fff; color: #334155; padding: 0 .75rem; font: inherit; font-size: .84rem; font-weight: 800; cursor: pointer; transition: border-color .15s ease, background .15s ease, color .15s ease, box-shadow .15s ease; white-space: nowrap; }
.pdf-icon-btn svg { position: static; transform: none; color: currentColor; }
.pdf-icon-btn:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }
.pdf-download-list { display: grid; gap: .5rem; max-height: 310px; overflow: auto; scrollbar-gutter: stable; }
.pdf-download-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .75rem; align-items: center; border: 1px solid #e2e8f0; border-radius: 14px; background: #fff; padding: .75rem; transition: border-color .15s, background .15s, box-shadow .15s; }
.pdf-download-item:hover { border-color: #bfdbfe; background: #f8fbff; box-shadow: 0 8px 20px rgba(15, 23, 42, .04); }
.pdf-download-main { display: flex; gap: .625rem; align-items: flex-start; min-width: 0; }
.pdf-download-main svg { flex: 0 0 auto; margin-top: 2px; color: #2563eb; }
.pdf-download-title { font-weight: 700; color: var(--text); word-break: break-word; line-height: 1.45; }
.pdf-download-meta { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .3rem; color: var(--text-muted); font-size: .78rem; line-height: 1.4; }
.pdf-download-actions { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; justify-content: flex-end; }
.pdf-source-separator { display: flex; align-items: center; gap: .75rem; margin: 1rem 0; color: var(--text-muted); font-size: .84rem; font-weight: 700; }
.pdf-source-separator::before, .pdf-source-separator::after { content: ""; height: 1px; background: #e2e8f0; flex: 1; }
	.pdf-drop-zone { border: 1.5px dashed var(--border); border-radius: 18px; padding: 42px 24px; text-align: center; cursor: pointer; transition: border-color .2s ease, background .2s ease, box-shadow .2s ease; background: linear-gradient(180deg, #fff, #fbfdff); box-shadow: inset 0 1px 0 rgba(255, 255, 255, .8); }
.pdf-drop-zone:hover, .pdf-drop-zone.dragover { border-color: #0052ff; background: rgba(0, 82, 255, .04); box-shadow: 0 14px 34px rgba(37, 99, 235, .08); }
.pdf-artifact-row { display: grid; grid-template-columns: minmax(140px, 1.1fr) minmax(0, 1.4fr) auto; gap: .8rem; align-items: center; border-top: 1px solid var(--border); padding: .75rem 0; }
.pdf-artifact-row code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-muted); }
.pdf-artifact-name { display: flex; min-width: 0; flex-direction: column; gap: .1rem; font-weight: 700; }
.pdf-artifact-name small { color: var(--text-muted); font-weight: 500; }
.pdf-artifact-actions { display: flex; flex-wrap: wrap; gap: .4rem; justify-content: flex-end; }
.pdf-artifact-row.missing { opacity: .55; }
.pdf-quality-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: .7rem; }
.pdf-quality-grid > div, .pdf-quality-row { border: 1px solid var(--border); border-radius: 16px; background: rgba(248, 250, 252, .7); padding: .8rem; }
.pdf-quality-grid strong { display: block; font-size: 1.2rem; }
.pdf-quality-grid span, .pdf-quality-row span { color: var(--text-muted); font-size: .82rem; }
.pdf-quality-row { display: flex; justify-content: space-between; gap: 1rem; margin-top: .7rem; }
.pdf-quality-section { margin-top: 1rem; }
.pdf-quality-section-title { margin-bottom: .55rem; font-weight: 800; }
.pdf-chip-row { display: flex; flex-wrap: wrap; gap: .45rem; }
.pdf-chip { border: 1px solid var(--border); border-radius: 999px; background: var(--card); padding: .35rem .65rem; font-size: .8rem; font-weight: 700; }
.pdf-chip-missing { color: #b45309; background: rgba(245, 158, 11, .1); }
.pdf-chip-secondary { background: rgba(20, 184, 166, .08); }
.pdf-chip.trace-chip { cursor: pointer; }
.pdf-chip.trace-chip:hover { border-color: #2563eb; background: #eff6ff; color: #2563eb; }
.pdf-markdown-body { max-height: 520px; overflow: auto; border-radius: 18px; background: #0f172a; padding: 1rem; color: #e2e8f0; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .82rem; line-height: 1.6; }
.pdf-markdown-line { display: grid; grid-template-columns: 3rem minmax(0, 1fr); gap: .75rem; border-radius: 8px; padding: .08rem .35rem; }
.pdf-markdown-line.is-focused { background: rgba(59, 130, 246, .25); }
.pdf-markdown-line-number { color: #94a3b8; text-align: right; user-select: none; }
.pdf-md-preview { background: #fff; color: var(--text); border: 1px solid var(--border); border-radius: 16px; padding: 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .9rem; line-height: 1.7; max-height: 500px; overflow: auto; white-space: normal; word-break: break-word; box-shadow: 0 8px 24px rgba(15, 23, 42, .04); }
.pdf-md-line { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: .75rem; min-height: 22px; border-radius: 4px; }
.pdf-md-line.focus { background: rgba(37, 99, 235, .18); outline: 1px solid rgba(96, 165, 250, .65); }
.pdf-md-line-no { color: #7d8590; text-align: right; user-select: none; font-variant-numeric: tabular-nums; }
.pdf-md-line-text { white-space: pre-wrap; }

/* Markdown actions */
.pdf-md-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 1rem; }
.pdf-md-actions { display: flex; align-items: flex-end; justify-content: flex-end; gap: .75rem; flex-wrap: wrap; }
.pdf-md-action-group { display: grid; gap: .35rem; }
.pdf-md-action-label { color: var(--text-muted); font-size: .72rem; font-weight: 700; }
.pdf-md-action-row { display: flex; align-items: center; gap: .35rem; border: 1px solid var(--border); border-radius: 12px; background: var(--card); padding: .25rem; }
.pdf-md-heading { display: grid; gap: .25rem; min-width: 0; }
.pdf-md-heading h3 { margin: 0; color: var(--text); font-size: 1rem; font-weight: 750; }
.pdf-md-heading p { margin: 0; color: var(--text-muted); font-size: .82rem; line-height: 1.45; }
.pdf-md-action { display: inline-flex; align-items: center; justify-content: center; gap: .35rem; min-height: 40px; border: 1px solid transparent; border-radius: 10px; padding: 0 .7rem; font-size: .82rem; font-weight: 700; color: var(--text); text-decoration: none; white-space: nowrap; }
.pdf-md-action:hover { border-color: #bfdbfe; background: #fff; color: #1d4ed8; box-shadow: 0 8px 18px rgba(37, 99, 235, .08); }
.pdf-md-action span { display: inline-flex; align-items: center; gap: .35rem; min-width: 0; line-height: 1.1; }
.pdf-md-action b { font-size: .86rem; font-weight: 800; white-space: nowrap; }
.pdf-md-action small { display: inline-flex; align-items: center; border-left: 1px solid var(--border); padding-left: .35rem; font-size: .72rem; font-weight: 750; color: var(--text-muted); white-space: nowrap; }
.pdf-md-action.primary, .pdf-md-action-primary { border-color: #2563eb; background: #2563eb; color: white; box-shadow: 0 8px 18px rgba(37, 99, 235, .18); }
.pdf-md-action.primary small, .pdf-md-action-primary small { border-left-color: rgba(255, 255, 255, .28); color: rgba(255, 255, 255, .88); }
.pdf-md-action.primary:hover, .pdf-md-action-primary:hover { border-color: #1d4ed8; background: #1d4ed8; color: white; box-shadow: 0 10px 22px rgba(37, 99, 235, .24); }

/* Pipeline */
.pdf-pipeline-note { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .75rem; align-items: flex-start; border: 1px solid rgba(37, 99, 235, .18); background: rgba(37, 99, 235, .06); border-radius: 16px; padding: .85rem 1rem; font-size: .84rem; line-height: 1.55; color: var(--text); }
.pdf-pipeline-note code { background: rgba(255,255,255,.55); padding: .1rem .3rem; border-radius: 6px; font-size: .78rem; }
.pdf-preflight-list { display: grid; gap: .5rem; }
.pdf-preflight-item { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .6rem; align-items: start; border: 1px solid var(--border); border-radius: 12px; background: var(--card); padding: .6rem .7rem; font-size: .82rem; line-height: 1.45; }
.pdf-preflight-dot { width: 8px; height: 8px; border-radius: 999px; background: #16a34a; margin-top: 6px; }
.pdf-preflight-item.warn .pdf-preflight-dot { background: #ca8a04; }
.pdf-preflight-item.error .pdf-preflight-dot { background: #dc2626; }
.pdf-preflight-title { font-weight: 700; color: var(--text); }
.pdf-preflight-message { color: var(--text-muted); word-break: break-word; }

/* Source workbench */
.pdf-source-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; }
@media (min-width: 1100px) { .pdf-source-grid { grid-template-columns: minmax(0, 1fr) minmax(360px, .9fr); } }
.pdf-mobile-review-tabs { display: none; margin-bottom: .85rem; border: 1px solid var(--border); border-radius: 12px; background: #f8fafc; padding: .3rem; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .85); }
.pdf-source-pane { min-width: 0; border: 0; border-radius: 0; background: #fff; padding: 0; overflow: hidden; }
.pdf-source-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: .7rem; margin-bottom: .75rem; }
.pdf-source-summary > div { border: 1px solid var(--border); border-radius: 12px; background: rgba(248, 250, 252, .7); padding: .7rem .8rem; }
.pdf-source-summary strong { display: block; font-size: 1.05rem; color: #2563eb; }
.pdf-source-summary span { display: block; color: var(--text-muted); font-size: .78rem; margin-top: .15rem; }
.pdf-source-meta { display: flex; justify-content: space-between; gap: .75rem; border-top: 1px solid var(--border); padding: .5rem 0; font-size: .88rem; }
.pdf-source-meta span { color: var(--text-muted); flex-shrink: 0; }
.pdf-source-meta b { text-align: right; word-break: break-word; }
.pdf-source-block { border-top: 1px solid var(--border); padding-top: 1rem; margin-top: 1rem; }
.pdf-source-block h4 { margin: 0 0 .6rem; font-size: .95rem; font-weight: 700; }
.pdf-workbench { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0; align-items: stretch; overflow: hidden; border: 1px solid var(--border); border-radius: 16px; background: #fff; box-shadow: 0 8px 24px rgba(15, 23, 42, .045); }
.pdf-workbench > .pdf-source-pane + .pdf-source-pane { border-left: 1px solid var(--border); }
@media (max-width: 1200px) { .pdf-workbench { grid-template-columns: 1fr; } }
.pdf-mobile-hidden { display: block; }
.pdf-source-pane { min-width: 0; border: 0; border-radius: 0; background: #fff; overflow: hidden; }
.pdf-source-pane-head { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; min-height: 62px; border-bottom: 1px solid var(--border); background: #fff; padding: .85rem 1rem; }
.pdf-source-pane-head h4 { margin: 0; color: var(--text); font-size: .96rem; font-weight: 800; line-height: 1.2; }
.pdf-source-pane-head p { margin: .25rem 0 0; color: var(--text-muted); font-size: .78rem; line-height: 1.45; }
.pdf-reading-topline { display: flex; align-items: center; justify-content: space-between; gap: .6rem; flex-wrap: wrap; }
.pdf-reading-mode-switch { display: inline-flex; align-items: center; gap: .25rem; flex: 0 0 auto; border: 1px solid var(--border); border-radius: 10px; background: #f8fafc; padding: .25rem; }
.pdf-reading-mode-btn { min-height: 30px; border: 1px solid transparent; background: transparent; color: var(--text-muted); border-radius: 8px; padding: 0 .65rem; font-size: .76rem; font-weight: 800; cursor: pointer; white-space: nowrap; transition: border-color .15s ease, background .15s ease, color .15s ease, box-shadow .15s ease; }
.pdf-reading-mode-btn.active { border-color: #bfdbfe; background: #fff; color: #1d4ed8; box-shadow: 0 4px 12px rgba(15, 23, 42, .06); }
.pdf-reading-mode-btn:hover { color: #1d4ed8; }
.pdf-pdf-page-stack { width: 100%; max-width: 100%; min-width: 0; height: min(720px, calc(100dvh - 235px)); max-height: 720px; overflow: auto; overscroll-behavior: contain; background: #f5f6f8; padding: 0 1.6rem 1.25rem; }
.pdf-pdf-page-stack-item { display: grid; gap: .55rem; margin: 1rem auto 1.25rem; width: 860px; max-width: 100%; }
.pdf-pdf-page-stack[data-zoom="50"] .pdf-pdf-page-stack-item { width: 430px; max-width: 100%; }
.pdf-pdf-page-stack[data-zoom="100"] .pdf-pdf-page-stack-item { width: 860px; max-width: 100%; }
.pdf-pdf-page-stack[data-zoom="150"] .pdf-pdf-page-stack-item { width: 1290px; max-width: none; }
.pdf-pdf-page-card { display: grid; gap: .55rem; }
.pdf-pdf-page-title { display: flex; align-items: center; justify-content: space-between; gap: .75rem; color: #111827; font-size: .78rem; font-weight: 850; }
.pdf-pdf-page-canvas { position: relative; overflow: hidden; border: 1px solid #e5e7eb; border-radius: 2px; background: #fff; box-shadow: 0 1px 2px rgba(15, 23, 42, .06); }
.pdf-pdf-page-image { display: block; width: 100%; height: auto; user-select: none; }
.pdf-page-toolbar-actions { display: flex; align-items: center; justify-content: flex-end; gap: .5rem; flex-wrap: wrap; }
.pdf-page-nav { display: inline-flex; align-items: center; gap: .35rem; }
.pdf-zoom-controls { display: inline-flex; align-items: center; gap: .35rem; flex-wrap: nowrap; }
.pdf-zoom-btn { display: inline-flex; align-items: center; justify-content: center; min-height: 32px; min-width: 64px; border: 1px solid var(--border); border-radius: 10px; background: #fff; color: var(--text-muted); padding: 0 .6rem; font-size: .78rem; font-weight: 800; cursor: pointer; transition: background .15s ease, border-color .15s ease, color .15s ease, box-shadow .15s ease; white-space: nowrap; }
.pdf-zoom-btn:hover { border-color: #bfdbfe; background: #eff6ff; color: #1d4ed8; }
.pdf-zoom-btn.active { border-color: #2563eb; background: #2563eb; color: #fff; box-shadow: 0 6px 14px rgba(37, 99, 235, .16); }
.pdf-page-stage[data-zoom="50"] { width: 50%; min-width: 430px; }
.pdf-page-stage[data-zoom="100"] { width: 100%; min-width: 860px; }
.pdf-page-stage[data-zoom="150"] { width: 150%; min-width: 1290px; }
.pdf-reading-render { display: grid; gap: .55rem; max-width: 900px; margin: 0 auto; }
.pdf-table-wrap { width: 100%; max-width: 100%; min-width: 0; max-height: 620px; overflow-x: auto; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--border); border-radius: 12px; background: var(--card); scrollbar-gutter: stable both-edges; scrollbar-width: auto; scrollbar-color: #2563eb #e2e8f0; }
.pdf-source-pane > .pdf-table-wrap { height: min(690px, calc(100dvh - 235px)); max-height: 690px; border: 0; border-radius: 0; background: #fff; }
.pdf-table-wrap table { border-collapse: collapse; table-layout: auto; width: max-content; min-width: max(100%, 1080px); font-size: .84rem; line-height: 1.45; }
.pdf-table-wrap th, .pdf-table-wrap td { min-width: 96px; border: 1px solid var(--border); padding: .45rem .55rem; vertical-align: top; background: var(--card); }
.pdf-table-wrap tr:first-child td, .pdf-table-wrap th { background: rgba(241, 245, 249, .8); font-weight: 700; position: sticky; top: 0; z-index: 1; }
.pdf-table-wrap td:first-child, .pdf-table-wrap th:first-child { position: sticky; left: 0; z-index: 2; background: rgba(248, 250, 252, .9); font-weight: 600; min-width: 160px; }
.pdf-table-wrap tr:first-child td:first-child, .pdf-table-wrap th:first-child { z-index: 3; }
.pdf-table-wrap::-webkit-scrollbar { width: 14px; height: 14px; }
.pdf-table-wrap::-webkit-scrollbar-track { background: #e2e8f0; border-radius: 999px; box-shadow: inset 0 0 0 1px #cbd5e1; }
.pdf-table-wrap::-webkit-scrollbar-thumb { border: 3px solid #e2e8f0; border-radius: 999px; background: linear-gradient(90deg, #2563eb, #60a5fa); }
.pdf-table-wrap::-webkit-scrollbar-thumb:hover { background: linear-gradient(90deg, #1d4ed8, #3b82f6); }
.pdf-table-wrap::-webkit-scrollbar-corner { background: #e2e8f0; }
.pdf-table-x-scrollbar { display: flex; align-items: center; width: 100%; height: 34px; margin: 0 0 .5rem; border: 1px solid #bfdbfe; border-radius: 999px; background: #eff6ff; padding: 0 .625rem; box-shadow: inset 0 0 0 1px rgba(191, 219, 254, .7), 0 6px 14px rgba(37, 99, 235, .08); cursor: pointer; touch-action: none; }
.pdf-table-x-scrollbar.is-hidden { display: none; }
.pdf-table-x-scrollbar-track { position: relative; width: 100%; height: 12px; border-radius: 999px; background: #dbeafe; box-shadow: inset 0 0 0 1px #bfdbfe; }
.pdf-table-x-scrollbar-thumb { position: absolute; left: 0; top: 50%; height: 22px; min-width: 54px; transform: translateY(-50%); border-radius: 999px; background: linear-gradient(90deg, #2563eb, #60a5fa); box-shadow: 0 4px 10px rgba(37, 99, 235, .28); cursor: grab; }
.pdf-table-x-scrollbar-thumb:hover { background: linear-gradient(90deg, #1d4ed8, #3b82f6); }
.pdf-table-x-scrollbar-thumb.is-dragging { cursor: grabbing; }
.pdf-table-x-dragging, .pdf-table-x-dragging * { cursor: grabbing !important; user-select: none !important; }
.pdf-editable th[contenteditable="true"], .pdf-editable td[contenteditable="true"] { cursor: text; }
.pdf-editable th.selected-cell, .pdf-editable td.selected-cell { outline: 2px solid #f97316; outline-offset: -2px; background: #ffedd5 !important; }
.pdf-editable th[contenteditable="true"]:focus, .pdf-editable td[contenteditable="true"]:focus { outline: 2px solid #2563eb; outline-offset: -2px; background: #eff6ff; }
.pdf-overlay-layer { position: absolute; inset: 0; pointer-events: none; }
.pdf-bbox { position: absolute; z-index: 2; border: 1px solid rgba(37, 99, 235, .78); border-radius: 0; background: rgba(37, 99, 235, .045); box-shadow: none; pointer-events: auto; color: #1d4ed8; cursor: pointer; transition: background .15s ease, border-color .15s ease, box-shadow .15s ease, opacity .15s ease; }
.pdf-bbox span { position: absolute; left: -1px; top: -1px; transform: translateY(-100%); max-width: 5.5rem; overflow: hidden; text-overflow: ellipsis; border-radius: 2px 2px 0 0; background: #2563eb; padding: .08rem .32rem; color: #fff; font-size: .62rem; font-weight: 850; line-height: 1.15; white-space: nowrap; opacity: 1; pointer-events: none; transition: opacity .15s ease; }
.pdf-bbox:not(:hover):not(:focus-visible):not(.pdf-bbox-selected) span { opacity: .88; }
.pdf-bbox-table { border-color: #16a34a; background: rgba(34, 197, 94, .08); }
.pdf-bbox-table span { background: #16a34a; }
.pdf-bbox-block { border-color: rgba(37, 99, 235, .86); background: rgba(37, 99, 235, .055); }
.pdf-bbox-block span { background: #2563eb; }
.pdf-bbox-selected { z-index: 4; border-width: 2px; border-color: #dc2626; background: rgba(220, 38, 38, .12); box-shadow: 0 0 0 3px rgba(220, 38, 38, .16); }
.pdf-bbox-selected span { background: #dc2626; }
.pdf-bbox-text { z-index: 3; border-width: 2px; border-color: #ea580c; background: rgba(249, 115, 22, .12); box-shadow: 0 0 0 3px rgba(249, 115, 22, .12); }
.pdf-bbox-text span { background: #ea580c; }
.pdf-merge-stem { position: absolute; z-index: 3; width: 0; min-height: 24px; pointer-events: auto; border: 0; border-left: 2px dashed #55b938; background: transparent; color: #fff; cursor: pointer; }
.pdf-merge-stem span { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); border-radius: 5px; background: #55b938; padding: .12rem .36rem; font-size: .68rem; font-weight: 900; line-height: 1.15; box-shadow: 0 4px 10px rgba(22, 163, 74, .28); white-space: nowrap; }
.pdf-merge-stem.is-from span { top: 14%; }
.pdf-merge-stem.is-to span { top: 42%; }
.pdf-merge-stem.is-candidate { border-left-color: #65a30d; }
.pdf-merge-stem.is-candidate span { background: #65a30d; }
.pdf-page-merge-bridge { position: relative; display: flex; align-items: center; justify-content: center; min-height: 54px; border: 0; background: transparent; color: #fff; cursor: pointer; }
.pdf-page-merge-bridge::before { content: ""; position: absolute; inset: 50% 12% auto 12%; border-top: 2px dashed #55b938; transform: rotate(8deg); transform-origin: center; }
.pdf-page-merge-bridge span { position: relative; z-index: 1; border-radius: 5px; background: #55b938; padding: .14rem .42rem; font-size: .7rem; font-weight: 900; line-height: 1.2; box-shadow: 0 4px 10px rgba(22, 163, 74, .28); }
.pdf-page-merge-bridge.is-candidate::before { border-color: #65a30d; }
.pdf-page-merge-bridge.is-candidate span { background: #65a30d; }
.pdf-page-state, .pdf-workbench-empty { display: flex; min-height: 240px; align-items: center; justify-content: center; gap: .45rem; border: 1px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; color: var(--text-muted); padding: 1rem; text-align: center; font-size: .84rem; line-height: 1.5; }
.pdf-page-state { width: min(100%, 760px); margin: 0 auto; background: #fff; }
.pdf-page-state.is-error { border-color: #fecaca; background: #fff1f2; color: #b91c1c; }

/* Corrections */
.pdf-correction-toolbar { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; margin-bottom: .6rem; }
.pdf-correction-toolbar label { display: inline-flex; align-items: center; gap: .4rem; color: var(--text-muted); font-size: .84rem; }
.pdf-correction-toolbar select { border: 1px solid var(--border); border-radius: 8px; background: var(--card); padding: .35rem .5rem; color: var(--text); }
.pdf-correction-editor, .pdf-correction-note { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 10px; padding: .7rem .8rem; resize: vertical; color: var(--text); background: var(--card); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; line-height: 1.5; }
.pdf-correction-editor { min-height: 180px; font-size: .8rem; }
.pdf-correction-note { min-height: 64px; margin-top: .5rem; font-size: .84rem; }

/* Source line / reading blocks */
.pdf-source-line { display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: .6rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .8rem; padding: .2rem .4rem; border-radius: 4px; }
.pdf-source-line.focus { background: rgba(37, 99, 235, .1); color: #1e40af; }
.pdf-source-line span { color: var(--text-muted); text-align: right; user-select: none; }
.pdf-source-line code { white-space: pre-wrap; word-break: break-word; }

.pdf-md-render { width: 100%; max-width: 100%; min-width: 0; height: min(720px, calc(100dvh - 235px)); max-height: 720px; overflow: auto; overscroll-behavior: contain; background: #fff; padding: 1.25rem 1.6rem; }
.pdf-md-block { display: block; width: 100%; max-width: 880px; border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 4px; background: transparent; padding: .55rem .75rem .65rem; margin: 0 auto .45rem; color: var(--text); text-align: left; cursor: pointer; transition: border-color .15s ease, box-shadow .15s ease, background .15s ease; }
.pdf-md-block:hover, .pdf-md-block:focus-visible, .pdf-md-block.is-focused { border-color: #dbeafe; border-left-color: #2563eb; background: #f8fbff; box-shadow: none; outline: none; }
.pdf-md-block.is-focused { border-color: #fecaca; border-left-color: #dc2626; background: #fff7f7; box-shadow: inset 0 0 0 1px rgba(220, 38, 38, .08); }
.pdf-md-block-meta { display: inline-flex; margin-bottom: .35rem; border-radius: 4px; background: rgba(37, 99, 235, .08); padding: .12rem .42rem; color: #1d4ed8; font-size: .68rem; font-weight: 850; }
.pdf-md-block.is-focused .pdf-md-block-meta { background: rgba(220, 38, 38, .09); color: #b91c1c; }
.pdf-md-html { overflow-x: auto; color: var(--text); font-size: .86rem; line-height: 1.65; }
.pdf-md-html h1, .pdf-md-html h2, .pdf-md-html h3, .pdf-md-html h4 { margin: .25rem 0 .5rem; color: #0f172a; font-weight: 850; line-height: 1.25; }
.pdf-md-html h1 { font-size: 1.2rem; }
.pdf-md-html h2 { font-size: 1.08rem; }
.pdf-md-html h3 { font-size: .98rem; }
.pdf-md-html p { margin: .45rem 0; }
.pdf-md-html ul, .pdf-md-html ol { margin: .45rem 0 .45rem 1.2rem; padding: 0; }
.pdf-md-html table { width: max-content; min-width: 100%; border-collapse: collapse; margin: .65rem 0; background: #f8fafc; font-size: .82rem; }
.pdf-md-html th, .pdf-md-html td { border: 1px solid #cbd5e1; padding: .42rem .5rem; vertical-align: top; white-space: nowrap; }
.pdf-md-html th { background: #e2e8f0; color: #0f172a; font-weight: 850; }
.pdf-md-html code { border-radius: 5px; background: #e2e8f0; padding: .05rem .25rem; font-size: .8em; }
.pdf-md-html pre { overflow: auto; border-radius: 8px; background: #0f172a; color: #e2e8f0; padding: .75rem; }
.pdf-md-html details { border: 1px solid #dbeafe; border-radius: 8px; background: #f8fbff; padding: .55rem; }
.pdf-page-reading-view { display: grid; gap: .55rem; }
.pdf-page-reading-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; flex-wrap: wrap; padding: .65rem .75rem; border: 1px solid #dbeafe; border-left: 3px solid #2563eb; border-radius: 4px; background: #f8fbff; }
.pdf-page-reading-summary strong { display: block; color: #1d4ed8; font-size: .98rem; }
.pdf-page-reading-summary span { display: block; color: var(--text-muted); font-size: .78rem; margin-top: .15rem; }
.pdf-page-block { min-width: 0; max-width: 100%; overflow: hidden; border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 4px; background: transparent; padding: .6rem .75rem .7rem; transition: border-color .15s ease, background .15s ease, box-shadow .15s ease; }
.pdf-page-block[data-bbox] { cursor: pointer; }
.pdf-page-block:hover { border-color: #dbeafe; border-left-color: #2563eb; background: #f8fbff; }
.pdf-page-block.focus-table, .pdf-page-block.is-trace-focused { border-color: #fecaca; border-left-color: #dc2626; background: #fff7f7; box-shadow: inset 0 0 0 1px rgba(220, 38, 38, .08); }
.pdf-page-block-muted { color: var(--text-muted); background: rgba(248, 250, 252, .58); }
.pdf-page-block-head { display: flex; align-items: flex-start; justify-content: space-between; gap: .6rem; min-width: 0; margin-bottom: .5rem; }
.pdf-page-block-type { display: inline-flex; border-radius: 4px; background: rgba(37, 99, 235, .08); padding: .12rem .42rem; color: #1d4ed8; font-size: .68rem; font-weight: 850; }
.pdf-page-block.focus-table .pdf-page-block-type, .pdf-page-block.is-trace-focused .pdf-page-block-type { background: rgba(220, 38, 38, .09); color: #b91c1c; }
.pdf-page-block-meta { display: block; margin-top: .1rem; color: var(--text-muted); font-size: .74rem; }
.pdf-page-block-tag-row { display: flex; flex-wrap: wrap; gap: .35rem; min-width: 0; margin-bottom: .5rem; }
.pdf-page-block-tag { display: inline-flex; align-items: center; border-radius: 999px; padding: .2rem .5rem; background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; font-size: .74rem; }
.pdf-page-block-text { white-space: pre-wrap; word-break: break-word; font-size: .86rem; line-height: 1.55; }
.pdf-page-block-heading { font-weight: 700; color: var(--text); }
.pdf-page-block-list { margin: 0; padding-left: 1.25rem; font-size: .86rem; line-height: 1.55; }
.pdf-page-table-wrap { display: block; box-sizing: border-box; width: 100%; max-width: 100%; min-width: 0; min-height: 0 !important; height: auto !important; max-height: 420px !important; overflow: auto !important; border-radius: 8px; }
.pdf-page-block-footnotes { display: grid; gap: .3rem; margin-top: .6rem; border-top: 1px solid #e2e8f0; padding-top: .55rem; color: #334155; font-size: .8rem; line-height: 1.55; }
.pdf-page-block-footnote-label { width: fit-content; border-radius: 4px; background: #f1f5f9; padding: .08rem .38rem; color: #475569; font-size: .68rem; font-weight: 850; }
.pdf-page-block-footnotes p { margin: 0; white-space: pre-wrap; word-break: break-word; }
.pdf-task-item { display: flex; align-items: center; justify-content: space-between; gap: .75rem; border: 1px solid var(--border); border-radius: 10px; background: #f8fafc; padding: .65rem .75rem; margin-bottom: .5rem; font-size: .9rem; cursor: pointer; transition: background .15s, border-color .15s, box-shadow .15s; }
.pdf-task-item:hover { border-color: #bfdbfe; background: #eff6ff; box-shadow: 0 8px 20px rgba(37, 99, 235, .04); }
.pdf-task-item .task-main { display: flex; align-items: center; gap: .75rem; min-width: 0; flex: 1 1 auto; }
.pdf-task-item .task-name { min-width: 0; font-weight: 600; word-break: break-word; }
.pdf-task-item .task-meta { display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; flex-shrink: 0; }
.pdf-task-item .task-actions { display: grid; align-items: stretch; justify-content: flex-end; gap: .55rem; flex-shrink: 0; grid-template-columns: repeat(var(--task-action-count, 4), minmax(0, 1fr)); min-width: 0; }
.pdf-task-action { display: inline-flex; align-items: center; justify-content: center; gap: .25rem; min-height: 32px; min-width: 4.5rem; border: 1px solid #cbd5e1; border-radius: 999px; background: #fff; color: #334155; padding: 0 .65rem; font-size: .78rem; font-weight: 700; cursor: pointer; }
.pdf-task-action:hover { border-color: #94a3b8; background: #f1f5f9; }
.pdf-task-action.danger { border-color: #fecaca; background: #fff1f2; color: #b91c1c; }
.pdf-task-action.danger:hover { background: #ffe4e6; }

@media (max-width: 1024px) {
  .pdf-mobile-review-tabs { display: flex; }
  .pdf-mobile-hidden { display: none !important; }
  .pdf-workbench { grid-template-columns: 1fr; border-radius: 14px; }
  .pdf-workbench > .pdf-source-pane + .pdf-source-pane { border-left: 0; border-top: 0; }
}

@media (min-width: 1024px) and (max-width: 1439px), (min-width: 1024px) and (max-height: 820px) {
  .pdf-drop-zone { padding: 28px 20px; }
  .pdf-source-choice { padding: .875rem; }
  .pdf-download-list { max-height: 230px; }
  .pdf-reading-body, .pdf-source-pane > .pdf-table-wrap, .pdf-page-viewer { height: min(560px, calc(100dvh - 205px)); max-height: 560px; }
  .pdf-markdown-body, .pdf-md-preview { max-height: 380px; }
  .pdf-log { max-height: 180px; }
}
@media (max-width: 720px) {
  .pdf-stage { padding: .9rem; border-radius: 18px; }
  .pdf-source-choice { padding: .875rem; border-radius: 16px; }
  .pdf-source-choice-head { align-items: flex-start; gap: .75rem; }
  .pdf-source-choice-head h3 { font-size: .98rem; }
  .pdf-source-choice-head p { font-size: .82rem; }
  .pdf-health-strip { width: 100%; justify-content: flex-start; overflow-x: auto; }
  .pdf-download-search { grid-template-columns: minmax(0, 1fr) auto; gap: .5rem; padding: .5rem; border-radius: 14px; }
  .pdf-download-search label { grid-column: 1 / -1; }
  .pdf-download-search input { height: 46px; font-size: 1rem; }
  .pdf-download-count { justify-content: flex-start; min-width: 0; }
  .pdf-icon-btn { min-width: 92px; height: 46px; }
  .pdf-drop-zone { padding: 1.4rem .9rem; border-radius: 16px; }
  .pdf-upload-actions > button { flex: 1 1 9rem; min-height: 46px; justify-content: center; }
  .pdf-upload-actions > button:first-child { flex-basis: 100%; }
  .pdf-source-separator { margin: .9rem 0; }
  .pdf-artifact-row, .pdf-download-item { grid-template-columns: 1fr; }
  .pdf-download-item { gap: .625rem; padding: .7rem; }
  .pdf-download-title { font-size: .9rem; line-height: 1.38; }
  .pdf-artifact-actions, .pdf-download-actions { justify-content: stretch; }
  .pdf-download-actions .pdf-small-action,
  .pdf-artifact-actions .pdf-small-action { flex: 1 1 7rem; min-height: 44px; }
  .pdf-quality-grid, .pdf-source-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .pdf-md-header { grid-template-columns: 1fr; }
  .pdf-md-actions { width: 100%; min-width: 0; align-items: stretch; justify-content: flex-start; gap: .625rem; overflow-x: auto; overscroll-behavior-x: contain; scrollbar-width: none; padding: 2px 2px .5rem; margin: 0 -2px; }
  .pdf-md-actions::-webkit-scrollbar { display: none; }
  .pdf-md-action-group { flex: 0 0 auto; }
  .pdf-md-action-row { align-items: stretch; }
  .pdf-md-action { min-height: 46px; padding: 0 .7rem; }
  .pdf-workbench { grid-template-columns: 1fr; border-radius: 14px; }
  .pdf-workbench > .pdf-source-pane + .pdf-source-pane { border-left: 0; border-top: 1px solid var(--border); }
  .pdf-source-pane-head { min-height: auto; padding: .8rem .85rem; }
  .pdf-reading-body, .pdf-page-viewer, .pdf-source-pane > .pdf-table-wrap { height: auto; max-height: none; min-height: 300px; }
  .pdf-reading-body { max-height: 68dvh; padding: .85rem; }
  .pdf-page-viewer { min-height: 340px; }
  .pdf-page-toolbar { align-items: flex-start; }
  .pdf-page-topline, .pdf-page-toolbar-actions, .pdf-page-nav, .pdf-zoom-controls { width: 100%; }
  .pdf-page-nav { justify-content: flex-start; }
  .pdf-page-input { width: 100%; max-width: 88px; }
  .pdf-page-canvas { max-height: 70dvh; padding: .85rem; }
  .pdf-page-stage { min-width: 100%; }
  .pdf-page-stage[data-zoom="50"], .pdf-page-stage[data-zoom="100"], .pdf-page-stage[data-zoom="150"] { min-width: 100%; width: 100%; }
  .pdf-table-wrap { border-radius: 12px; max-height: 68dvh; }
  .pdf-source-pane > .pdf-table-wrap { border-radius: 0; }
  .pdf-table-wrap table { min-width: max(100%, 760px); font-size: .8rem; }
  .pdf-table-wrap th, .pdf-table-wrap td { min-width: 84px; padding: .4rem .45rem; }
  .pdf-table-wrap td:first-child, .pdf-table-wrap th:first-child { min-width: 118px; }
  .pdf-table-x-scrollbar { height: 40px; margin-bottom: .55rem; }
  .pdf-table-x-scrollbar-thumb { min-width: 64px; height: 24px; }
  .pdf-correction-toolbar { align-items: stretch; }
  .pdf-correction-toolbar label { width: 100%; justify-content: space-between; }
  .pdf-source-meta { flex-direction: column; align-items: flex-start; }
  .pdf-source-meta b { text-align: left; }
  .pdf-reading-topline { align-items: flex-start; }
  .pdf-reading-mode-switch { width: 100%; flex-wrap: wrap; }
  .pdf-reading-mode-btn { flex: 1 1 0; justify-content: center; text-align: center; }
  .pdf-task-item { flex-direction: column; align-items: flex-start; gap: .625rem; padding: .75rem; }
  .pdf-task-item .task-main { flex-direction: column; align-items: flex-start; gap: .5rem; width: 100%; }
  .pdf-task-item .task-name { width: 100%; }
  .pdf-task-item .task-meta { width: 100%; justify-content: flex-start; gap: .45rem; }
  .pdf-task-item .task-actions { justify-content: flex-start; width: 100%; gap: .45rem; overflow-x: auto; scrollbar-width: none; padding-bottom: 2px; }
  .pdf-task-item .task-actions::-webkit-scrollbar { display: none; }
  .pdf-task-action { min-height: 44px; min-width: 0; border-radius: 12px; padding: 0 .45rem; font-size: .75rem; }
  .pdf-page-reading-summary { flex-direction: column; align-items: flex-start; }
}

@media (max-width: 520px) {
  .pdf-download-search { grid-template-columns: 1fr; }
  .pdf-icon-btn,
  .pdf-download-count { width: 100%; }
  .pdf-drop-zone { padding: 1.2rem .85rem; }
  .pdf-quality-grid, .pdf-source-summary { grid-template-columns: 1fr; }
  .pdf-source-line, .pdf-md-line, .pdf-markdown-line { grid-template-columns: 42px minmax(0, 1fr); gap: .5rem; }
  .pdf-md-heading h3 { font-size: 1.05rem; }
  .pdf-md-heading p { font-size: .78rem; }
  .pdf-md-actions { display: grid; grid-template-columns: 1fr; overflow: visible; gap: .625rem; padding: 0; margin: 0; }
  .pdf-md-action-group { width: 100%; }
  .pdf-md-action-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .375rem; }
  .pdf-md-action-row .pdf-md-action-primary { grid-column: 1 / -1; }
  .pdf-md-action { width: 100%; min-width: 0; min-height: 52px; border-radius: 12px; }
  .pdf-md-action span { flex-direction: column; align-items: flex-start; gap: 2px; }
  .pdf-md-action small { border-left: 0; padding-left: 0; }
  .pdf-md-action b { font-size: .9rem; }
  .pdf-page-toolbar { padding: 10px 8px; }
  .pdf-page-nav { flex-wrap: wrap; gap: .5rem; }
  .pdf-zoom-controls { flex-wrap: wrap; }
  .pdf-zoom-btn { min-width: 0; flex: 1 1 4.5rem; }
  .pdf-correction-editor { min-height: 180px; }
}

.mobile-tab-strip a.is-active {
  background: rgba(0, 113, 227, .12);
  color: var(--color-primary);
}
`
