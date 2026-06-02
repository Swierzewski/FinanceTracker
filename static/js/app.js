/* ── Tab switching ── */
function activateTab(target) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    const btn = document.querySelector(`.tab-btn[data-tab="${target}"]`);
    if (btn) btn.classList.add('active');
    const panel = document.getElementById(target);
    if (panel) panel.classList.add('active');
}

document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        activateTab(btn.dataset.tab);
        sessionStorage.setItem('activeTab', btn.dataset.tab);
    });
});

// Restore active tab on page load
(function restoreTab() {
    const saved = sessionStorage.getItem('activeTab');
    if (saved && document.getElementById(saved)) activateTab(saved);
}());

/* ── Spinner helpers ── */
const spinner = document.getElementById('spinner-overlay');
function showSpinner() { spinner && spinner.classList.add('active'); }
function hideSpinner() { spinner && spinner.classList.remove('active'); }

/* ── Undo + alert persistence via sessionStorage ── */
const UNDO_TTL  = 5 * 60 * 1000; // 5 minutes
const ALERT_TTL = 60 * 1000;     // 1 minute
const UNDO_KEY  = 'undoTimestamp';
const ALERT_KEY = 'aiAlert'; // JSON: {text, type, ts}

function _setUndoPending() {
    sessionStorage.setItem(UNDO_KEY, Date.now().toString());
}
function _clearUndoPending() {
    sessionStorage.removeItem(UNDO_KEY);
}
function _scheduleUndoHide(msRemaining) {
    setTimeout(() => {
        _clearUndoPending();
        const sec = document.getElementById('undo-section');
        if (sec) sec.style.display = 'none';
    }, msRemaining);
}

function _saveAlert(text, type) {
    sessionStorage.setItem(ALERT_KEY, JSON.stringify({ text, type, ts: Date.now() }));
}
function _clearAlert() {
    sessionStorage.removeItem(ALERT_KEY);
}
function _scheduleAlertHide(msRemaining) {
    setTimeout(() => {
        _clearAlert();
        clearAlert('ai-alert');
    }, msRemaining);
}

// On every page load: restore undo button if within TTL
(function restoreUndo() {
    const ts = parseInt(sessionStorage.getItem(UNDO_KEY) || '0', 10);
    if (!ts) return;
    const elapsed = Date.now() - ts;
    if (elapsed >= UNDO_TTL) { _clearUndoPending(); return; }
    const sec = document.getElementById('undo-section');
    if (sec) sec.style.display = 'block';
    _scheduleUndoHide(UNDO_TTL - elapsed);
}());

// On every page load: restore alert message if within 1 minute
(function restoreAlert() {
    const raw = sessionStorage.getItem(ALERT_KEY);
    if (!raw) return;
    try {
        const { text, type, ts } = JSON.parse(raw);
        const elapsed = Date.now() - ts;
        if (elapsed >= ALERT_TTL) { _clearAlert(); return; }
        showAlert('ai-alert', text, type);
        _scheduleAlertHide(ALERT_TTL - elapsed);
    } catch { _clearAlert(); }
}());

/* ── AI Entry Form (AJAX) ── */
const aiForm = document.getElementById('ai-entry-form');
if (aiForm) {
    aiForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const input = document.getElementById('nl-input').value.trim();
        if (!input) return;

        // A new submission replaces any previous undo/alert state
        _clearUndoPending();
        _clearAlert();
        showSpinner();
        clearAlert('ai-alert');

        try {
            const res  = await fetch('/api/ai_entry', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ text: input }),
            });
            const data = await res.json();
            hideSpinner();

            if (data.success) {
                showAlert('ai-alert', data.message, 'success');
                document.getElementById('nl-input').value = '';
                _setUndoPending();
                _saveAlert(data.message, 'success'); // persist across the reload below
                setTimeout(() => location.reload(), 1200);
            } else {
                showAlert('ai-alert', data.error || 'Processing failed.', 'error');
            }
        } catch (err) {
            hideSpinner();
            showAlert('ai-alert', 'Network error: ' + err.message, 'error');
        }
    });
}

/* ── Undo button ── */
const undoBtn = document.getElementById('undo-btn');
if (undoBtn) {
    undoBtn.addEventListener('click', async () => {
        showSpinner();
        const res  = await fetch('/api/undo', { method: 'POST' });
        const data = await res.json();
        hideSpinner();
        if (data.success) {
            _clearUndoPending();
            _clearAlert();
            document.getElementById('undo-section').style.display = 'none';
            showAlert('ai-alert', 'Last action undone.', 'info');
            setTimeout(() => location.reload(), 1000);
        }
    });
}

/* ── Month selector for Tab 2 ── */
const monthSel = document.getElementById('month-select');
if (monthSel) {
    monthSel.addEventListener('change', async () => {
        const month = monthSel.value;
        const res  = await fetch(`/api/monthly?month=${encodeURIComponent(month)}`);
        const data = await res.json();
        renderMonthly(data);
    });
    // Trigger on load to populate with current month
    monthSel.dispatchEvent(new Event('change'));
}

/* ── Render monthly breakdown ── */
function renderMonthly(data) {
    setMetricCard('m-income',   data.income,   null);
    setMetricCard('m-expenses', data.expenses, null);
    setMetricCard('m-needs',    data.needs,    data.needs_pct);
    setMetricCard('m-wants',    data.wants,    data.wants_pct);
    setMetricCard('m-invest',   data.invest,   data.invest_pct);

    renderTable('exp-table', data.exp_rows,
        ['Date','Item','Amount','Category','Rule_Bucket','Card'],
        { Rule_Bucket: bucketSelectCell });
    renderTable('inc-table', data.inc_rows, ['Date','Source','Amount','Card']);
}

const BUCKETS = ['Needs', 'Wants', 'Investments'];

function bucketSelectCell(row) {
    const opts = BUCKETS.map(b =>
        `<option${b === row.Rule_Bucket ? ' selected' : ''}>${b}</option>`
    ).join('');
    return `<td><select class="bucket-select" data-row-idx="${row._row_idx}">${opts}</select></td>`;
}

function setMetricCard(id, value, delta) {
    const card = document.getElementById(id);
    if (!card) return;
    card.querySelector('.metric-value').textContent = fmtPLN(value);
    const d = card.querySelector('.metric-delta');
    if (d) d.textContent = delta !== null ? delta : '';
}

function renderTable(tbodyId, rows, cols, customCells = {}) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;
    if (!rows || rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${cols.length}" style="text-align:center;color:var(--text-secondary);padding:20px;">No data</td></tr>`;
        return;
    }
    tbody.innerHTML = rows.map(row =>
        `<tr>${cols.map(c =>
            customCells[c] ? customCells[c](row) : `<td>${row[c] ?? ''}</td>`
        ).join('')}</tr>`
    ).join('');
}

/* ── Bucket inline edit ── */
document.getElementById('exp-table')?.addEventListener('change', async (e) => {
    const sel = e.target.closest('.bucket-select');
    if (!sel) return;
    const rowIdx = sel.dataset.rowIdx;
    const bucket = sel.value;
    sel.disabled = true;
    try {
        const res  = await fetch(`/api/expense/${rowIdx}/bucket`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ bucket }),
        });
        const data = await res.json();
        if (data.success) {
            sel.classList.add('bucket-saved');
            setTimeout(() => sel.classList.remove('bucket-saved'), 1500);
        } else {
            alert(data.error || 'Failed to update bucket.');
        }
    } catch {
        alert('Network error — bucket not saved.');
    } finally {
        sel.disabled = false;
    }
});

/* ── Alert helpers ── */
function showAlert(id, msg, type) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = `alert alert-${type}`;
    el.textContent = msg;
    el.style.display = 'flex';
}
function clearAlert(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = 'none';
    el.textContent = '';
}

/* ── Formatters ── */
function fmtPLN(v) {
    if (v === undefined || v === null) return '—';
    return Number(v).toLocaleString('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' zł';
}

/* ── Checklist ── */
const clForm = document.getElementById('checklist-add-form');
if (clForm) {
    clForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name   = document.getElementById('cl-name').value.trim();
        const dueDay = parseInt(document.getElementById('cl-day').value, 10);
        if (!name || !dueDay) return;

        const res  = await fetch('/api/checklist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, due_day: dueDay }),
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('cl-name').value = '';
            document.getElementById('cl-day').value  = '';
            location.reload();
        } else {
            alert(data.error || 'Failed to add item.');
        }
    });
}

/* ── Investment: Add asset ── */
const investAddForm = document.getElementById('invest-add-form');
if (investAddForm) {
    investAddForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const assetName   = document.getElementById('inv-asset-name').value.trim();
        const amtRaw      = document.getElementById('inv-amount').value.trim().replace(',', '.');
        const curRaw      = document.getElementById('inv-current').value.trim().replace(',', '.');
        const amtInvested = parseFloat(amtRaw);
        const currentVal  = curRaw !== '' ? parseFloat(curRaw) : amtInvested;

        if (!assetName || isNaN(amtInvested) || amtInvested <= 0) {
            showAlert('invest-add-alert', 'Please provide a valid asset name and a positive invested amount.', 'error');
            return;
        }
        const res  = await fetch('/api/investments/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ asset_name: assetName, amount_invested: amtInvested, current_value: currentVal }),
        });
        const data = await res.json();
        if (data.success) {
            showAlert('invest-add-alert', 'Asset added successfully.', 'success');
            setTimeout(() => location.reload(), 800);
        } else {
            showAlert('invest-add-alert', data.error || 'Failed to add asset.', 'error');
        }
    });
}

/* ── Investment: Update current value ── */
const investUpdateForm = document.getElementById('invest-update-form');
if (investUpdateForm) {
    investUpdateForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const assetId  = document.getElementById('inv-select-asset').value;
        const newValue = parseFloat(document.getElementById('inv-new-value').value.trim().replace(',', '.'));

        if (!assetId) {
            showAlert('invest-update-alert', 'Please select an asset.', 'error');
            return;
        }
        if (isNaN(newValue) || newValue < 0) {
            showAlert('invest-update-alert', 'Please provide a valid current value.', 'error');
            return;
        }
        const res  = await fetch('/api/investments/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: assetId, current_value: newValue }),
        });
        const data = await res.json();
        if (data.success) {
            showAlert('invest-update-alert', 'Asset value updated.', 'success');
            setTimeout(() => location.reload(), 800);
        } else {
            showAlert('invest-update-alert', data.error || 'Failed to update asset.', 'error');
        }
    });
}

/* ── Investment: Delete asset (event delegation) ── */
document.getElementById('portfolio-table-wrap')?.addEventListener('click', async (e) => {
    const btn = e.target.closest('.invest-del-btn');
    if (!btn) return;
    const id  = btn.dataset.id;
    const res = await fetch(`/api/investments/delete/${id}`, { method: 'POST' });
    if ((await res.json()).success) location.reload();
});

/* ── PPK: Log new contribution ── */
document.getElementById('ppk-add-btn')?.addEventListener('click', async () => {
    const dateVal    = document.getElementById('ppk-date').value.trim();
    const empRaw     = document.getElementById('ppk-employee').value.trim().replace(',', '.');
    const empRRaw    = document.getElementById('ppk-employer').value.trim().replace(',', '.');
    const stateRaw   = document.getElementById('ppk-state').value.trim().replace(',', '.');
    const valRaw     = document.getElementById('ppk-value').value.trim().replace(',', '.');

    const employee = parseFloat(empRaw);
    const employer = parseFloat(empRRaw);

    if (isNaN(employee) || isNaN(employer) || (employee === 0 && employer === 0)) {
        showAlert('ppk-alert', 'Enter at least one contribution amount (employee or employer).', 'error');
        return;
    }

    const payload = {
        date:                   dateVal || null,
        employee_contribution:  isNaN(employee)                      ? 0 : employee,
        employer_contribution:  isNaN(employer)                      ? 0 : employer,
        state_bonus:            stateRaw !== '' && !isNaN(parseFloat(stateRaw)) ? parseFloat(stateRaw) : 0,
        current_account_value:  valRaw   !== '' && !isNaN(parseFloat(valRaw))   ? parseFloat(valRaw)   : '',
    };

    const res  = await fetch('/api/ppk/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
        showAlert('ppk-alert', 'PPK contribution logged successfully.', 'success');
        setTimeout(() => location.reload(), 800);
    } else {
        showAlert('ppk-alert', data.error || 'Failed to log contribution.', 'error');
    }
});

/* ── PPK: Delete entry (event delegation) ── */
document.getElementById('ppk-table-wrap')?.addEventListener('click', async (e) => {
    const btn = e.target.closest('.ppk-del-btn');
    if (!btn) return;
    const id  = btn.dataset.id;
    const res = await fetch(`/api/ppk/delete/${id}`, { method: 'POST' });
    if ((await res.json()).success) location.reload();
});

document.getElementById('checklist-list')?.addEventListener('click', async (e) => {
    const item = e.target.closest('.checklist-list-item');
    if (!item) return;
    const id = item.dataset.id;

    if (e.target.classList.contains('checklist-toggle')) {
        const res = await fetch(`/api/checklist/toggle/${id}`, { method: 'POST' });
        if ((await res.json()).success) location.reload();
    }
    if (e.target.classList.contains('checklist-del')) {
        const res = await fetch(`/api/checklist/delete/${id}`, { method: 'POST' });
        if ((await res.json()).success) location.reload();
    }
});
