const API_BASE = '';
let currentReports = [];
let currentCompany = '';

function log(msg, type='info') {
  const panel = document.getElementById('logPanel');
  const card = document.getElementById('logCard');
  const time = new Date().toLocaleTimeString();
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-time">${time}</span><span class="log-msg ${type}">${msg}</span>`;
  panel.appendChild(div);
  panel.scrollTop = panel.scrollHeight;
  card.style.display = 'block';
}

function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}

function setLoading(btnId, spinnerId, textId, text, loading) {
  const btn = document.getElementById(btnId);
  const spinner = document.getElementById(spinnerId);
  const txt = document.getElementById(textId);
  btn.disabled = loading;
  spinner.classList.toggle('active', loading);
  if (txt) txt.textContent = loading ? text + '中...' : text;
}

async function queryReports() {
  const input = document.getElementById('companyInput').value.trim();
  const exchange = document.getElementById('exchangeSelect').value;
  const year = document.getElementById('yearSelect').value;
  if (!input) { showToast('请输入公司名称或股票代码'); return; }

  setLoading('queryBtn', 'querySpinner', 'queryBtnText', '查询', true);
  log(`开始查询: ${input}`);

  let resolveRes;
  try {
    resolveRes = await fetch(`${API_BASE}/v1/resolve`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({company_name: input, exchange_hint: exchange || undefined})
    });
    if (!resolveRes.ok) throw new Error('解析公司失败');
  } catch (e) {
    log('解析公司失败，尝试重试...', 'warn');
    resolveRes = await fetch(`${API_BASE}/v1/resolve`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({company_name: '查询', ticker: input, exchange_hint: exchange || undefined})
    });
  }

  const resolved = await resolveRes.json();
  currentCompany = resolved.resolved.canonical_name;
  const ticker = resolved.resolved.ticker;
  log(`解析成功: ${currentCompany} (${ticker})`, 'success');

  try {
    const recentRes = await fetch(`${API_BASE}/v1/reports/recent`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({company_name: currentCompany, target: 'financial_report', report_year: parseInt(year), limit: 20})
    });
    if (!recentRes.ok) throw new Error('查询列表失败');
    const data = await recentRes.json();
    currentReports = data.reports || [];
    renderReportLists(currentReports);
    document.getElementById('annualListCard').style.display = 'block';
    document.getElementById('financialListCard').style.display = 'block';
    document.getElementById('downloadBar').style.display = 'block';
    log(`查到 ${currentReports.length} 份报告`, 'success');
  } catch (e) {
    log(`查询列表失败: ${e.message}`, 'error');
    showToast('查询列表失败，请重试');
  }

  setLoading('queryBtn', 'querySpinner', 'queryBtnText', '查询', false);
}

function renderReportLists(reports) {
  const annualBody = document.getElementById('annualTableBody');
  const financialBody = document.getElementById('financialTableBody');
  annualBody.innerHTML = '';
  financialBody.innerHTML = '';

  if (!reports.length) {
    annualBody.innerHTML = '<tr><td colspan="5"><div class="empty-state">暂无年报数据</div></td></tr>';
    financialBody.innerHTML = '<tr><td colspan="5"><div class="empty-state">暂无财报数据</div></td></tr>';
    return;
  }

  const typeMap = {
    'annual': {label: '年报', cls: 'tag-annual'},
    'semiannual': {label: '半年报', cls: 'tag-semiannual'},
    'q1': {label: '一季报', cls: 'tag-q1'},
    'q3': {label: '三季报', cls: 'tag-q3'}
  };

  let annualCount = 0;
  let financialCount = 0;

  reports.forEach((r, idx) => {
    const t = typeMap[r.report_type] || {label: r.report_type, cls: ''};
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="checkbox" class="report-check" data-idx="${idx}" data-type="${r.report_type}"></td>
      <td>${r.title}</td>
      <td><span class="report-tag ${t.cls}">${t.label}</span></td>
      <td>${r.report_end}</td>
      <td>${r.published_at}</td>
    `;

    if (r.report_type === 'annual') {
      annualBody.appendChild(tr);
      annualCount++;
    } else {
      financialBody.appendChild(tr);
      financialCount++;
    }
  });

  if (!annualCount) annualBody.innerHTML = '<tr><td colspan="5"><div class="empty-state">该年份暂无年报</div></td></tr>';
  if (!financialCount) financialBody.innerHTML = '<tr><td colspan="5"><div class="empty-state">该年份暂无财报</div></td></tr>';

  updateDownloadButton();
}

function toggleSelectAll(listType) {
  const checkboxId = listType === 'annual' ? 'selectAllAnnual' : 'selectAllFinancial';
  const checked = document.getElementById(checkboxId).checked;
  const selector = listType === 'annual'
    ? '#annualTableBody .report-check'
    : '#financialTableBody .report-check';
  document.querySelectorAll(selector).forEach(cb => cb.checked = checked);
  updateDownloadButton();
}

function updateDownloadButton() {
  const checked = document.querySelectorAll('.report-check:checked').length;
  document.getElementById('downloadBtn').disabled = checked === 0;
  document.getElementById('selectedCount').textContent = `已选择 ${checked} 份`;
}

document.addEventListener('change', e => {
  if (e.target.classList.contains('report-check')) {
    updateDownloadButton();
    // 取消全选框的勾选状态（如果有单个被取消）
    updateSelectAllState();
  }
});

function updateSelectAllState() {
  const annualChecks = document.querySelectorAll('#annualTableBody .report-check');
  const annualChecked = document.querySelectorAll('#annualTableBody .report-check:checked');
  document.getElementById('selectAllAnnual').checked = annualChecks.length > 0 && annualChecks.length === annualChecked.length;

  const finChecks = document.querySelectorAll('#financialTableBody .report-check');
  const finChecked = document.querySelectorAll('#financialTableBody .report-check:checked');
  document.getElementById('selectAllFinancial').checked = finChecks.length > 0 && finChecks.length === finChecked.length;
}

async function downloadSelected() {
  const checked = Array.from(document.querySelectorAll('.report-check:checked'));
  if (!checked.length) { showToast('请先选择要下载的报告'); return; }

  const reports = checked.map(cb => currentReports[parseInt(cb.dataset.idx, 10)]).filter(Boolean);
  await doDownload(reports);
}

async function doDownload(reports) {
  setLoading('downloadBtn', 'downloadSpinner', 'downloadBtnText', '下载', true);
  const reportLabels = reports.map(r => `${r.report_end} ${r.report_type}`);
  log(`开始下载: ${currentCompany}, 报告: ${reportLabels.join(', ')}`);

  try {
    const res = await fetch(`${API_BASE}/v1/reports/select-download`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        company_name: currentCompany,
        reports: reports
      })
    });
    if (!res.ok) throw new Error('下载请求失败');

    const data = await res.json();
    renderResults(data);
    showToast(`下载完成: ${data.succeeded}/${data.total}`);
    log(`下载完成: ${data.succeeded} 成功, ${data.failed} 失败`, data.failed > 0 ? 'warn' : 'success');
  } catch (e) {
    log(`下载失败: ${e.message}`, 'error');
    showToast('下载失败，请重试');
  }

  setLoading('downloadBtn', 'downloadSpinner', 'downloadBtnText', '下载选中报告', false);
}

function renderResults(data) {
  const card = document.getElementById('resultCard');
  const body = document.getElementById('resultBody');
  card.style.display = 'block';
  body.innerHTML = '';

  data.files.forEach(f => {
    const div = document.createElement('div');
    div.className = 'result-item ' + (f.file_name ? 'success' : 'failed');
    div.innerHTML = `
      <div>
        <b>${f.title}</b>
        <div style="font-size:0.8rem;color:var(--text-muted);margin-top:2px;">${f.saved_path || f.error}</div>
      </div>
      <span>${f.size_bytes ? (f.size_bytes / 1024 / 1024).toFixed(2) + ' MB' : '失败'}</span>
    `;
    body.appendChild(div);
  });
}

// 动态生成年份选项（当前年份往前推5年）
function initYearSelect() {
  const select = document.getElementById('yearSelect');
  const currentYear = new Date().getFullYear(); // 2026
  for (let y = currentYear; y >= currentYear - 5; y--) {
    const opt = document.createElement('option');
    opt.value = y;
    opt.textContent = y;
    if (y === currentYear) opt.selected = true;
    select.appendChild(opt);
  }
}
initYearSelect();

// 支持回车触发查询
document.getElementById('companyInput').addEventListener('keypress', e => {
  if (e.key === 'Enter') queryReports();
});
