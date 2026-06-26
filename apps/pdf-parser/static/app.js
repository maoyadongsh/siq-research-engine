(function () {
  const $ = (id) => document.getElementById(id);
  let currentFile = null;
  let selectedFiles = [];
  let currentTaskId = null;
  let pollInterval = null;
  let markdownContent = "";
  let markdownLines = [];
  let logCount = 0;
  let isCancelled = false;
  let mineruReadyForSubmit = false;
  let currentPdfContext = null;
  let currentSourceContext = null;

  async function checkHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      $("mineruDot").className = "health-dot " + (data.mineru ? "ok" : "bad");
      $("vlmDot").className = "health-dot " + (data.vlm ? "ok" : "bad");
      mineruReadyForSubmit = !!data.submit_ready;
      if (data.warning) {
        $("readinessBanner").textContent = data.warning;
        $("readinessBanner").classList.add("active");
      } else {
        $("readinessBanner").textContent = "";
        $("readinessBanner").classList.remove("active");
      }
      return data;
    } catch (error) {
      $("mineruDot").className = "health-dot bad";
      $("vlmDot").className = "health-dot bad";
      mineruReadyForSubmit = false;
      $("readinessBanner").textContent = "MinerU 状态检查失败，当前不建议上传。";
      $("readinessBanner").classList.add("active");
      return null;
    }
  }

  function renderSelectedFiles() {
    const list = $("selectedFilesList");
    const card = $("selectedFilesCard");
    if (!selectedFiles.length) {
      $("fileName").textContent = "";
      card.classList.remove("active");
      list.innerHTML = "";
      $("convertBtn").disabled = true;
      return;
    }
    $("fileName").textContent =
      selectedFiles.length === 1
        ? selectedFiles[0].name + " (" + formatSize(selectedFiles[0].size) + ")"
        : "已选择 " + selectedFiles.length + " 个 PDF";
    list.innerHTML = selectedFiles
      .map((file) => {
        return (
          '<div class="selected-file-item"><b>' +
          escapeHtml(file.name) +
          "</b><span>" +
          escapeHtml(formatSize(file.size)) +
          "</span></div>"
        );
      })
      .join("");
    card.classList.add("active");
    $("convertBtn").disabled = false;
  }

  function handleFiles(files) {
    const incoming = Array.from(files || []);
    if (!incoming.length) return;
    if (incoming.length > 5) {
      showError("一次最多选择 5 个 PDF");
      return;
    }
    for (const file of incoming) {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        showError("仅支持 PDF 文件");
        return;
      }
      if (file.size > 100 * 1024 * 1024) {
        showError("文件超过 100 MB 限制: " + file.name);
        return;
      }
    }
    selectedFiles = incoming;
    currentFile = selectedFiles[0] || null;
    $("clearBtn").style.display = "inline-flex";
    hideError();
    renderSelectedFiles();
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || seconds < 0) return "--";
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60);
    if (minutes > 0) return minutes + "分" + remainder + "秒";
    return remainder + "秒";
  }

  function formatFinancialNumber(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "--";
    const abs = Math.abs(numeric);
    if (abs >= 100000000) return (numeric / 100000000).toFixed(2) + "亿";
    if (abs >= 10000) return (numeric / 10000).toFixed(2) + "万";
    return numeric.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function setUploadProgress(pct, status) {
    $("uploadProgressBar").style.width = pct + "%";
    $("uploadStatus").textContent = status;
    $("uploadPercent").textContent = Math.round(pct) + "%";
  }

  function setParseProgress(pct, status) {
    $("parseProgressBar").style.width = pct + "%";
    $("parseStatus").textContent = status;
    $("parsePercent").textContent = Math.round(pct) + "%";
  }

  function showError(message) {
    $("errorBox").textContent = message;
    $("errorBox").classList.add("active");
  }

  function hideError() {
    $("errorBox").classList.remove("active");
  }

  function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2500);
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function translateStatus(status) {
    const map = {
      queued: "已排队",
      uploaded: "已上传",
      submitting: "提交中",
      submitted: "已提交",
      pending: "排队中",
      processing: "处理中",
      completed: "已完成",
      completed_missing_artifact: "结果缺失",
      failed: "失败",
      error: "错误",
      cancelled: "已停止",
    };
    return map[status] || status;
  }

  function isTerminalStatus(status) {
    return ["completed", "completed_missing_artifact", "success", "done", "finished", "failed", "error", "failure", "cancelled"].includes(status);
  }

  function resetAll() {
    currentFile = null;
    selectedFiles = [];
    currentTaskId = null;
    markdownContent = "";
    markdownLines = [];
    logCount = 0;
    isCancelled = false;
    currentPdfContext = null;
    currentSourceContext = null;
    $("fileName").textContent = "";
    $("fileInput").value = "";
    $("convertBtn").disabled = true;
    $("clearBtn").style.display = "none";
    $("cancelBtn").style.display = "none";
    $("uploadStage").classList.remove("active");
    $("parseStage").classList.remove("active");
    $("logCard").style.display = "none";
    $("artifactCard").style.display = "none";
    $("qualityCard").style.display = "none";
    $("financialCard").style.display = "none";
    $("sourceCard").style.display = "none";
    $("previewArea").classList.remove("active");
    $("logPanel").innerHTML = "";
    $("selectedFilesCard").classList.remove("active");
    $("selectedFilesList").innerHTML = "";
    hideError();
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  async function cancelCurrentTask() {
    if (!currentTaskId) return;
    if (!confirm("确定停止查看当前任务吗？\n如果 MinerU 支持取消，我也会尝试通知后端停止处理。")) {
      return;
    }
    try {
      const res = await fetch("/api/cancel/" + encodeURIComponent(currentTaskId), {
        method: "POST",
      });
      const data = await res.json();
      if (data.success) {
        isCancelled = true;
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
        $("cancelBtn").style.display = "none";
        $("convertBtn").disabled = false;
        $("btnSpinner").style.display = "none";
        $("btnText").textContent = "批量入队";
        showToast(data.upstream_cancelled ? "任务已取消" : "已停止查看任务");
      }
    } catch (error) {
      console.error("Cancel error:", error);
    }
  }

  function appendLogs(logs) {
    if (!logs || logs.length === 0) return;
    const panel = $("logPanel");
    const html = logs
      .map((log) => {
        const time = new Date(log.time).toLocaleTimeString("zh-CN", { hour12: false });
        const cls = log.level || "info";
        return (
          '<div class="log-entry"><span class="log-time">' +
          time +
          '</span><span class="log-msg ' +
          cls +
          '">' +
          escapeHtml(log.message) +
          "</span></div>"
        );
      })
      .join("");
    panel.insertAdjacentHTML("beforeend", html);
    panel.scrollTop = panel.scrollHeight;
  }

  function showMarkdown(text) {
    markdownContent = text || "";
    markdownLines = markdownContent.split(/\r?\n/);
    $("markdownPreview").innerHTML = markdownLines
      .map((line, idx) => {
        const lineNo = idx + 1;
        return (
          '<div class="md-line" id="md-line-' +
          lineNo +
          '"><span class="md-line-no">' +
          lineNo +
          '</span><span class="md-line-text">' +
          escapeHtml(line || " ") +
          "</span></div>"
        );
      })
      .join("");
    $("previewArea").classList.add("active");
  }

  function focusMarkdownLine(line) {
    if (!line) return;
    const target = $("md-line-" + line);
    if (!target) return;
    document.querySelectorAll(".md-line.focus").forEach((item) => item.classList.remove("focus"));
    target.classList.add("focus");
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function tableButton(item, labelPrefix) {
    if (!item || !item.table_index) {
      return '<span class="quality-muted">' + escapeHtml((labelPrefix || "表") + " 未定位") + "</span>";
    }
    const page =
      item.pdf_page_number
        ? " / PDF " + item.pdf_page_number + "页" + (item.pdf_page_source === "markdown_marker_inferred" ? "(推断)" : "")
        : "";
    const line = item.line ? " / 行 " + item.line : "";
    const label = (labelPrefix || "表") + " " + item.table_index + line + page;
    return (
      '<button class="trace-btn" data-table-index="' +
      escapeHtml(String(item.table_index)) +
      '" data-line="' +
      escapeHtml(String(item.line)) +
      '">' +
      escapeHtml(label) +
      "</button>"
    );
  }

  function sanitizeTableHtml(html) {
    if (!html) return "";
    const doc = new DOMParser().parseFromString(String(html), "text/html");
    doc.querySelectorAll("script, style, iframe, object, embed, link, meta").forEach((node) => node.remove());
    const allowedTags = new Set(["TABLE", "THEAD", "TBODY", "TFOOT", "TR", "TH", "TD", "CAPTION", "COLGROUP", "COL"]);
    doc.body.querySelectorAll("*").forEach((node) => {
      if (!allowedTags.has(node.tagName)) {
        node.replaceWith(document.createTextNode(node.textContent || ""));
        return;
      }
      Array.from(node.attributes).forEach((attr) => {
        if (!["rowspan", "colspan", "data-bbox", "data-cell-bbox", "bbox"].includes(attr.name.toLowerCase())) {
          node.removeAttribute(attr.name);
        }
      });
    });
    return doc.body.innerHTML;
  }

  function makeEditableTableHtml(html) {
    if (!html) return "";
    const doc = new DOMParser().parseFromString(sanitizeTableHtml(html), "text/html");
    doc.querySelectorAll("th, td").forEach((cell) => {
      cell.setAttribute("contenteditable", "true");
      cell.setAttribute("spellcheck", "false");
      cell.setAttribute("tabindex", "0");
    });
    return doc.body.innerHTML;
  }

  function serializeEditableTable() {
    const table = document.querySelector("#editableTableWrap table");
    if (!table) return "";
    const clone = table.cloneNode(true);
    clone.querySelectorAll("[contenteditable], [spellcheck], [tabindex]").forEach((node) => {
      node.removeAttribute("contenteditable");
      node.removeAttribute("spellcheck");
      node.removeAttribute("tabindex");
    });
    clone.querySelectorAll(".selected-cell, .selected-row").forEach((node) => {
      node.classList.remove("selected-cell", "selected-row");
      if (!node.getAttribute("class")) {
        node.removeAttribute("class");
      }
    });
    return clone.outerHTML;
  }

  function syncCorrectionFromEditableTable() {
    const text = $("correctionText");
    const tableHtml = serializeEditableTable();
    if (!text || !tableHtml) return;
    text.value = tableHtml;
    if (currentSourceContext) {
      currentSourceContext.correctionText = tableHtml;
    }
    const status = $("correctionStatus");
    if (status && status.value === "unreviewed") {
      status.value = "fixed";
    }
  }

  function pageBlockBbox(block) {
    const bbox = Array.isArray(block && block.bbox) ? block.bbox : [];
    if (bbox.length !== 4) return "";
    return "bbox: " + bbox.map((value) => String(value)).join(", ");
  }

  function normalizeCellText(text) {
    return String(text || "")
      .replace(/\s+/g, "")
      .replace(/[，,]/g, "")
      .trim();
  }

  function isUsefulTextAnchor(text) {
    const normalized = normalizeCellText(text);
    if (normalized.length < 6) return false;
    if (/^[\d.\-+()%（）/／—–_]+$/.test(normalized)) return false;
    if (/^(--|-|不适用|无|否|是|0|0.00)$/.test(normalized)) return false;
    return true;
  }

  function parseBbox(value) {
    if (Array.isArray(value)) {
      const bbox = value.map((item) => Number(item));
      return bbox.length === 4 && bbox.every((item) => Number.isFinite(item)) ? bbox : null;
    }
    if (!value) return null;
    const bbox = String(value)
      .replace(/[\[\]]/g, "")
      .split(/[,\s]+/)
      .filter(Boolean)
      .map((item) => Number(item));
    return bbox.length === 4 && bbox.every((item) => Number.isFinite(item)) ? bbox : null;
  }

  function normalizeBboxScale(bbox) {
    if (!bbox || bbox.length !== 4) return null;
    const extent = currentPdfContext && currentPdfContext.bboxExtent ? currentPdfContext.bboxExtent : {};
    if (bbox.every((value) => value >= 0 && value <= 1) && extent.width && extent.height) {
      return [bbox[0] * extent.width, bbox[1] * extent.height, bbox[2] * extent.width, bbox[3] * extent.height];
    }
    return bbox;
  }

  function selectedCellPosition(cell) {
    const table = cell && cell.closest ? cell.closest("table") : null;
    const row = cell && cell.closest ? cell.closest("tr") : null;
    if (!table || !row) return null;
    return {
      rowIndex: Array.prototype.indexOf.call(table.rows, row),
      cellIndex: Array.prototype.indexOf.call(row.cells, cell),
    };
  }

  function findTextAnchorTrace(cell) {
    if (!currentSourceContext || !currentPdfContext) return null;
    const text = normalizeCellText(cell.textContent || "");
    if (!isUsefulTextAnchor(text)) return null;
    const pageData =
      currentSourceContext.pageCache && currentSourceContext.pageCache[currentPdfContext.sourcePage]
        ? currentSourceContext.pageCache[currentPdfContext.sourcePage]
        : null;
    if (!pageData || !Array.isArray(pageData.blocks)) return null;

    const matches = [];
    pageData.blocks.forEach((block) => {
      if (!block || block.type === "table") return;
      let blockText = "";
      if (Array.isArray(block.list_items)) {
        blockText = block.list_items.join("");
      } else {
        blockText = block.text || "";
      }
      const normalizedBlock = normalizeCellText(blockText);
      if (normalizedBlock === text || (text.length >= 10 && normalizedBlock.includes(text))) {
        const bbox = normalizeBboxScale(parseBbox(block.bbox));
        if (bbox) matches.push({ bbox, block });
      }
    });

    if (matches.length !== 1) return null;
    return {
      pageNumber: currentPdfContext.sourcePage,
      bbox: matches[0].bbox,
      source: "text_anchor",
      confidence: "medium",
    };
  }

  function traceForSelectedCell(cell) {
    if (!cell || !currentPdfContext) return null;
    const directBbox =
      parseBbox(cell.dataset.cellBbox) ||
      parseBbox(cell.dataset.bbox) ||
      parseBbox(cell.getAttribute("bbox"));
    if (directBbox) {
      return {
        pageNumber: currentPdfContext.sourcePage,
        bbox: normalizeBboxScale(directBbox),
        source: "cell_bbox",
        confidence: "high",
      };
    }
    return findTextAnchorTrace(cell);
  }

  function clearSelectedTableCell(wrap) {
    const root = wrap || $("editableTableWrap");
    if (!root) return;
    root.querySelectorAll(".selected-cell").forEach((node) => node.classList.remove("selected-cell"));
    root.querySelectorAll(".selected-row").forEach((node) => node.classList.remove("selected-row"));
  }

  function applySelectedCellPosition() {
    if (!currentSourceContext || !currentSourceContext.selectedCell) return;
    const table = document.querySelector("#editableTableWrap table");
    if (!table) return;
    const row = table.rows[currentSourceContext.selectedCell.rowIndex];
    const cell = row && row.cells[currentSourceContext.selectedCell.cellIndex];
    if (!cell) return;
    clearSelectedTableCell();
    cell.classList.add("selected-cell");
  }

  function refreshPdfOverlays() {
    const stage = document.querySelector(".pdf-page-stage");
    if (!stage || !currentPdfContext) return;
    stage.querySelectorAll(".pdf-bbox").forEach((item) => item.remove());
    const overlay = pdfOverlayHtml(currentPdfContext.currentPage || currentPdfContext.sourcePage || 1);
    if (overlay) stage.insertAdjacentHTML("beforeend", overlay);
  }

  function selectEditableTableCell(cell) {
    if (!cell || !currentSourceContext) return;
    const position = selectedCellPosition(cell);
    if (!position) return;
    currentSourceContext.selectedCell = {
      rowIndex: position.rowIndex,
      cellIndex: position.cellIndex,
      text: cell.textContent || "",
    };
    clearSelectedTableCell();
    cell.classList.add("selected-cell");
    if (currentPdfContext) {
      currentPdfContext.selectedTrace = traceForSelectedCell(cell);
      if (currentPdfContext.selectedTrace && Number(currentPdfContext.currentPage) !== Number(currentPdfContext.selectedTrace.pageNumber)) {
        updatePdfPageViewer(currentPdfContext.selectedTrace.pageNumber);
      } else {
        refreshPdfOverlays();
      }
    }
  }

  function renderPageBlock(block) {
    const type = block && block.type ? block.type : "unknown";
    if (type === "table") {
      const label = block.table_index ? "表 " + block.table_index : "表格块";
      const tags = []
        .concat(block.heading ? [block.heading] : [])
        .concat(block.matched_financial_names || [])
        .filter(Boolean)
        .slice(0, 3)
        .map((item) => '<span class="page-block-tag">' + escapeHtml(item) + "</span>")
        .join("");
      const action = block.table_index
        ? '<button class="trace-btn page-block-open-btn" data-page-table-index="' +
          escapeHtml(String(block.table_index)) +
          '">打开该表</button>'
        : "";
      return (
        '<section class="page-block page-block-table ' +
        (block.is_focus_table ? "focus-table" : "") +
        '"><div class="page-block-head"><div><span class="page-block-type">' +
        escapeHtml(label) +
        '</span><span class="page-block-meta">' +
        escapeHtml(pageBlockBbox(block) || "表格解析块") +
        "</span></div>" +
        action +
        '</div><div class="page-block-tag-row">' +
        tags +
        '</div><div class="rendered-table-wrap page-table-wrap">' +
        (block.table_html
          ? sanitizeTableHtml(block.table_html)
          : '<div class="quality-muted">这一页识别到了表格区域，但没有可用的表格 HTML。</div>') +
        "</div></section>"
      );
    }
    if (type === "list") {
      const items = (block.list_items || [])
        .map((item) => "<li>" + escapeHtml(item || "") + "</li>")
        .join("");
      return (
        '<section class="page-block"><div class="page-block-head"><div><span class="page-block-type">列表</span><span class="page-block-meta">' +
        escapeHtml(pageBlockBbox(block) || "列表解析块") +
        '</span></div></div><ul class="page-block-list">' +
        items +
        "</ul></section>"
      );
    }
    if (type === "image") {
      return (
        '<section class="page-block page-block-muted"><div class="page-block-head"><div><span class="page-block-type">图片</span><span class="page-block-meta">' +
        escapeHtml(pageBlockBbox(block) || "图片解析块") +
        '</span></div></div><div class="page-block-text quality-muted">来源图像：' +
        escapeHtml(block.image_path || "未提供路径") +
        "</div></section>"
      );
    }
    const text = block.text || "";
    const headingLike = type === "header" || Number(block.text_level || 0) > 0;
    return (
      '<section class="page-block ' +
      (type === "page_number" || type === "header" ? "page-block-muted" : "") +
      '"><div class="page-block-head"><div><span class="page-block-type">' +
      escapeHtml(type === "header" ? "页眉" : type === "page_number" ? "页码" : headingLike ? "标题" : "文本") +
      '</span><span class="page-block-meta">' +
      escapeHtml(pageBlockBbox(block) || "文本解析块") +
      '</span></div></div><div class="page-block-text ' +
      (headingLike ? "page-block-heading" : "") +
      '">' +
      escapeHtml(text || " ") +
      "</div></section>"
    );
  }

  function renderPageReading(pageData) {
    if (!pageData) {
      return '<div class="quality-muted page-reading-empty">正在加载当前页解析内容...</div>';
    }
    const pageTables = pageData.page_tables || [];
    const pageTableHtml = pageTables.length
      ? pageTables
          .map((item) => {
            return (
              '<button class="quality-chip trace-chip page-table-chip" data-page-table-index="' +
              escapeHtml(String(item.table_index)) +
              '">' +
              escapeHtml(
                "表 " +
                  item.table_index +
                  ((item.matched_financial_names || []).length ? " · " + item.matched_financial_names.join("、") : "")
              ) +
              "</button>"
            );
          })
          .join("")
      : '<span class="quality-muted">这一页没有可定位的表格。</span>';
    const blocks = (pageData.blocks || []).map((block) => renderPageBlock(block)).join("");
    return (
      '<div class="page-reading-view"><div class="page-reading-summary"><div><strong>PDF 第 ' +
      escapeHtml(String(pageData.page_number || currentPdfContext.currentPage || 1)) +
      '</strong><span>' +
      escapeHtml(
        String(pageData.block_count || 0) + " 个解析块 / " + String(pageData.table_count || 0) + " 张表"
      ) +
      '</span></div><div class="quality-chip-row">' +
      pageTableHtml +
      "</div></div>" +
      (blocks || '<div class="quality-muted page-reading-empty">这一页没有可展示的解析内容。</div>') +
      "</div>"
    );
  }

  function updateReadingModeUi() {
    const mode = currentSourceContext && currentSourceContext.readingMode ? currentSourceContext.readingMode : "page";
    const tableBtn = $("readingModeTable");
    const pageBtn = $("readingModePage");
    if (tableBtn) tableBtn.classList.toggle("active", mode === "table");
    if (pageBtn) pageBtn.classList.toggle("active", mode === "page");
    const hint = $("readingModeHint");
    if (!hint) return;
    if (mode === "table") {
      hint.textContent = "当前表格模式：便于直接编辑并同步到下方修正文本。";
      return;
    }
    const currentPage = currentPdfContext && currentPdfContext.currentPage ? currentPdfContext.currentPage : "--";
    hint.textContent = "当前PDF页模式：阅读视图随 PDF 翻页同步显示第 " + currentPage + " 页解析内容。";
  }

  async function loadPageContent(pageNumber) {
    if (!currentTaskId || !currentSourceContext) return null;
    const normalizedPage = Number(pageNumber || 1);
    currentSourceContext.pageCache = currentSourceContext.pageCache || {};
    if (currentSourceContext.pageCache[normalizedPage]) {
      return currentSourceContext.pageCache[normalizedPage];
    }
    const res = await fetch(
      "/api/source/" +
        encodeURIComponent(currentTaskId) +
        "/page/" +
        encodeURIComponent(String(normalizedPage)) +
        "?focus_table=" +
        encodeURIComponent(String(currentSourceContext.selectedTableIndex || ""))
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "页内容加载失败");
    currentSourceContext.pageCache[normalizedPage] = data;
    return data;
  }

  function bindPageReadingActions() {
    const readingBody = $("readingPaneBody");
    if (!readingBody) return;
    readingBody.querySelectorAll("[data-page-table-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const nextIndex = Number(button.dataset.pageTableIndex || 0);
        if (!nextIndex || (currentSourceContext && nextIndex === currentSourceContext.selectedTableIndex)) {
          return;
        }
        showTableSource(nextIndex);
      });
    });
  }

  async function renderReadingPane() {
    const readingBody = $("readingPaneBody");
    if (!readingBody || !currentSourceContext) return;
    const mode = currentSourceContext.readingMode || "page";
    updateReadingModeUi();
    if (mode === "table") {
      const editableTable = makeEditableTableHtml(currentSourceContext.correctionText || currentSourceContext.tableHtml || "");
      readingBody.innerHTML =
        '<div class="rendered-table-wrap editable-table-wrap" id="editableTableWrap">' +
        (editableTable || '<div class="quality-muted page-reading-empty">未找到可渲染表格 HTML</div>') +
        "</div>";
      bindEditableTable();
      return;
    }

    const pageNumber = currentPdfContext && currentPdfContext.currentPage ? currentPdfContext.currentPage : currentSourceContext.sourcePage;
    const cached = currentSourceContext.pageCache && currentSourceContext.pageCache[pageNumber];
    readingBody.innerHTML = renderPageReading(cached);
    bindPageReadingActions();
    if (cached) return;
    try {
      const pageData = await loadPageContent(pageNumber);
      if (!currentSourceContext || currentSourceContext.readingMode !== "page") return;
      const activePage = currentPdfContext && currentPdfContext.currentPage ? currentPdfContext.currentPage : pageNumber;
      if (Number(activePage) !== Number(pageNumber)) return;
      readingBody.innerHTML = renderPageReading(pageData);
      bindPageReadingActions();
      updateReadingModeUi();
    } catch (error) {
      readingBody.innerHTML = '<div class="quality-muted page-reading-empty">' + escapeHtml(error.message || "页内容加载失败") + "</div>";
    }
  }

  function bindReadingModeSwitch() {
    const tableBtn = $("readingModeTable");
    const pageBtn = $("readingModePage");
    if (tableBtn) {
      tableBtn.addEventListener("click", () => {
        if (!currentSourceContext) return;
        currentSourceContext.readingMode = "table";
        renderReadingPane();
      });
    }
    if (pageBtn) {
      pageBtn.addEventListener("click", () => {
        if (!currentSourceContext) return;
        currentSourceContext.readingMode = "page";
        renderReadingPane();
      });
    }
  }

  function pdfOverlayHtml(pageNumber) {
    if (!currentPdfContext) return "";
    const extent = currentPdfContext.bboxExtent || {};
    if (!(extent.width && extent.height)) {
      return "";
    }

    function overlayBox(bbox, className, title) {
      const normalized = normalizeBboxScale(parseBbox(bbox));
      if (!normalized || normalized.length !== 4) return "";
      const left = Math.max(0, Math.min(100, (Number(normalized[0]) / Number(extent.width)) * 100));
      const top = Math.max(0, Math.min(100, (Number(normalized[1]) / Number(extent.height)) * 100));
      const right = Math.max(0, Math.min(100, (Number(normalized[2]) / Number(extent.width)) * 100));
      const bottom = Math.max(0, Math.min(100, (Number(normalized[3]) / Number(extent.height)) * 100));
      return (
        '<div class="pdf-bbox ' +
        escapeHtml(className || "") +
        '" title="' +
        escapeHtml(title || "") +
        '" style="left:' +
        left +
        "%;top:" +
        top +
        "%;width:" +
        Math.max(0, right - left) +
        "%;height:" +
        Math.max(0, bottom - top) +
        '%;"></div>'
      );
    }

    let html = "";
    if (Number(pageNumber) === Number(currentPdfContext.sourcePage)) {
      html += overlayBox(currentPdfContext.bbox || [], "pdf-bbox-table", "表格区域");
    }
    const trace = currentPdfContext.selectedTrace;
    if (trace && Number(pageNumber) === Number(trace.pageNumber)) {
      html += overlayBox(
        trace.bbox || [],
        trace.source === "text_anchor" ? "pdf-bbox-text" : "pdf-bbox-selected",
        trace.source === "cell_bbox" ? "单元格区域" : trace.source === "text_anchor" ? "文本锚定区域" : "选中区域"
      );
    }
    return html;
  }

  function renderPdfPagePanel(pageImage) {
    if (!pageImage || !pageImage.url) {
      return '<div class="quality-muted">未识别 PDF 页码，无法展示原页。</div>';
    }
    const currentPage = Number(pageImage.page_number || 1);
    const pageCount = Number(pageImage.page_count || currentPage || 1);
    return (
      '<div class="pdf-page-viewer" data-zoom="fit"><div class="pdf-page-toolbar"><div class="pdf-page-topline"><span id="pdfPageLabel">PDF 第 ' +
      escapeHtml(String(currentPage)) +
      " / " +
      escapeHtml(String(pageCount)) +
      ' 页</span><div class="pdf-page-nav"><button class="pdf-nav-btn" id="pdfPrevBtn">上一页</button><input id="pdfPageInput" class="pdf-page-input" type="number" min="1" max="' +
      escapeHtml(String(pageCount)) +
      '" value="' +
      escapeHtml(String(currentPage)) +
      '"><button class="pdf-nav-btn" id="pdfNextBtn">下一页</button></div></div><div class="pdf-zoom-controls" aria-label="PDF 缩放"><button class="pdf-zoom-btn active" data-pdf-zoom="fit">适应宽度</button><button class="pdf-zoom-btn" data-pdf-zoom="1">100%</button><button class="pdf-zoom-btn" data-pdf-zoom="1.5">150%</button><button class="pdf-zoom-btn" data-pdf-zoom="2">200%</button></div><a id="pdfPageOpenLink" href="' +
      escapeHtml(pageImage.url) +
      '" target="_blank" rel="noopener">打开原页图片</a></div><div class="pdf-page-canvas"><div class="pdf-page-stage"><img id="pdfPageImage" src="' +
      escapeHtml(pageImage.url) +
      '" alt="PDF page preview">' +
      pdfOverlayHtml(currentPage) +
      "</div></div></div>"
    );
  }

  function updatePdfPageViewer(pageNumber) {
    if (!currentPdfContext) return;
    const pageCount = Number(currentPdfContext.pageCount || 1);
    const nextPage = Math.max(1, Math.min(pageCount, Number(pageNumber) || currentPdfContext.currentPage || 1));
    currentPdfContext.currentPage = nextPage;
    const imageUrl = "/api/pdf_page/" + encodeURIComponent(currentTaskId) + "/" + encodeURIComponent(String(nextPage));
    if ($("pdfPageImage")) $("pdfPageImage").src = imageUrl;
    if ($("pdfPageOpenLink")) $("pdfPageOpenLink").href = imageUrl;
    if ($("pdfPageInput")) $("pdfPageInput").value = String(nextPage);
    if ($("pdfPageLabel")) $("pdfPageLabel").textContent = "PDF 第 " + nextPage + " / " + pageCount + " 页";
    const stage = document.querySelector(".pdf-page-stage");
    if (stage) {
      refreshPdfOverlays();
    }
    if ($("pdfPrevBtn")) $("pdfPrevBtn").disabled = nextPage <= 1;
    if ($("pdfNextBtn")) $("pdfNextBtn").disabled = nextPage >= pageCount;
    if (currentSourceContext && currentSourceContext.readingMode === "page") {
      renderReadingPane();
    } else {
      updateReadingModeUi();
    }
  }

  function bindPdfPageNavigation() {
    if (!currentPdfContext) return;
    const prevBtn = $("pdfPrevBtn");
    const nextBtn = $("pdfNextBtn");
    const pageInput = $("pdfPageInput");
    if (prevBtn) {
      prevBtn.addEventListener("click", () => updatePdfPageViewer((currentPdfContext.currentPage || 1) - 1));
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", () => updatePdfPageViewer((currentPdfContext.currentPage || 1) + 1));
    }
    if (pageInput) {
      pageInput.addEventListener("change", () => updatePdfPageViewer(pageInput.value));
      pageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          updatePdfPageViewer(pageInput.value);
        }
      });
    }
    updatePdfPageViewer(currentPdfContext.currentPage || currentPdfContext.sourcePage || 1);
  }

  async function showTableSource(tableIndex, line) {
    if (!currentTaskId || !tableIndex) return;
    focusMarkdownLine(Number(line));
    try {
      const res = await fetch(
        "/api/source/" + encodeURIComponent(currentTaskId) + "/table/" + encodeURIComponent(tableIndex)
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "溯源失败");
      const table = data.table || {};
      const renderedTable = sanitizeTableHtml(data.table_html || "");
      const correction = data.correction || {};
      const correctionText = correction.table_markdown || data.table_html || "";
      currentPdfContext = data.pdf_page_image && data.pdf_page_image.url
        ? {
            sourcePage: Number(data.pdf_page_image.page_number || 1),
            currentPage: Number(data.pdf_page_image.page_number || 1),
            pageCount: Number(data.pdf_page_image.page_count || data.pdf_page_image.page_number || 1),
            bbox: data.pdf_page_image.bbox || [],
            bboxExtent: data.pdf_page_image.bbox_extent || {},
            selectedTrace: null,
          }
        : null;
      currentSourceContext = {
        selectedTableIndex: Number(table.table_index || tableIndex || 0),
        sourcePage: Number((data.pdf_page_image && data.pdf_page_image.page_number) || table.pdf_page_number || 1),
        readingMode: "page",
        tableHtml: renderedTable,
        correctionText: correctionText,
        selectedCell: null,
        pageCache: {},
      };
      if (data.page_content && data.page_content.page_number) {
        currentSourceContext.pageCache[data.page_content.page_number] = data.page_content;
      }
      const correctionNote = correction.note || "";
      const correctionStatus = correction.review_status || "unreviewed";
      const statusOptions = [
        ["unreviewed", "未复核"],
        ["correct", "确认无误"],
        ["needs_fix", "需要修正"],
        ["fixed", "已修正"],
        ["ignored", "忽略"],
      ].map((item) => {
        return (
          '<option value="' +
          item[0] +
          '"' +
          (correctionStatus === item[0] ? " selected" : "") +
          ">" +
          item[1] +
          "</option>"
        );
      }).join("");
      const excerpt = (data.markdown_excerpt || [])
        .map((item) => {
          return (
            '<div class="source-line ' +
            (item.focus ? "focus" : "") +
            '"><span>' +
            escapeHtml(String(item.line)) +
            '</span><code>' +
            escapeHtml(item.text || " ") +
            "</code></div>"
          );
        })
        .join("");
      const artifacts = data.artifacts || {};
      const artifactHtml = Object.keys(artifacts)
        .map((name) => {
          const item = artifacts[name] || {};
          const label = item.exists && item.url
            ? '<a class="trace-btn" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener">打开</a>'
            : "";
          return (
            '<div class="artifact-row ' +
            (item.exists ? "ok" : "missing") +
            '"><span>' +
            escapeHtml(name) +
            "</span><code>" +
            escapeHtml(item.path || "未生成") +
            "</code>" +
            label +
            "</div>"
          );
        })
        .join("");
      $("sourceBody").innerHTML =
        '<div class="source-summary">' +
        '<div><strong>表 ' +
        escapeHtml(String(table.table_index || tableIndex)) +
        '</strong><span>Markdown 行 ' +
        escapeHtml(String(table.line || line || "-")) +
        "</span></div>" +
        '<div><strong>' +
        escapeHtml(String(table.rows || 0)) +
        '</strong><span>行</span></div>' +
        '<div><strong>' +
        escapeHtml(table.pdf_page_number ? String(table.pdf_page_number) : "--") +
        '</strong><span>PDF 页码' +
        (table.pdf_page_source === "markdown_marker_inferred" ? "（推断）" : "") +
        "</span></div>" +
        '<div><strong>' +
        escapeHtml(String(table.cells || 0)) +
        '</strong><span>单元格</span></div>' +
        '<div><strong>' +
        escapeHtml(String(Math.round((table.empty_ratio || 0) * 1000) / 10)) +
        '%</strong><span>空单元格</span></div>' +
        '<div><strong>' +
        escapeHtml(String(Math.round((table.numeric_ratio || 0) * 1000) / 10)) +
        '%</strong><span>数字密度</span></div>' +
        "</div>" +
        '<div class="source-meta"><span>附近标题</span><b>' +
        escapeHtml(table.heading || "未识别") +
        "</b></div>" +
        '<div class="source-meta"><span>单位</span><b>' +
        escapeHtml(table.unit || "未识别") +
        "</b></div>" +
        '<div class="source-meta"><span>命中类别</span><b>' +
        escapeHtml((table.matched_financial_names || []).join("、") || "普通表") +
        "</b></div>" +
        '<div class="source-meta"><span>PDF 坐标 bbox</span><b>' +
        escapeHtml((table.bbox || []).join(", ") || "未识别") +
        "</b></div>" +
        '<div class="source-meta"><span>页面截图</span><b>' +
        escapeHtml(table.source_image_path || "未识别") +
        "</b></div>" +
        '<div class="source-workbench"><div class="source-block source-pane"><div class="source-pane-head"><div class="reading-pane-topline"><h4>阅读视图</h4><div class="reading-mode-switch"><button class="reading-mode-btn" id="readingModePage">当前PDF页</button><button class="reading-mode-btn" id="readingModeTable">当前表格</button></div></div><div class="quality-muted edit-hint" id="readingModeHint"></div></div><div class="source-reading-body" id="readingPaneBody"></div></div>' +
        '<div class="source-block source-pane"><div class="source-pane-head"><h4>PDF 原页</h4><div class="quality-muted edit-hint">支持上下翻页与缩放，定位框仅显示在来源页。</div></div>' +
        renderPdfPagePanel(data.pdf_page_image) +
        "</div></div>" +
        '<div class="source-block"><h4>人工复核修正</h4>' +
        '<div class="correction-toolbar"><label>状态 <select id="correctionStatus">' +
        statusOptions +
        '</select></label><button class="trace-btn" id="saveCorrectionBtn">保存修正</button><span id="correctionSaved" class="quality-muted">' +
        escapeHtml(correction.updated_at ? "上次保存: " + correction.updated_at : "") +
        "</span></div>" +
        '<textarea id="correctionText" class="correction-editor" spellcheck="false">' +
        escapeHtml(correctionText) +
        "</textarea>" +
        '<textarea id="correctionNote" class="correction-note" placeholder="复核备注，例如：第 3 列金额错位，应以 PDF 第 67 页为准。">' +
        escapeHtml(correctionNote) +
        "</textarea></div>" +
        '<div class="source-block"><h4>Markdown 上下文</h4>' +
        excerpt +
        "</div>" +
        '<div class="source-block"><h4>产物文件</h4>' +
        artifactHtml +
        "</div>";
      $("sourceCard").style.display = "block";
      bindCorrectionEditor(table.table_index || tableIndex);
      bindReadingModeSwitch();
      renderReadingPane();
      bindPdfZoomControls();
      bindPdfPageNavigation();
      $("sourceCard").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      showError(error.message || "溯源失败");
    }
  }

  function bindEditableTable() {
    const wrap = $("editableTableWrap");
    if (!wrap) return;
    wrap.querySelectorAll("th, td").forEach((cell) => {
      cell.addEventListener("click", () => selectEditableTableCell(cell));
      cell.addEventListener("focus", () => selectEditableTableCell(cell));
      cell.addEventListener("input", syncCorrectionFromEditableTable);
      cell.addEventListener("blur", syncCorrectionFromEditableTable);
    });
    applySelectedCellPosition();
  }

  function bindPdfZoomControls() {
    const viewer = document.querySelector(".pdf-page-viewer");
    if (!viewer) return;
    viewer.querySelectorAll("[data-pdf-zoom]").forEach((button) => {
      button.addEventListener("click", () => {
        const zoom = button.dataset.pdfZoom || "fit";
        viewer.dataset.zoom = zoom;
        viewer.querySelectorAll("[data-pdf-zoom]").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
      });
    });
  }

  function bindCorrectionEditor(tableIndex) {
    const button = $("saveCorrectionBtn");
    const text = $("correctionText");
    if (text) {
      text.addEventListener("input", () => {
        if (currentSourceContext) {
          currentSourceContext.correctionText = text.value;
        }
      });
    }
    if (!button) return;
    button.addEventListener("click", async () => {
      if (!currentTaskId || !tableIndex) return;
      syncCorrectionFromEditableTable();
      button.disabled = true;
      const saved = $("correctionSaved");
      if (saved) saved.textContent = "正在保存...";
      try {
        const res = await fetch(
          "/api/source/" +
            encodeURIComponent(currentTaskId) +
            "/table/" +
            encodeURIComponent(tableIndex) +
            "/correction",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              review_status: $("correctionStatus").value,
              table_markdown: $("correctionText").value,
              note: $("correctionNote").value,
            }),
          }
        );
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "保存失败");
        if (saved) saved.textContent = "已保存: " + (data.correction && data.correction.updated_at ? data.correction.updated_at : "");
        showToast("人工修正已保存");
      } catch (error) {
        showError(error.message || "保存失败");
        if (saved) saved.textContent = "保存失败";
      } finally {
        button.disabled = false;
      }
    });
  }

  function candidateMeta(item) {
    if (!item || item.status === "missing") return "需复核：未在表格中定位";
    if (!item.table_index) {
      return item._source === "financial_data" ? "已抽取，暂无表格定位" : "需复核：暂无表格定位";
    }
    const page =
      item.pdf_page_number
        ? " / PDF " + item.pdf_page_number + "页" + (item.pdf_page_source === "markdown_marker_inferred" ? "(推断)" : "")
        : "";
    const confidenceMap = { high: "高置信", medium: "中置信", low: "低置信" };
    const confidence = item.confidence ? " / " + (confidenceMap[item.confidence] || item.confidence) : "";
    const line = item.line ? " / 行 " + item.line : "";
    return "表 " + item.table_index + line + page + confidence;
  }

  function renderCandidateChip(name, item, extraClass) {
    const candidate = item || {};
    const label = name + " · " + candidateMeta(candidate);
    if (!candidate.table_index || candidate.status === "missing") {
      return '<span class="quality-chip quality-chip-missing">' + escapeHtml(label) + "</span>";
    }
    return (
      '<button class="quality-chip trace-chip ' +
      escapeHtml(extraClass || "") +
      '" data-table-index="' +
      escapeHtml(String(candidate.table_index)) +
      '" data-line="' +
      escapeHtml(String(candidate.line || "")) +
      '">' +
      escapeHtml(label) +
      "</button>"
    );
  }

  function renderCandidateList(candidates, emptyText, extraClass) {
    const items = candidates || [];
    if (!items.length) return '<span class="quality-muted">' + escapeHtml(emptyText) + "</span>";
    return items.map((item) => renderCandidateChip(item.name || "候选表", item, extraClass)).join("");
  }

  function formatSuspectReasons(reasons) {
    const map = {
      single_row: "单行/空壳",
      many_empty_cells: "空单元格偏多",
      low_numeric_density: "数字密度偏低",
      key_table_too_short: "关键表过短",
      low_confidence_core_candidate: "低置信核心候选",
      medium_confidence_core_candidate: "中置信核心候选",
    };
    return (reasons || []).map((reason) => map[reason] || reason).join("、");
  }

  function showQuality(quality) {
    if (!quality) return;
    const warnings = quality.warnings && quality.warnings.length
      ? quality.warnings.map((item) => "<li>" + escapeHtml(item) + "</li>").join("")
      : "<li>未发现明显质量告警</li>";
    const keyCandidates = quality.key_table_candidates || {};
    const fallbackKeyHtml = Object.keys(keyCandidates).length
      ? Object.keys(keyCandidates).slice(0, 8).map((name) => {
          const first = keyCandidates[name] && keyCandidates[name][0];
          if (!first) {
            return '<span class="quality-chip">' + escapeHtml(name) + "</span>";
          }
          return renderCandidateChip(name, first);
        }).join("")
      : '<span class="quality-muted">未定位到候选表</span>';
    const coreCandidates = quality.core_financial_table_candidates || [];
    const indicatorCandidates = (quality.indicator_table_candidates || []).filter((item) => item.status === "found");
    const coreFound = coreCandidates.filter((item) => item.status === "found");
    const keyHtml = coreCandidates.length
      ? renderCandidateList(coreCandidates, "未定位到核心候选表")
      : fallbackKeyHtml;
    const indicatorHtml = indicatorCandidates.length
      ? renderCandidateList(indicatorCandidates, "未定位到指标候选表", "quality-chip-secondary")
      : '<span class="quality-muted">未定位到指标/经营分析候选表</span>';
    const suspicious = quality.suspicious_tables || [];
    const suspiciousHtml = suspicious.length
      ? suspicious.map((item) => {
          return (
            '<li>' +
            tableButton(item, "打开表") +
            " · " +
            escapeHtml(formatSuspectReasons(item.suspect_reasons || [])) +
            "</li>"
          );
        }).join("")
      : "<li>未发现可疑表样本</li>";
    $("qualityBody").innerHTML =
      '<div class="quality-grid">' +
      '<div><strong>' + escapeHtml(String(quality.table_count || 0)) + '</strong><span>表格</span></div>' +
      '<div><strong>' + escapeHtml(String(quality.single_row_table_count || 0)) + '</strong><span>单行/空壳表</span></div>' +
      '<div><strong>' + escapeHtml(String(Math.round((quality.single_row_table_ratio || 0) * 1000) / 10)) + '%</strong><span>空壳比例</span></div>' +
      '<div><strong>' + escapeHtml(String(quality.image_ref_count || 0)) + '</strong><span>图片引用</span></div>' +
      '<div><strong>' + escapeHtml(String(suspicious.length)) + '</strong><span>可疑表样本</span></div>' +
      "</div>" +
      '<div class="quality-row"><span>核心章节</span><b>' +
      escapeHtml((quality.found_sections || []).length + "/" + ((quality.found_sections || []).length + (quality.missing_sections || []).length)) +
      '</b></div>' +
      '<div class="quality-row"><span>财报核心表</span><b>' +
      escapeHtml(
        coreCandidates.length
          ? coreFound.length + "/" + coreCandidates.length + " · " + (coreFound.map((item) => item.name).join("、") || "未识别")
          : (quality.found_financial_tables || []).join("、") || "未识别"
      ) +
      '</b></div>' +
      '<div class="quality-section"><div class="quality-section-title">关键表候选</div><div class="quality-chip-row">' + keyHtml + "</div></div>" +
      '<div class="quality-section"><div class="quality-section-title">指标/经营分析候选</div><div class="quality-chip-row">' + indicatorHtml + "</div></div>" +
      '<div class="quality-section"><div class="quality-section-title">优先复核表</div><ul class="quality-suspects">' + suspiciousHtml + "</ul></div>" +
      '<ul class="quality-warnings">' + warnings + "</ul>";
    $("qualityCard").style.display = "block";
    $("qualityBody").querySelectorAll("[data-table-index]").forEach((button) => {
      button.addEventListener("click", () => {
        showTableSource(button.dataset.tableIndex, button.dataset.line);
      });
    });
  }

  async function fetchQuality() {
    if (!currentTaskId) return;
    try {
      const res = await fetch("/api/quality/" + encodeURIComponent(currentTaskId));
      const data = await res.json();
      if (data.quality) {
        showQuality(data.quality);
      }
    } catch (error) {
      console.error("Fetch quality error:", error);
    }
  }

  function financialStatusText(status) {
    if (status === "pass") return "通过";
    if (status === "fail") return "存在异常";
    if (status === "error") return "生成失败";
    return "未生成";
  }

  function scopeName(scope) {
    if (scope === "consolidated") return "合并";
    if (scope === "parent_company") return "母公司";
    return scope || "--";
  }

  function showFinancial(payload) {
    const data = payload && payload.financial_data;
    const checks = payload && payload.financial_checks;
    if (!data || !checks) return;
    const summary = checks.summary || {};
    const dataSummary = data.summary || {};
    const failures = (checks.checks || []).filter((item) => item.status === "fail").slice(0, 8);
    const warnings = (checks.warnings || []).slice(0, 8);
    const status = checks.overall_status || "skipped";
    const statementCount = dataSummary.statement_count || (data.statements || []).length || 0;
    const keyMetricCount = dataSummary.key_metric_count || (data.key_metrics || []).length || 0;
    const scopes = (dataSummary.scopes || []).map(scopeName).join("、") || "--";
    const failureHtml = failures.length
      ? failures
          .map((item) => {
            const diff = item.diff === undefined ? "" : " · 差异 " + formatFinancialNumber(item.diff);
            const tolerance = item.tolerance === undefined ? "" : " / 容差 " + formatFinancialNumber(item.tolerance);
            return (
              "<li><b>" +
              escapeHtml(item.rule_name || item.rule_id || "校验失败") +
              "</b> · " +
              escapeHtml(scopeName(item.scope)) +
              " · " +
              escapeHtml(item.period || "--") +
              escapeHtml(diff + tolerance) +
              "</li>"
            );
          })
          .join("")
      : "<li>未发现失败项</li>";
    const warningHtml = warnings.length
      ? warnings.map((item) => "<li>" + escapeHtml(item) + "</li>").join("")
      : "<li>无额外提示</li>";
    const jsonLink = currentTaskId
      ? '<a class="trace-btn" href="/api/financial/' + encodeURIComponent(currentTaskId) + '" target="_blank" rel="noopener">打开 JSON</a>'
      : "";

    $("financialBody").innerHTML =
      '<div class="quality-grid">' +
      '<div><strong>' + escapeHtml(financialStatusText(status)) + '</strong><span>整体状态</span></div>' +
      '<div><strong>' + escapeHtml(String(summary.pass || 0)) + '</strong><span>通过</span></div>' +
      '<div><strong>' + escapeHtml(String(summary.fail || 0)) + '</strong><span>失败</span></div>' +
      '<div><strong>' + escapeHtml(String(summary.skipped || 0)) + '</strong><span>跳过</span></div>' +
      '<div><strong>' + escapeHtml(String(statementCount)) + '</strong><span>结构化报表</span></div>' +
      "</div>" +
      '<div class="quality-row"><span>识别范围</span><b>' + escapeHtml(scopes) + '</b></div>' +
      '<div class="quality-row"><span>关键指标</span><b>' + escapeHtml(String(keyMetricCount)) + '</b></div>' +
      '<div class="quality-row"><span>报告年份</span><b>' + escapeHtml(String(data.report_year || "--")) + '</b></div>' +
      '<div class="quality-section"><div class="quality-section-title">失败项</div><ul class="quality-suspects">' + failureHtml + "</ul></div>" +
      '<div class="quality-section"><div class="quality-section-title">提示</div><ul class="quality-warnings">' + warningHtml + "</ul></div>" +
      '<div class="quality-section"><div class="quality-chip-row">' + jsonLink + "</div></div>";
    $("financialCard").style.display = "block";
  }

  function showArtifacts(artifacts) {
    const body = $("artifactBody");
    if (!body) return;
    const artifactNames = Object.keys(artifacts || {});
    if (!artifactNames.length) {
      $("artifactCard").style.display = "none";
      return;
    }
    body.innerHTML = artifactNames
      .map((name) => {
        const item = artifacts[name] || {};
        const label = item.exists && item.url
          ? '<a class="trace-btn" href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener">打开</a>'
          : '<span class="quality-muted">未生成</span>';
        return (
          '<div class="artifact-row ' +
          (item.exists ? "ok" : "missing") +
          '"><span>' +
          escapeHtml(name) +
          "</span><code>" +
          escapeHtml(item.path || "未生成") +
          "</code>" +
          label +
          "</div>"
        );
      })
      .join("");
    $("artifactCard").style.display = "block";
  }

  async function fetchFinancial() {
    if (!currentTaskId) return;
    try {
      const res = await fetch("/api/financial/" + encodeURIComponent(currentTaskId));
      const data = await res.json();
      if (data.financial_checks) {
        showFinancial(data);
      }
    } catch (error) {
      console.error("Fetch financial error:", error);
    }
  }

  async function fetchResult() {
    if (!currentTaskId) return;
    try {
      const res = await fetch("/api/result/" + encodeURIComponent(currentTaskId));
      const data = await res.json();
      showArtifacts(data.artifacts || {});
      if (data.markdown) {
        showMarkdown(data.markdown);
        fetchQuality();
        fetchFinancial();
      }
    } catch (error) {
      console.error("Fetch result error:", error);
    }
  }

  async function loadTasks() {
    try {
      const res = await fetch("/api/tasks");
      const data = await res.json();
      const list = $("taskList");
      if (!data.tasks || data.tasks.length === 0) {
        $("taskListCard").style.display = "none";
        return;
      }
      $("taskListCard").style.display = "block";
      list.innerHTML = data.tasks
        .map((task) => {
          let badgeClass = "pending";
          if (task.status === "completed" || task.status === "success" || task.status === "done") {
            badgeClass = "completed";
          } else if (task.status === "processing") {
            badgeClass = "processing";
          } else if (task.status === "failed" || task.status === "error" || task.status === "completed_missing_artifact") {
            badgeClass = "failed";
          } else if (task.status === "cancelled") {
            badgeClass = "cancelled";
          }
          const canRefetch = task.status === "completed" || task.status === "completed_missing_artifact";
          const canReparse = isTerminalStatus(task.status);
          const actionButtons =
            (canRefetch
              ? '<button class="task-action task-refetch" data-task-id="' +
                escapeHtml(task.task_id) +
                '">补拉</button>'
              : "") +
            (canReparse
              ? '<button class="task-action task-reparse" data-task-id="' +
                escapeHtml(task.task_id) +
                '">重跑</button>'
              : "");
          return (
            '<div class="task-item" data-task-id="' +
            escapeHtml(task.task_id) +
            '" data-status="' +
            escapeHtml(task.status) +
            '" data-filename="' +
            escapeHtml(task.filename) +
            '"><span class="task-name">' +
            escapeHtml(task.filename) +
            '</span><div class="task-meta"><span class="status-badge ' +
            badgeClass +
            '">' +
            translateStatus(task.status) +
            '</span><span style="color:var(--text-muted);font-size:0.8rem;">' +
            escapeHtml(
              task.local_queue_position ? "本地队列第 " + task.local_queue_position + " 位" : ""
            ) +
            '</span><span style="color:var(--text-muted);font-size:0.8rem;">' +
            new Date(task.created_at).toLocaleString("zh-CN") +
            '</span><button class="task-delete" data-task-id="' +
            escapeHtml(task.task_id) +
            '" data-task-status="' +
            escapeHtml(task.status) +
            '">删除</button>' +
            actionButtons +
            "</div></div>"
          );
        })
        .join("");
      list.querySelectorAll(".task-item").forEach((item) => {
        item.addEventListener("click", () => {
          resumeTask(item.dataset.taskId, item.dataset.filename, item.dataset.status);
        });
      });
      list.querySelectorAll(".task-delete").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await deleteTask(button.dataset.taskId, button.dataset.taskStatus);
        });
      });
      list.querySelectorAll(".task-refetch").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await refetchTask(button.dataset.taskId);
        });
      });
      list.querySelectorAll(".task-reparse").forEach((button) => {
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          await reparseTask(button.dataset.taskId);
        });
      });
      const latestCompletedTask = data.tasks.find(
        (task) =>
          task.markdown_ready &&
          ["completed", "success", "done", "finished"].includes(task.status)
      );
      const latestActiveTask =
        data.tasks.find((task) => ["processing", "pending", "submitted", "submitting"].includes(task.status)) ||
        data.tasks.find((task) => task.status === "queued") ||
        data.tasks.find((task) => !isTerminalStatus(task.status));
      const taskToOpen = latestCompletedTask || latestActiveTask;
      if (taskToOpen && !currentTaskId) {
        resumeTask(taskToOpen.task_id, taskToOpen.filename, taskToOpen.status);
      }
    } catch (error) {
      console.error("Load tasks error:", error);
    }
  }

  async function resumeTask(taskId, filename, status) {
    if (!taskId) return;
    currentTaskId = taskId;
    currentFile = null;
    isCancelled = false;
    markdownContent = "";
    logCount = 0;
    $("fileName").textContent = filename ? filename + "（最近任务）" : "";
    $("clearBtn").style.display = "inline-flex";
    $("cancelBtn").style.display = isTerminalStatus(status) ? "none" : "inline-flex";
    $("uploadStage").classList.remove("active");
    $("parseStage").classList.add("active");
    $("logCard").style.display = "block";
    $("artifactCard").style.display = "none";
    $("qualityCard").style.display = "none";
    $("financialCard").style.display = "none";
    $("sourceCard").style.display = "none";
    $("previewArea").classList.remove("active");
    $("logPanel").innerHTML = "";
    hideError();
    $("convertBtn").disabled = false;
    $("btnSpinner").style.display = "none";
    $("btnText").textContent = "批量入队";
    setParseProgress(0, "正在恢复任务状态...");

    try {
      if (status === "completed" || status === "success" || status === "done" || status === "finished") {
        $("cancelBtn").style.display = "none";
        $("parseBadge").className = "status-badge completed";
        $("parseBadge").textContent = "已完成";
        setParseProgress(100, "解析完成");
        fetchResult();
        showToast("已恢复任务视图");
        return;
      }
      const latest = await pollStatus();
      const latestStatus = latest && latest.status ? latest.status : status;
      $("cancelBtn").style.display = isTerminalStatus(latestStatus) ? "none" : "inline-flex";
      if (!isTerminalStatus(latestStatus)) {
        startPolling();
      } else if (latestStatus === "completed" || latestStatus === "success" || latestStatus === "done" || latestStatus === "finished") {
        fetchResult();
      }
      showToast("已恢复任务视图");
    } catch (error) {
      console.error("Resume task error:", error);
      showError("恢复任务状态失败");
    }
  }

  async function deleteTask(taskId, status) {
    if (!taskId) return;
    if (!isTerminalStatus(status)) {
      showError("请先停止或等待任务结束后再删除");
      return;
    }
    if (!confirm("确定删除这条最近任务记录吗？")) {
      return;
    }

    try {
      const res = await fetch("/api/tasks/" + encodeURIComponent(taskId), { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "删除失败");

      if (currentTaskId === taskId) {
        resetAll();
      }
      await loadTasks();
      showToast("任务记录已删除");
    } catch (error) {
      console.error("Delete task error:", error);
      showError(error.message || "删除失败");
    }
  }

  async function refetchTask(taskId) {
    if (!taskId) return;
    try {
      const res = await fetch("/api/refetch/" + encodeURIComponent(taskId), { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "重新拉取失败");
      if (currentTaskId === taskId) {
        fetchResult();
      }
      await loadTasks();
      showToast("结果已重新拉取");
    } catch (error) {
      console.error("Refetch task error:", error);
      showError(error.message || "重新拉取失败");
    }
  }

  async function reparseTask(taskId) {
    if (!taskId) return;
    if (!confirm("确定基于原 PDF 创建一个重新解析任务吗？")) {
      return;
    }
    try {
      const res = await fetch("/api/reparse/" + encodeURIComponent(taskId), { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "重新解析失败");
      await loadTasks();
      resumeTask(data.task_id, data.filename, "queued");
      showToast("重新解析任务已入队");
    } catch (error) {
      console.error("Reparse task error:", error);
      showError(error.message || "重新解析失败");
    }
  }

  function updateStatus(data) {
    if (isCancelled) return;

    const stage = data.stage || data.status || "pending";
    const isFailed =
      data.status === "failed" ||
      data.status === "error" ||
      data.status === "failure" ||
      data.status === "completed_missing_artifact";
    const isCompleted =
      data.status === "completed" || data.status === "success" || data.status === "done" || data.status === "finished";
    const badge = $("parseBadge");
    badge.className = "status-badge " + stage;
    badge.textContent = translateStatus(data.status || "pending");

    $("queueInfo").textContent =
      data.local_queue_position
        ? "本地队列位置: 第 " + data.local_queue_position + " 位"
        : data.queue_position !== null && data.queue_position !== undefined
          ? "MinerU 队列前方: " + data.queue_position + " 任务"
          : "";
    $("elapsedInfo").textContent =
      !isFailed && data.elapsed_seconds !== null && data.elapsed_seconds !== undefined
        ? "已耗时: " + formatDuration(data.elapsed_seconds)
        : "";

    if (!isFailed && data.total_pages && data.processed_pages !== null && data.processed_pages !== undefined) {
      const remaining = data.total_pages - data.processed_pages;
      $("pagesInfo").textContent =
        "已完成 " + data.processed_pages + "/" + data.total_pages + " 页, 还剩 " + remaining + " 页";
    } else if (!isFailed && data.total_pages) {
      $("pagesInfo").textContent = "共 " + data.total_pages + " 页";
    } else {
      $("pagesInfo").textContent = "";
    }

    const stageMap = {
      queued: "已加入本地队列",
      uploaded: "文件已上传",
      submitting: "正在提交到 MinerU",
      submitted: "已提交到 MinerU",
      pending: "排队等待中",
      processing: "正在解析 PDF",
      completed: "解析完成",
      completed_missing_artifact: "结果缺失",
      failed: "解析失败",
      cancelled: "已停止查看",
    };
    $("stageInfo").textContent = stageMap[stage] || stage;

    let targetPct = 0;
    if (isCompleted) {
      targetPct = 100;
    } else if (stage === "processing" && data.progress_percent !== null && data.progress_percent !== undefined) {
      targetPct = Math.max(0, Math.min(99, Number(data.progress_percent)));
    } else if (stage === "processing" && data.total_pages && data.processed_pages !== null && data.processed_pages !== undefined) {
      targetPct = Math.round((data.processed_pages / data.total_pages) * 100);
    } else if (stage === "pending") {
      targetPct = 0;
    } else if (stage === "submitted" || stage === "uploaded") {
      targetPct = 0;
    }
    setParseProgress(targetPct, translateStatus(data.status || "pending"));

    appendLogs(data.logs || []);
    logCount = typeof data.log_count === "number" ? data.log_count : logCount + (data.logs || []).length;

    if (isCompleted) {
      setParseProgress(100, "解析完成!");
      $("parseBadge").className = "status-badge completed";
      $("parseBadge").textContent = "已完成";
      $("cancelBtn").style.display = "none";
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      $("convertBtn").disabled = false;
      $("btnSpinner").style.display = "none";
      $("btnText").textContent = "批量入队";
      if (data.markdown_ready) {
        fetchResult();
      } else {
        fetchResult();
      }
      loadTasks();
    } else if (isFailed) {
      setParseProgress(0, data.status === "completed_missing_artifact" ? "结果缺失" : "解析失败");
      $("parseBadge").className = "status-badge failed";
      $("parseBadge").textContent = translateStatus(data.status);
      $("elapsedInfo").textContent = "";
      $("pagesInfo").textContent = "";
      $("cancelBtn").style.display = "none";
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      $("convertBtn").disabled = false;
      $("btnSpinner").style.display = "none";
      $("btnText").textContent = "批量入队";
      showError(data.error || "转换失败");
    } else if (data.status === "cancelled") {
      setParseProgress(0, "已停止查看");
      $("parseBadge").className = "status-badge cancelled";
      $("parseBadge").textContent = "已停止";
      $("cancelBtn").style.display = "none";
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      $("convertBtn").disabled = false;
      $("btnSpinner").style.display = "none";
      $("btnText").textContent = "批量入队";
    }
  }

  function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollStatus, 1000);
    pollStatus();
  }

  async function pollStatus() {
    if (!currentTaskId || isCancelled) return;
    try {
      const url = "/api/status/" + encodeURIComponent(currentTaskId) + "?since=" + encodeURIComponent(logCount);
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "状态查询失败");
      updateStatus(data);
      return data;
    } catch (error) {
      console.error("Poll error:", error);
      if ($("parseBadge").textContent !== "失败") {
        $("parseStatus").textContent = "状态查询失败，正在重试...";
      }
      throw error;
    }
  }

  async function startConvert() {
    if (!selectedFiles.length) return;
    await checkHealth();
    hideError();
    $("convertBtn").disabled = true;
    $("btnSpinner").style.display = "inline-block";
    $("btnText").textContent = "入队中...";
    isCancelled = false;
    logCount = 0;
    $("logPanel").innerHTML = "";

    $("uploadStage").classList.add("active");
    $("parseStage").classList.remove("active");
    $("logCard").style.display = "none";
    $("previewArea").classList.remove("active");
    $("cancelBtn").style.display = "inline-flex";
    setUploadProgress(0, "准备上传...");

    const form = new FormData();
    selectedFiles.forEach((file) => form.append("files", file));
    form.append("backend", $("backend").value);
    form.append("parse_method", $("parseMethod").value);
    form.append("start_page_id", $("startPage").value);
    form.append("end_page_id", $("endPage").value);
    form.append("formula_enable", $("formulaEnable").checked ? "true" : "false");
    form.append("table_enable", $("tableEnable").checked ? "true" : "false");

    let uploadPct = 0;
    const uploadTimer = setInterval(() => {
      if (isCancelled) {
        clearInterval(uploadTimer);
        return;
      }
      uploadPct += Math.random() * 15;
      if (uploadPct > 90) uploadPct = 90;
      setUploadProgress(uploadPct, "正在上传并加入本地队列...");
    }, 300);

    try {
      const res = await fetch("/api/upload", { method: "POST", body: form });
      clearInterval(uploadTimer);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "上传失败");

      currentTaskId = data.task_id;
      setUploadProgress(100, "批量入队完成");
      $("uploadBadge").className = "status-badge completed";
      $("uploadBadge").textContent = "已完成";

      $("parseStage").classList.add("active");
      $("logCard").style.display = "block";
      setParseProgress(0, "已加入本地队列，等待轮到当前任务...");
      $("parseBadge").className = "status-badge queued";
      $("parseBadge").textContent = "已排队";
      showToast("已加入队列: " + (data.batch_count || selectedFiles.length) + " 个 PDF");
      selectedFiles = [];
      renderSelectedFiles();
      $("btnSpinner").style.display = "none";
      $("btnText").textContent = "批量入队";
      $("convertBtn").disabled = false;

      startPolling();
      loadTasks();
    } catch (error) {
      clearInterval(uploadTimer);
      $("convertBtn").disabled = false;
      $("btnSpinner").style.display = "none";
      $("btnText").textContent = "批量入队";
      $("cancelBtn").style.display = "none";
      showError(error.message);
      setUploadProgress(0, "上传失败");
      $("uploadBadge").className = "status-badge failed";
      $("uploadBadge").textContent = "失败";
    }
  }

  function bindEvents() {
    const dropZone = $("dropZone");
    const fileInput = $("fileInput");
    const configBody = $("configBody");
    const configToggle = $("configToggle");

    dropZone.addEventListener("click", () => fileInput.click());
    dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropZone.classList.add("dragover");
    });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
    dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropZone.classList.remove("dragover");
      if (event.dataTransfer.files.length) handleFiles(event.dataTransfer.files);
    });
    fileInput.addEventListener("change", (event) => {
      if (event.target.files.length) handleFiles(event.target.files);
    });

    $("clearBtn").addEventListener("click", resetAll);
    $("cancelBtn").addEventListener("click", cancelCurrentTask);
    $("convertBtn").addEventListener("click", startConvert);
    $("configHeader").addEventListener("click", () => {
      const open = configBody.classList.toggle("open");
      configToggle.innerHTML = open ? "收起 &#9652;" : "展开 &#9662;";
    });
    $("copyBtn").addEventListener("click", () => {
      if (!markdownContent) return;
      navigator.clipboard.writeText(markdownContent).then(() => showToast("已复制到剪贴板!"));
    });
    $("downloadBtn").addEventListener("click", () => {
      if (!currentTaskId) return;
      window.location.href = "/api/download/" + encodeURIComponent(currentTaskId);
    });
    $("downloadCompleteBtn").addEventListener("click", () => {
      if (!currentTaskId) return;
      window.location.href = "/api/download_complete/" + encodeURIComponent(currentTaskId);
    });
    $("downloadCorrectedBtn").addEventListener("click", () => {
      if (!currentTaskId) return;
      window.location.href = "/api/download_corrected/" + encodeURIComponent(currentTaskId);
    });
  }

  bindEvents();
  checkHealth();
  loadTasks();
  setInterval(checkHealth, 10000);
})();
