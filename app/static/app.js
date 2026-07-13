const API_BASE = '';
let pollingTimer = null;
let templatesCache = [];
let currentSmartTaskId = null;
let smartActionsCache = [];
let currentAnalysisTaskId = null;  // Для кнопки PDF

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function showStatus(el, message, type = 'info') {
    el.textContent = message;
    el.className = 'status show ' + type;
}

function formatBytes(b) {
    if (b < 1024) return b + ' B';
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1024 / 1024).toFixed(1) + ' MB';
}

function formatDate(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString('ru-RU'); }
    catch { return iso; }
}

function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderTemplate(templateId, fillFn) {
    const tpl = document.getElementById(templateId);
    const clone = tpl.content.cloneNode(true);
    fillFn(clone);
    return clone;
}

async function api(path, options = {}) {
    const res = await fetch(API_BASE + path, options);
    if (!res.ok) {
        let msg = 'HTTP ' + res.status;
        try { const err = await res.json(); msg = err.detail || msg; } catch {}
        throw new Error(msg);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
}

// === ВКЛАДКИ ===
$$('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.tab').forEach(b => b.classList.remove('active'));
        $$('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        $('#tab-' + btn.dataset.tab).classList.add('active');
        if (btn.dataset.tab === 'files') loadFiles();
        if (btn.dataset.tab === 'smart') loadFilesForSmart();
        if (btn.dataset.tab === 'templates') {
            loadTemplates();
            loadAvailableChecks();
            refreshTemplateSelects();
        }
    });
});

// === ШАБЛОНЫ (КЭШ) ===
async function refreshTemplateSelects() {
    try {
        const res = await api('/api/v1/templates/catalog');
        templatesCache = res.templates || [];
        const uploadSel = $('#upload-template-select');
        const currentValue = uploadSel.value;
        uploadSel.innerHTML = '<option value="">— без проверки —</option>' +
            templatesCache.map(t => `<option value="${escapeHtml(t.name)}">${escapeHtml((t.title || t.name).trim())}</option>`).join('');
        uploadSel.value = currentValue;
    } catch {}
}

// === ЗАГРУЗКА ФАЙЛА ===
$('#upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fileInput = $('#file-input');
    const status = $('#upload-status');
    const templateName = $('#upload-template-select').value;
    const file = fileInput.files[0];
    if (!file) return;

    showStatus(status, 'Загрузка...', 'info');
    try {
        const form = new FormData();
        form.append('file', file);
        const uploadRes = await api('/api/v1/data/upload', { method: 'POST', body: form });
        showStatus(status, 'Запуск анализа...', 'info');
        const analysisRes = await api('/api/v1/analysis/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: uploadRes.file_id, template_name: templateName || null })
        });
        showStatus(status, `Готово. Task ID: ${analysisRes.task_id}`, 'success');
        fileInput.value = '';
        setTimeout(() => {
            $('[data-tab="analysis"]').click();
            loadReport(analysisRes.task_id);
        }, 500);
    } catch (err) {
        showStatus(status, 'Ошибка: ' + err.message, 'error');
    }
});

// === СПИСОК ФАЙЛОВ ===
async function loadFiles() {
    const type = $('#filter-type').value;
    try {
        const [files, stats] = await Promise.all([
            api(`/api/v1/data/recent?file_type=${type}&limit=50`),
            api('/api/v1/data/stats/recent')
        ]);
        await refreshTemplateSelects();
        renderStats(stats);
        renderFiles(files);
    } catch (err) {
        $('#files-list').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
    }
}

function renderStats(stats) {
    const bar = $('#stats-bar');
    bar.innerHTML = '';
    if (!stats || typeof stats !== 'object') return;
    Object.entries(stats).forEach(([k, v]) => {
        bar.appendChild(renderTemplate('tpl-metric', (c) => {
            c.querySelector('.value').textContent = v;
            c.querySelector('.label').textContent = k;
        }));
    });
}

function renderFiles(files) {
    const list = $('#files-list');
    list.innerHTML = '';
    if (!files || files.length === 0) {
        list.innerHTML = '<p class="hint">Файлов пока нет.</p>';
        return;
    }
    files.forEach(f => {
        const node = renderTemplate('tpl-file-item', (clone) => {
            clone.querySelector('.name').textContent = f.filename;
            clone.querySelector('.meta').textContent = `${f.file_type.toUpperCase()} · ${formatBytes(f.size)} · ${formatDate(f.uploaded_at)}`;
            clone.querySelector('.analyze-btn').dataset.fileId = f.id;
            clone.querySelector('.smart-btn').dataset.fileId = f.id;
            const sel = clone.querySelector('.template-select');
            sel.innerHTML = '<option value="">— без шаблона —</option>' +
                templatesCache.map(t => `<option value="${escapeHtml(t.name)}">${escapeHtml((t.title || t.name).trim())}</option>`).join('');
        });
        list.appendChild(node);
    });
}

$('#files-list').addEventListener('click', async (e) => {
    if (e.target.classList.contains('analyze-btn')) {
        const btn = e.target;
        const fileId = btn.dataset.fileId;
        const templateName = btn.closest('.list-item').querySelector('.template-select').value;
        try {
            const res = await api('/api/v1/analysis/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_id: fileId, template_name: templateName || null })
            });
            $('[data-tab="analysis"]').click();
            loadReport(res.task_id);
        } catch (err) { alert('Ошибка: ' + err.message); }
    } else if (e.target.classList.contains('smart-btn')) {
        const fileId = e.target.dataset.fileId;
        $('[data-tab="smart"]').click();
        setTimeout(() => {
            $('#smart-file-select').value = fileId;
            $('#run-smart-btn').click();
        }, 100);
    }
});

$('#refresh-files').addEventListener('click', loadFiles);
$('#filter-type').addEventListener('change', loadFiles);

// === УМНЫЙ АНАЛИЗ ===
async function loadFilesForSmart() {
    try {
        const files = await api('/api/v1/data/recent?file_type=all&limit=50');
        const select = $('#smart-file-select');
        select.innerHTML = '<option value="">— выберите файл —</option>' +
            files.map(f => `<option value="${f.id}">${escapeHtml(f.filename)}</option>`).join('');
    } catch (err) { console.error(err); }
}

$('#run-smart-btn').addEventListener('click', async () => {
    const fileId = $('#smart-file-select').value;
    if (!fileId) { alert('Выберите файл'); return; }
    $('#smart-empty').classList.add('hidden');
    $('#smart-result').classList.remove('hidden');
    $('#smart-metrics').innerHTML = '<p class="hint">Запуск умного анализа...</p>';
    try {
        const result = await api('/api/v1/analysis/smart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_id: fileId, template_name: null })
        });
        currentSmartTaskId = result.task_id;
        renderSmartAnalysis(result);
    } catch (err) {
        $('#smart-metrics').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
    }
});

function renderSmartAnalysis(data) {
    const metricsGrid = $('#smart-metrics');
    metricsGrid.innerHTML = '';
    const metrics = [
        ['Строк', data.total_rows],
        ['Столбцов', data.total_columns],
        ['Рекомендаций', data.recommendations.length],
        ['Действий', data.cleaning_actions.length],
        ['Влияние', data.estimated_impact.toUpperCase()],
    ];
    metrics.forEach(([label, value]) => {
        metricsGrid.appendChild(renderTemplate('tpl-metric', (c) => {
            c.querySelector('.value').textContent = value;
            c.querySelector('.label').textContent = label;
        }));
    });

    const recList = $('#recommendations-list');
    recList.innerHTML = '';
    if (data.recommendations.length === 0) {
        recList.innerHTML = '<p class="hint">Рекомендаций нет.</p>';
    } else {
        data.recommendations.forEach(rec => {
            recList.appendChild(renderTemplate('tpl-recommendation', (c) => {
                c.querySelector('.rec-type').textContent = rec.check_type;
                c.querySelector('.rec-column').textContent = rec.column;
                c.querySelector('.rec-confidence').textContent = `Уверенность: ${(rec.confidence * 100).toFixed(0)}%`;
                c.querySelector('.rec-issue').textContent = rec.issue;
                c.querySelector('.rec-action').textContent = `→ ${rec.suggested_action.description}`;
            }));
        });
    }

    const actionsList = $('#cleaning-actions');
    actionsList.innerHTML = '';
    smartActionsCache = data.cleaning_actions;
    data.cleaning_actions.forEach((action, idx) => {
        const node = renderTemplate('tpl-cleaning-action', (c) => {
            c.querySelector('.action-check').dataset.index = idx;
            c.querySelector('.action-type').textContent = action.action_type;
            const priorityEl = c.querySelector('.action-priority');
            priorityEl.textContent = action.priority;
            priorityEl.classList.add(action.priority);
            c.querySelector('.action-desc').textContent = action.description;
            c.querySelector('.action-rows').textContent = `Затронет строк: ${action.affected_rows}`;
        });
        actionsList.appendChild(node);
    });
    $('#cleaning-result').classList.add('hidden');
}

$('#apply-all-btn').addEventListener('click', () => applyCleaning(smartActionsCache));
$('#apply-selected-btn').addEventListener('click', () => {
    const selected = Array.from($$('.action-check:checked')).map(cb => smartActionsCache[parseInt(cb.dataset.index)]);
    if (selected.length === 0) { alert('Выберите хотя бы одно действие'); return; }
    applyCleaning(selected);
});

async function applyCleaning(actions) {
    try {
        const result = await api('/api/v1/analysis/apply-cleaning', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: currentSmartTaskId, actions })
        });
        $('#cleaning-result').classList.remove('hidden');
        $('#cleaning-stats').innerHTML = `
            <div class="cleaning-stat"><div class="value">${result.original_rows}</div><div class="label">Было строк</div></div>
            <div class="cleaning-stat"><div class="value">${result.cleaned_rows}</div><div class="label">Стало строк</div></div>
            <div class="cleaning-stat"><div class="value">${result.rows_removed}</div><div class="label">Удалено строк</div></div>
        `;
        const downloadBtn = $('#download-cleaned-btn');
        downloadBtn.href = result.download_url;
        downloadBtn.download = 'cleaned_data.csv';
        $('#cleaning-result').scrollIntoView({ behavior: 'smooth' });
    } catch (err) { alert('Ошибка: ' + err.message); }
}

// === ОТЧЁТЫ ===
$('#load-task-btn').addEventListener('click', () => {
    const id = $('#task-id-input').value.trim();
    if (id) loadReport(id);
});

async function loadReport(taskId) {
    currentAnalysisTaskId = taskId;
    $('#analysis-empty').classList.add('hidden');
    $('#analysis-result').classList.remove('hidden');
    $('#metrics-grid').innerHTML = '<p class="hint">Загрузка...</p>';

    // Обновляем ссылку на PDF
    $('#download-pdf-btn').href = `/api/v1/analysis/report/${taskId}/pdf`;

    try {
        const status = await api(`/api/v1/analysis/status/${taskId}`);
        if (status.status === 'pending' || status.status === 'processing') {
            $('#metrics-grid').innerHTML = '<p class="hint">Анализ выполняется...</p>';
            startPolling(taskId);
            return;
        }
        const report = await api(`/api/v1/analysis/report/${taskId}`);
        renderReport(report);
    } catch (err) {
        $('#metrics-grid').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
    }
}

function startPolling(taskId) {
    stopPolling();
    pollingTimer = setInterval(async () => {
        try {
            const status = await api(`/api/v1/analysis/status/${taskId}`);
            if (status.status === 'done' || status.status === 'failed') {
                stopPolling();
                const report = await api(`/api/v1/analysis/report/${taskId}`);
                renderReport(report);
            }
        } catch (err) {
            stopPolling();
            $('#metrics-grid').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
        }
    }, 2000);
}
function stopPolling() { if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; } }

function renderReport(report) {
    const analysis = report.analysis || {};
    const validation = report.validation;

    const metricsGrid = $('#metrics-grid');
    metricsGrid.innerHTML = '';
    const metricsData = [
        ['Строк', analysis.total_rows ?? 0],
        ['Столбцов', analysis.total_columns ?? 0],
        ['Пропусков', `${analysis.total_missing ?? 0} (${(analysis.missing_percentage ?? 0).toFixed(1)}%)`],
        ['Дубликатов', `${analysis.duplicate_count ?? 0} (${(analysis.duplicate_percentage ?? 0).toFixed(1)}%)`],
        ['Выбросов', analysis.total_outliers ?? 0],
        ['Память', (analysis.memory_usage_mb ?? 0).toFixed(2) + ' MB'],
    ];
    metricsData.forEach(([label, value]) => {
        metricsGrid.appendChild(renderTemplate('tpl-metric', (c) => {
            c.querySelector('.value').textContent = value;
            c.querySelector('.label').textContent = label;
        }));
    });

    // Колонки
    const colsContainer = $('#columns-details');
    colsContainer.innerHTML = '';
    const cols = analysis.missing_details || [];
    if (cols.length) {
        const table = document.createElement('table');
        table.className = 'table';
        table.innerHTML = '<thead><tr><th>Колонка</th><th>Тип</th><th>Пропуски</th><th>%</th><th>Уникальных</th></tr></thead><tbody></tbody>';
        const tbody = table.querySelector('tbody');
        cols.forEach(c => {
            tbody.appendChild(renderTemplate('tpl-col-row', (cl) => {
                cl.querySelector('.col-name').textContent = c.name;
                cl.querySelector('.col-type').textContent = c.data_type;
                cl.querySelector('.col-nulls').textContent = c.null_count;
                cl.querySelector('.col-nulls-pct').textContent = c.null_percentage.toFixed(1) + '%';
                cl.querySelector('.col-unique').textContent = c.unique_count;
            }));
        });
        colsContainer.appendChild(table);
    } else colsContainer.innerHTML = '<p class="hint">Нет данных.</p>';

    // Выбросы
    const outContainer = $('#outliers-details');
    outContainer.innerHTML = '';
    const outliers = analysis.outlier_details || [];
    if (outliers.length) {
        const table = document.createElement('table');
        table.className = 'table';
        table.innerHTML = '<thead><tr><th>Колонка</th><th>Кол-во</th><th>%</th><th>Диапазон</th></tr></thead><tbody></tbody>';
        const tbody = table.querySelector('tbody');
        outliers.forEach(o => {
            tbody.appendChild(renderTemplate('tpl-outlier-row', (cl) => {
                cl.querySelector('.out-col').textContent = o.column;
                cl.querySelector('.out-count').textContent = o.count;
                cl.querySelector('.out-pct').textContent = o.percentage.toFixed(1) + '%';
                cl.querySelector('.out-range').textContent = `${o.lower_bound ?? '—'} … ${o.upper_bound ?? '—'}`;
            }));
        });
        outContainer.appendChild(table);
    } else outContainer.innerHTML = '<p class="hint">Выбросов не обнаружено.</p>';

    // Корреляции
    const corrContainer = $('#correlations-details');
    corrContainer.innerHTML = '';
    const corrs = [...(analysis.perfect_correlations || []), ...(analysis.strong_correlations || [])];
    if (corrs.length) {
        const table = document.createElement('table');
        table.className = 'table';
        table.innerHTML = '<thead><tr><th>Колонка 1</th><th>Колонка 2</th><th>Коэфф.</th><th>Тип</th></tr></thead><tbody></tbody>';
        const tbody = table.querySelector('tbody');
        corrs.forEach(c => {
            tbody.appendChild(renderTemplate('tpl-corr-row', (cl) => {
                cl.querySelector('.corr-c1').textContent = c.col1;
                cl.querySelector('.corr-c2').textContent = c.col2;
                cl.querySelector('.corr-val').textContent = c.correlation.toFixed(3);
                cl.querySelector('.corr-type').textContent = c.type;
            }));
        });
        corrContainer.appendChild(table);
    } else corrContainer.innerHTML = '<p class="hint">Корреляций не найдено.</p>';

    // Рекомендации
    const recList = $('#recommendations-list-old');
    recList.innerHTML = '';
    const recs = analysis.recommendations || [];
    if (recs.length) {
        recs.forEach(r => {
            const text = typeof r === 'string' ? r : `${r.check_type}: ${r.column} — ${r.issue}`;
            recList.appendChild(renderTemplate('tpl-rec-item', (c) => { c.querySelector('.rec-text').textContent = text; }));
        });
    } else recList.innerHTML = '<li class="hint">Рекомендаций нет.</li>';

    // Валидация
    const vCard = $('#validation-card');
    if (validation && validation.results && validation.results.length) {
        vCard.style.display = '';
        $('#validation-summary').innerHTML = `
            <span>Шаблон: <b>${escapeHtml(validation.template_name)}</b></span>
            <span>Пройдено: <b style="color:var(--success)">${validation.passed}</b></span>
            <span>Ошибок: <b style="color:var(--danger)">${validation.failed}</b></span>
            <span>Предупреждений: <b style="color:var(--warning)">${validation.warnings}</b></span>`;
        const vTbody = $('#validation-tbody');
        vTbody.innerHTML = '';
        validation.results.forEach(r => {
            vTbody.appendChild(renderTemplate('tpl-val-row', (cl) => {
                const badge = cl.querySelector('.val-status');
                badge.textContent = r.status;
                badge.classList.add(r.status.toLowerCase());
                cl.querySelector('.val-type').textContent = r.check_type;
                cl.querySelector('.val-name').textContent = r.check_name;
                cl.querySelector('.val-msg').textContent = r.message;
            }));
        });
    } else vCard.style.display = 'none';
}

// === ШАБЛОНЫ ===
async function loadTemplates() {
    const el = $('#templates-catalog');
    el.innerHTML = '';
    try {
        const res = await api('/api/v1/templates/catalog');
        templatesCache = res.templates || [];
        if (!templatesCache.length) { el.innerHTML = '<p class="hint">Шаблонов пока нет.</p>'; return; }
        templatesCache.forEach(t => {
            const node = renderTemplate('tpl-template-accordion', (clone) => {
                const cleanName = (t.title || t.name || 'Без имени').trim();
                clone.querySelector('.accordion-title').textContent = cleanName;
                clone.querySelector('.accordion-badge').textContent = `${t.checks ? t.checks.length : 0} проверок`;
                clone.querySelector('.accordion-desc').textContent = (t.description || 'Нет описания').trim();
                clone.querySelector('.checks-list').textContent = t.checks && t.checks.length ? 'Включает: ' + t.checks.join(', ') : 'Нет активных проверок';
                clone.querySelector('.delete-tpl-btn').dataset.name = t.name;
            });
            el.appendChild(node);
        });
    } catch (err) {
        el.innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
    }
}

async function loadAvailableChecks() {
    const el = $('#tpl-rules');
    el.innerHTML = '';
    try {
        const res = await api('/api/v1/templates/available-checks');
        const checks = res.checks || [];
        checks.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = `${c.name} (${c.type})`;
            el.appendChild(opt);
        });
    } catch {}
}

$('#template-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = $('#template-status');
    const name = $('#tpl-name').value.trim();
    const description = $('#tpl-desc').value.trim();
    const rules = Array.from($('#tpl-rules').selectedOptions).map(o => o.value);
    try {
        await api('/api/v1/templates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, rules })
        });
        showStatus(status, `Шаблон "${name}" создан.`, 'success');
        $('#template-form').reset();
        loadTemplates();
        refreshTemplateSelects();
    } catch (err) {
        showStatus(status, 'Ошибка: ' + err.message, 'error');
    }
});

$('#templates-catalog').addEventListener('click', async (e) => {
    if (e.target.classList.contains('delete-tpl-btn')) {
        const name = e.target.dataset.name;
        if (!confirm(`Удалить шаблон "${name}"?`)) return;
        try {
            await api(`/api/v1/templates/${encodeURIComponent(name)}`, { method: 'DELETE' });
            loadTemplates();
            refreshTemplateSelects();
        } catch (err) { alert('Ошибка: ' + err.message); }
    }
});