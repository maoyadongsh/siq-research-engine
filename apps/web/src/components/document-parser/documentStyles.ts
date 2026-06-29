export const DOCUMENT_CSS = `
.doc-workbench { display: grid; grid-template-columns: minmax(300px, 370px) minmax(0, 1fr); gap: 1rem; align-items: start; }
@media (max-width: 1100px) { .doc-workbench { grid-template-columns: 1fr; } }
.doc-side { display: grid; gap: 1rem; min-width: 0; }
.doc-panel { border: 1px solid var(--border); border-radius: 16px; background: var(--card); box-shadow: 0 8px 24px rgba(15, 23, 42, .045); }
.doc-panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: .75rem; border-bottom: 1px solid var(--border); padding: .9rem 1rem; }
.doc-panel-head h2, .doc-panel-head h3 { margin: 0; color: var(--text); font-size: .98rem; font-weight: 780; }
.doc-panel-head p { margin: .25rem 0 0; color: var(--text-muted); font-size: .82rem; line-height: 1.5; }
.doc-panel-body { padding: 1rem; }
.doc-drop { display: grid; place-items: center; gap: .6rem; min-height: 150px; border: 1.5px dashed #cbd5e1; border-radius: 14px; background: #f8fafc; padding: 1rem; text-align: center; cursor: pointer; transition: border-color .16s ease, background .16s ease, box-shadow .16s ease; }
.doc-drop:hover, .doc-drop.is-dragover { border-color: #2563eb; background: #eff6ff; box-shadow: 0 12px 24px rgba(37, 99, 235, .08); }
.doc-drop svg { color: #2563eb; }
.doc-drop strong { color: var(--text); font-size: .95rem; }
.doc-drop span { color: var(--text-muted); font-size: .8rem; line-height: 1.5; }
.doc-file-list { display: grid; gap: .4rem; margin-top: .75rem; }
.doc-file-pill { display: flex; align-items: center; justify-content: space-between; gap: .55rem; min-height: 40px; min-width: 0; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: .4rem .6rem; font-size: .8rem; color: var(--text); text-align: left; cursor: pointer; }
.doc-file-pill button { display: inline-flex; align-items: center; justify-content: center; min-height: 28px; min-width: 28px; border-radius: 6px; color: var(--text-muted); }
.doc-file-pill button:hover { background: var(--color-bg); color: var(--color-text); }
.doc-file-pill span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-field { display: grid; gap: .35rem; }
.doc-label { color: var(--text); font-size: .8rem; font-weight: 750; }
.doc-input, .doc-select, .doc-textarea { width: 100%; min-height: 42px; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 0 .7rem; color: var(--text); font: inherit; font-size: .88rem; outline: none; }
.doc-textarea { min-height: 150px; padding: .7rem; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; line-height: 1.5; }
.doc-input:focus, .doc-select:focus, .doc-textarea:focus { border-color: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .1); }
.doc-segment { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .35rem; border: 1px solid var(--border); border-radius: 12px; background: #f8fafc; padding: .3rem; }
.doc-segment button { min-height: 36px; border: 1px solid transparent; border-radius: 9px; background: transparent; color: var(--text-muted); font-size: .78rem; font-weight: 800; cursor: pointer; }
.doc-segment button.active { border-color: #bfdbfe; background: #fff; color: #1d4ed8; box-shadow: 0 4px 12px rgba(15, 23, 42, .06); }
.doc-toggle-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .55rem; }
.doc-check { display: flex; align-items: center; gap: .45rem; min-height: 40px; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: .45rem .6rem; font-size: .8rem; font-weight: 700; color: var(--text); }
.doc-check.compact { min-height: 34px; padding: .35rem .55rem; white-space: nowrap; }
.doc-task-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) minmax(120px, 150px); gap: .5rem; margin-bottom: .65rem; }
.doc-search { display: flex; align-items: center; gap: .45rem; min-width: 0; min-height: 42px; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: 0 .65rem; color: var(--text-muted); }
.doc-search input { min-width: 0; width: 100%; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; font-size: .84rem; }
.doc-status-filter { min-height: 42px; }
.doc-batch-bar { display: flex; align-items: center; justify-content: space-between; gap: .6rem; flex-wrap: wrap; margin-bottom: .65rem; border: 1px solid var(--border); border-radius: 12px; background: #f8fafc; padding: .55rem; }
.doc-batch-count { color: var(--text-muted); font-size: .78rem; font-weight: 800; white-space: nowrap; }
.doc-task-list { display: grid; gap: .5rem; max-height: 420px; overflow: auto; }
.doc-task { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: .6rem; align-items: center; border: 1px solid var(--border); border-radius: 12px; background: #fff; padding: .7rem; cursor: pointer; text-align: left; transition: border-color .15s ease, background .15s ease, box-shadow .15s ease; }
.doc-task-check { width: 16px; height: 16px; accent-color: #2563eb; }
.doc-task:hover, .doc-task.active { border-color: #93c5fd; background: #f8fbff; box-shadow: 0 8px 20px rgba(37, 99, 235, .05); }
.doc-task-title { color: var(--text); font-size: .88rem; font-weight: 750; word-break: break-word; }
.doc-task-meta { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .35rem; color: var(--text-muted); font-size: .74rem; }
.doc-badge { display: inline-flex; align-items: center; min-height: 26px; border: 1px solid var(--border); border-radius: 999px; background: #fff; padding: 0 .55rem; color: #475569; font-size: .72rem; font-weight: 800; white-space: nowrap; }
.doc-badge.done { border-color: rgba(22, 163, 74, .2); background: rgba(22, 163, 74, .08); color: #15803d; }
.doc-badge.warn { border-color: rgba(202, 138, 4, .2); background: rgba(202, 138, 4, .08); color: #a16207; }
.doc-badge.fail { border-color: rgba(220, 38, 38, .2); background: rgba(220, 38, 38, .08); color: #b91c1c; }
.doc-badge.run { border-color: rgba(37, 99, 235, .2); background: rgba(37, 99, 235, .08); color: #1d4ed8; }
.doc-result-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; border-bottom: 1px solid var(--border); padding: 1rem; }
.doc-result-title { min-width: 0; }
.doc-result-title h2 { margin: 0; color: var(--text); font-size: 1.05rem; font-weight: 800; word-break: break-word; }
.doc-result-title p { margin: .35rem 0 0; color: var(--text-muted); font-size: .84rem; }
.doc-action-row { display: flex; align-items: center; justify-content: flex-end; gap: .5rem; flex-wrap: wrap; }
.doc-progress { height: 8px; overflow: hidden; border-radius: 999px; background: #e2e8f0; }
.doc-progress > span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #2563eb, #14b8a6); transition: width .2s ease; }
.doc-progress-stack { display: grid; gap: 1rem; }
.doc-progress-card { display: grid; gap: .7rem; padding: 1rem; }
.doc-progress-topline { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.doc-progress-title { display: flex; min-width: 0; align-items: center; gap: .45rem; }
.doc-progress-title h2 { margin: 0; color: var(--text); font-size: .98rem; font-weight: 780; }
.doc-live-dot { width: 9px; height: 9px; flex: 0 0 auto; border: 2px solid #bfdbfe; border-radius: 999px; background: #2563eb; box-shadow: 0 0 0 3px rgba(37, 99, 235, .1); }
.doc-progress-bar-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: .65rem; }
.doc-progress-bar-row strong { color: #64748b; font-size: .78rem; font-variant-numeric: tabular-nums; }
.doc-progress-stage { color: var(--text-muted); font-size: .8rem; line-height: 1.5; }
.doc-progress-facts { display: flex; flex-wrap: wrap; gap: .55rem 1rem; color: #64748b; font-size: .78rem; line-height: 1.45; }
.doc-progress-facts span { display: inline-flex; min-width: 0; align-items: center; gap: .28rem; }
.doc-progress-facts .strong { color: #2563eb; font-weight: 800; }
.doc-log-box { max-height: 240px; overflow: auto; border-top: 1px solid var(--border); background: #f8fafc; padding: .6rem .8rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.doc-log-line { display: grid; grid-template-columns: 62px minmax(0, 1fr); gap: .55rem; border-bottom: 1px solid #e2e8f0; padding: .28rem 0; color: #334155; font-size: .76rem; line-height: 1.45; }
.doc-log-line:last-child { border-bottom: 0; }
.doc-log-line span { color: #64748b; font-variant-numeric: tabular-nums; }
.doc-log-line p { margin: 0; word-break: break-word; }
.doc-log-line.error p { color: #b91c1c; }
.doc-log-line.warn p, .doc-log-line.warning p { color: #a16207; }
.doc-log-line.success p { color: #15803d; }
.doc-workflow-head { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 1rem; }
.doc-workflow-head h3 { display: flex; align-items: center; gap: .45rem; margin: 0; color: var(--text); font-size: 1rem; font-weight: 820; }
.doc-workflow-head p { margin: .35rem 0 0; color: var(--text-muted); font-size: .84rem; line-height: 1.55; }
.doc-pipeline-note { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .75rem; align-items: flex-start; border: 1px solid rgba(37, 99, 235, .18); border-radius: 14px; background: rgba(37, 99, 235, .06); padding: .85rem 1rem; color: var(--text); font-size: .84rem; line-height: 1.55; }
.doc-pipeline-note code { border-radius: 6px; background: rgba(255, 255, 255, .7); padding: .1rem .3rem; font-size: .78rem; }
.doc-preview-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0; align-items: stretch; padding: 0; }
@media (max-width: 720px) { .doc-preview-grid { grid-template-columns: 1fr; } }
.doc-source-pane, .doc-content-pane { min-width: 0; border: 0; border-radius: 0; background: #fff; overflow: hidden; }
.doc-source-pane { border-right: 1px solid var(--border); }
@media (max-width: 720px) { .doc-source-pane { border-right: 0; border-bottom: 1px solid var(--border); } }
.doc-source-page { height: min(720px, calc(100dvh - 220px)); min-height: 420px; overflow: auto; background: #f5f6f8; padding: 0 1.6rem 1.25rem; }
.doc-page-controls { display: inline-flex; align-items: center; gap: .35rem; flex: 0 0 auto; }
.doc-page-select { min-height: 26px; border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 0 .45rem; color: var(--text); font-size: .75rem; font-weight: 800; outline: none; }
.doc-pdf-page-stack { position: relative; display: grid; gap: .55rem; }
.doc-pdf-page-card { display: grid; gap: .55rem; margin: 1rem auto 1.25rem; max-width: 860px; }
.doc-pdf-page-title { display: flex; align-items: center; justify-content: space-between; gap: .75rem; color: #111827; font-size: .78rem; font-weight: 850; }
.doc-pdf-page-canvas { position: relative; overflow: hidden; border: 1px solid #e5e7eb; border-radius: 2px; background: #fff; box-shadow: 0 1px 2px rgba(15, 23, 42, .06); }
.doc-pdf-page-image { display: block; width: 100%; height: auto; user-select: none; }
.doc-auth-image-state { display: flex; min-height: 240px; align-items: center; justify-content: center; gap: .45rem; color: var(--text-muted); font-size: .82rem; }
.doc-pdf-overlay-layer { position: absolute; inset: 0; pointer-events: none; }
.doc-pdf-bbox { position: absolute; pointer-events: auto; border: 1px solid rgba(37, 99, 235, .78); border-radius: 0; background: rgba(37, 99, 235, .035); color: #1d4ed8; cursor: pointer; transition: background .15s ease, border-color .15s ease, box-shadow .15s ease, opacity .15s ease; }
.doc-pdf-bbox span { position: absolute; left: -1px; top: -1px; transform: translateY(-100%); max-width: 5rem; overflow: hidden; text-overflow: ellipsis; border-radius: 2px 2px 0 0; background: #2563eb; padding: .08rem .32rem; color: #fff; font-size: .62rem; font-weight: 850; line-height: 1.15; white-space: nowrap; opacity: 1; pointer-events: none; transition: opacity .15s ease; }
.doc-pdf-bbox:not(:hover):not(:focus-visible):not(.is-focused) span { opacity: .88; }
.doc-pdf-bbox.is-table { border-color: #16a34a; background: rgba(34, 197, 94, .08); }
.doc-pdf-bbox.is-table span { background: #16a34a; }
.doc-pdf-bbox.is-figure { border-color: #ea580c; background: rgba(249, 115, 22, .08); }
.doc-pdf-bbox.is-figure span { background: #ea580c; }
.doc-pdf-bbox.is-focused { z-index: 4; border-width: 2px; border-color: #dc2626; background: rgba(220, 38, 38, .12); box-shadow: 0 0 0 3px rgba(220, 38, 38, .18); }
.doc-pdf-bbox.is-focused span { background: #dc2626; }
.doc-merge-stem { position: absolute; z-index: 3; width: 0; min-height: 24px; pointer-events: auto; border: 0; border-left: 2px dashed #55b938; background: transparent; color: #fff; cursor: pointer; }
.doc-merge-stem span { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); border-radius: 5px; background: #55b938; padding: .12rem .36rem; font-size: .68rem; font-weight: 900; line-height: 1.15; box-shadow: 0 4px 10px rgba(22, 163, 74, .28); white-space: nowrap; }
.doc-merge-stem.is-to span { top: 42%; }
.doc-merge-stem.is-candidate span { background: #65a30d; }
.doc-merge-stem.is-rejected { border-left-color: #dc2626; }
.doc-merge-stem.is-rejected span { background: #dc2626; }
.doc-page-merge-bridge { position: relative; display: flex; align-items: center; justify-content: center; min-height: 54px; border: 0; background: transparent; color: #fff; cursor: pointer; }
.doc-page-merge-bridge::before { content: ""; position: absolute; inset: 50% 12% auto 12%; border-top: 2px dashed #55b938; transform: rotate(8deg); transform-origin: center; }
.doc-page-merge-bridge span { position: relative; z-index: 1; border-radius: 5px; background: #55b938; padding: .14rem .42rem; font-size: .7rem; font-weight: 900; line-height: 1.2; box-shadow: 0 4px 10px rgba(22, 163, 74, .28); }
.doc-page-merge-bridge.is-candidate::before { border-color: #65a30d; }
.doc-page-merge-bridge.is-candidate span { background: #65a30d; }
.doc-page-merge-bridge.is-rejected::before { border-color: #dc2626; }
.doc-page-merge-bridge.is-rejected span { background: #dc2626; }
.doc-source-block { border: 1px solid #dbeafe; border-radius: 10px; background: #fff; padding: .65rem; margin-bottom: .55rem; }
.doc-source-block b { display: inline-flex; margin-bottom: .35rem; color: #1d4ed8; font-size: .74rem; }
.doc-source-block p { margin: 0; color: var(--text); font-size: .82rem; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.doc-source-link { display: inline-flex; align-items: center; min-height: 28px; margin-top: .55rem; border: 0; border-radius: 999px; background: rgba(37, 99, 235, .08); padding: 0 .65rem; color: #1d4ed8; font: inherit; font-size: .75rem; font-weight: 800; text-decoration: none; cursor: pointer; }
.doc-source-link:hover { background: rgba(37, 99, 235, .13); color: #1e40af; }
.doc-markdown, .doc-json { height: min(620px, calc(100dvh - 260px)); min-height: 360px; overflow: auto; background: #0f172a; color: #e2e8f0; padding: 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .82rem; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
.doc-json { background: #111827; }
.doc-md-render { height: min(720px, calc(100dvh - 220px)); min-height: 420px; overflow: auto; background: #fff; padding: 1.25rem 1.6rem; }
.doc-md-render.is-full { border-top: 1px solid var(--border); }
.doc-md-block { display: block; width: 100%; max-width: 880px; border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 4px; background: transparent; padding: .55rem .75rem .65rem; margin: 0 auto .45rem; color: var(--text); text-align: left; cursor: pointer; transition: border-color .15s ease, box-shadow .15s ease, background .15s ease; }
.doc-md-block:hover, .doc-md-block:focus-visible, .doc-md-block.is-focused { border-color: #dbeafe; border-left-color: #2563eb; background: #f8fbff; box-shadow: none; outline: none; }
.doc-md-block.is-focused { border-color: #fecaca; border-left-color: #dc2626; background: #fff7f7; box-shadow: inset 0 0 0 1px rgba(220, 38, 38, .08); }
.doc-md-block-meta { display: inline-flex; margin-bottom: .35rem; border-radius: 4px; background: rgba(37, 99, 235, .08); padding: .12rem .42rem; color: #1d4ed8; font-size: .68rem; font-weight: 850; }
.doc-md-block.is-focused .doc-md-block-meta { background: rgba(220, 38, 38, .09); color: #b91c1c; }
.doc-md-html { overflow-x: auto; color: var(--text); font-size: .86rem; line-height: 1.65; }
.doc-md-html h1, .doc-md-html h2, .doc-md-html h3, .doc-md-html h4 { margin: .25rem 0 .5rem; color: #0f172a; font-weight: 850; line-height: 1.25; }
.doc-md-html h1 { font-size: 1.2rem; }
.doc-md-html h2 { font-size: 1.08rem; }
.doc-md-html h3 { font-size: .98rem; }
.doc-md-html p { margin: .45rem 0; }
.doc-md-html ul, .doc-md-html ol { margin: .45rem 0 .45rem 1.2rem; padding: 0; }
.doc-md-html table { width: max-content; min-width: 100%; border-collapse: collapse; margin: .65rem 0; background: #f8fafc; font-size: .82rem; }
.doc-md-html th, .doc-md-html td { border: 1px solid #cbd5e1; padding: .42rem .5rem; vertical-align: top; white-space: nowrap; }
.doc-md-html th { background: #e2e8f0; color: #0f172a; font-weight: 850; }
.doc-md-html code { border-radius: 5px; background: #e2e8f0; padding: .05rem .25rem; font-size: .8em; }
.doc-md-html pre { overflow: auto; border-radius: 8px; background: #0f172a; color: #e2e8f0; padding: .75rem; }
.doc-md-html details { border: 1px solid #dbeafe; border-radius: 8px; background: #f8fbff; padding: .55rem; }
.doc-md-page-marker, .doc-md-image-ref { border-radius: 8px; background: #eef2ff; padding: .45rem .55rem; color: #3730a3; font-size: .78rem; font-weight: 850; }
.doc-md-empty { color: var(--text-muted); }
.doc-figure-image { display: block; max-height: 320px; max-width: 100%; margin-top: .75rem; border: 1px solid var(--border); border-radius: 10px; object-fit: contain; }
.doc-table-list, .doc-figure-list, .doc-quality-list, .doc-artifact-list { display: grid; gap: .75rem; padding: 1rem; max-height: min(620px, calc(100dvh - 260px)); overflow: auto; }
.doc-data-row { border: 1px solid var(--border); border-radius: 12px; background: #fff; padding: .8rem; }
.doc-data-row h3 { margin: 0; color: var(--text); font-size: .92rem; font-weight: 800; }
.doc-data-row p { margin: .35rem 0 0; color: var(--text-muted); font-size: .8rem; line-height: 1.5; }
.doc-table-markdown { margin-top: .6rem; overflow: auto; border-radius: 10px; background: #0f172a; color: #e2e8f0; padding: .75rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .78rem; white-space: pre; }
.doc-relation-flow { display: flex; align-items: center; gap: .55rem; min-width: 0; overflow-x: auto; margin-top: .75rem; border: 1px solid #dbeafe; border-radius: 12px; background: #f8fbff; padding: .7rem; }
.doc-relation-step { display: flex; align-items: center; gap: .55rem; flex: 0 0 auto; min-width: 0; }
.doc-relation-node { display: grid; gap: .22rem; width: clamp(150px, 18vw, 220px); min-height: 98px; border: 1px solid #bfdbfe; border-radius: 10px; background: #fff; padding: .62rem; box-shadow: 0 6px 16px rgba(15, 23, 42, .05); }
.doc-relation-page { width: fit-content; border-radius: 999px; background: rgba(37, 99, 235, .08); padding: .12rem .45rem; color: #1d4ed8; font-size: .68rem; font-weight: 850; }
.doc-relation-node strong { color: #0f172a; font-size: .78rem; font-weight: 850; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-relation-node span:not(.doc-relation-page) { color: var(--text); font-size: .78rem; line-height: 1.35; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-relation-node em { color: var(--text-muted); font-size: .72rem; font-style: normal; }
.doc-relation-connector { display: flex; align-items: center; width: clamp(44px, 6vw, 96px); min-width: 44px; }
.doc-relation-connector span { display: block; width: 100%; border-top: 2px dashed #2563eb; }
.doc-relation-connector::after { content: ""; width: 8px; height: 8px; margin-left: -5px; border-top: 2px solid #2563eb; border-right: 2px solid #2563eb; transform: rotate(45deg); background: transparent; }
.doc-relation-flow.is-accepted { border-color: rgba(22, 163, 74, .28); background: rgba(22, 163, 74, .06); }
.doc-relation-flow.is-accepted .doc-relation-node { border-color: rgba(22, 163, 74, .26); }
.doc-relation-flow.is-accepted .doc-relation-page { background: rgba(22, 163, 74, .1); color: #15803d; }
.doc-relation-flow.is-accepted .doc-relation-connector span { border-color: #16a34a; }
.doc-relation-flow.is-accepted .doc-relation-connector::after { border-color: #16a34a; }
.doc-relation-flow.is-rejected { border-color: rgba(220, 38, 38, .25); background: rgba(220, 38, 38, .05); }
.doc-relation-flow.is-rejected .doc-relation-node { border-color: rgba(220, 38, 38, .22); }
.doc-relation-flow.is-rejected .doc-relation-page { background: rgba(220, 38, 38, .09); color: #b91c1c; }
.doc-relation-flow.is-rejected .doc-relation-connector span { border-color: #dc2626; }
.doc-relation-flow.is-rejected .doc-relation-connector::after { border-color: #dc2626; }
details.doc-panel > summary { list-style: none; cursor: pointer; }
details.doc-panel > summary::-webkit-details-marker { display: none; }
details.doc-panel > summary::marker { display: none; }
.scroll-hint { position: relative; }
@media (max-width: 768px) {
  .scroll-hint::after { content: ""; position: absolute; top: 0; right: 0; bottom: 0; width: 2rem; background: linear-gradient(to left, var(--card), transparent); pointer-events: none; opacity: .7; }
}
.doc-empty { display: grid; place-items: center; min-height: 360px; border: 1px dashed #cbd5e1; border-radius: 16px; background: #f8fafc; color: var(--text-muted); text-align: center; padding: 2rem; }
.doc-error { border: 1px solid #fecaca; border-radius: 12px; background: #fff1f2; color: #b91c1c; padding: .75rem .9rem; font-size: .84rem; line-height: 1.5; }
@media (max-width: 720px) {
  .doc-panel-head, .doc-panel-body, .doc-result-head, .doc-preview-grid { padding: .85rem; }
  .doc-segment { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .doc-toggle-grid { grid-template-columns: 1fr; }
  .doc-task-toolbar { grid-template-columns: 1fr; }
  .doc-source-page, .doc-markdown, .doc-json, .doc-md-render { height: 420px; min-height: 320px; }
  .doc-relation-node { width: 160px; }
  .doc-relation-connector { width: 46px; }
  .doc-batch-bar { align-items: stretch; }
  .doc-batch-bar .doc-action-row { flex-wrap: wrap; justify-content: flex-start; }
  .doc-batch-bar .doc-action-row button { min-height: 44px; min-width: 44px; }
}
`
