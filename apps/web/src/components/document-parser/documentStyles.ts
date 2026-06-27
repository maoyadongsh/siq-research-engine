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
.doc-file-pill { display: flex; align-items: center; justify-content: space-between; gap: .55rem; min-height: 36px; min-width: 0; border: 1px solid var(--border); border-radius: 10px; background: #fff; padding: .4rem .6rem; font-size: .8rem; color: var(--text); text-align: left; cursor: pointer; }
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
.doc-preview-grid { display: grid; grid-template-columns: minmax(0, .88fr) minmax(0, 1.12fr); gap: 1rem; align-items: start; padding: 1rem; }
@media (max-width: 1200px) { .doc-preview-grid { grid-template-columns: 1fr; } }
.doc-source-pane, .doc-content-pane { min-width: 0; border: 1px solid var(--border); border-radius: 14px; background: #fff; overflow: hidden; }
.doc-source-page { height: min(620px, calc(100dvh - 260px)); min-height: 360px; overflow: auto; background: #f1f5f9; padding: 1rem; }
.doc-source-block { border: 1px solid #dbeafe; border-radius: 10px; background: #fff; padding: .65rem; margin-bottom: .55rem; }
.doc-source-block b { display: inline-flex; margin-bottom: .35rem; color: #1d4ed8; font-size: .74rem; }
.doc-source-block p { margin: 0; color: var(--text); font-size: .82rem; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.doc-source-link { display: inline-flex; align-items: center; min-height: 28px; margin-top: .55rem; border-radius: 999px; background: rgba(37, 99, 235, .08); padding: 0 .65rem; color: #1d4ed8; font-size: .75rem; font-weight: 800; text-decoration: none; }
.doc-source-link:hover { background: rgba(37, 99, 235, .13); color: #1e40af; }
.doc-markdown, .doc-json { height: min(620px, calc(100dvh - 260px)); min-height: 360px; overflow: auto; background: #0f172a; color: #e2e8f0; padding: 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: .82rem; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
.doc-json { background: #111827; }
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
.doc-empty { display: grid; place-items: center; min-height: 360px; border: 1px dashed #cbd5e1; border-radius: 16px; background: #f8fafc; color: var(--text-muted); text-align: center; padding: 2rem; }
.doc-error { border: 1px solid #fecaca; border-radius: 12px; background: #fff1f2; color: #b91c1c; padding: .75rem .9rem; font-size: .84rem; line-height: 1.5; }
@media (max-width: 720px) {
  .doc-panel-head, .doc-panel-body, .doc-result-head, .doc-preview-grid { padding: .85rem; }
  .doc-segment { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .doc-toggle-grid { grid-template-columns: 1fr; }
  .doc-task-toolbar { grid-template-columns: 1fr; }
  .doc-source-page, .doc-markdown, .doc-json { height: 420px; min-height: 320px; }
  .doc-relation-node { width: 160px; }
  .doc-relation-connector { width: 46px; }
}
`
