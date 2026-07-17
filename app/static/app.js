const API_BASE = '';
let pollingTimer = null;
let templatesCache = [];
let currentSmartTaskId = null;
let smartActionsCache = [];
let currentAnalysisTaskId = null;
let chartInstances = {};
let dbConnected = false;
let currentReportData = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function showStatus(el, message, type = 'info') {
    if (!el) return;
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
    if (!tpl) return document.createDocumentFragment();
    const clone = tpl.content.cloneNode(true);
    if (fillFn) fillFn(clone);
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

// === ОТОБРАЖЕНИЕ ИМЕНИ ФАЙЛА ===
$('#file-input').addEventListener('change', function() {
    const display = $('#file-name-display');
    if (this.files && this.files.length > 0) {
        display.textContent = this.files[0].name;
        display.classList.add('has-file');
    } else {
        display.textContent = 'Файл не выбран';
        display.classList.remove('has-file');
    }
});

// === ВКЛАДКИ ===
function switchTab(tabName) {
    $$('.tab').forEach(b => b.classList.remove('active'));
    $$('.tab-content').forEach(c => c.classList.remove('active'));
    
    const btn = document.querySelector('[data-tab="' + tabName + '"]');
    const content = document.getElementById('tab-' + tabName);
    
    if (btn) btn.classList.add('active');
    if (content) content.classList.add('active');
    
    if (tabName === 'files') loadFiles();
    if (tabName === 'smart') loadFilesForSmart();
    if (tabName === 'analysis') loadAnalysisTasks(); // ← НОВОЕ: загружаем задачи при открытии вкладки
    if (tabName === 'templates') { loadTemplates(); loadAvailableChecks(); refreshTemplateSelects(); }
}

$$('.tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
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
        $('#file-name-display').textContent = 'Файл не выбран';
        $('#file-name-display').classList.remove('has-file');
        setTimeout(() => {
            switchTab('analysis');
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
            switchTab('analysis');
            loadReport(res.task_id);
        } catch (err) { alert('Ошибка: ' + err.message); }
    } else if (e.target.classList.contains('smart-btn')) {
        const fileId = e.target.dataset.fileId;
        switchTab('smart');
        setTimeout(() => {
            $('#smart-file-select').value = fileId;
            $('#run-smart-btn').click();
        }, 100);
    }
});

$('#refresh-files').addEventListener('click', loadFiles);
$('#filter-type').addEventListener('change', loadFiles);

// === ПОДКЛЮЧЕНИЕ К БД ===
$('#connect-db-btn').addEventListener('click', () => switchTab('database'));

$('#db-connect-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = $('#db-status');
    const card = $('#db-tables-card');
    
    const config = {
        server_name: $('#db-server').value.trim(),
        type_db: $('#db-type').value,
        host: $('#db-host').value.trim(),
        port: parseInt($('#db-port').value),
        user: $('#db-user').value.trim(),
        password: $('#db-password').value.trim(),
        database: $('#db-database').value.trim(),
        extra_params: { sslmode: 'require' }
    };
    
    showStatus(status, 'Подключение...', 'info');
    try {
        const res = await api('/api/v1/data/connect-db', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        showStatus(status, `✓ Подключено к ${res.server_name} (${res.type_db})`, 'success');
        dbConnected = true;
        card.style.display = '';
        renderDbTables(res.tables);
    } catch (err) {
        showStatus(status, 'Ошибка: ' + err.message, 'error');
        card.style.display = 'none';
    }
});

function renderDbTables(tables) {
    const list = $('#db-tables-list');
    list.innerHTML = '';
    if (!tables || tables.length === 0) {
        list.innerHTML = '<p class="hint">Таблиц не найдено.</p>';
        return;
    }
    tables.forEach(table => {
        const node = renderTemplate('tpl-db-table', (clone) => {
            clone.querySelector('.name').textContent = table;
            clone.querySelector('.analyze-db-btn').dataset.table = table;
        });
        list.appendChild(node);
    });
}

// === АНАЛИЗ ТАБЛИЦ ИЗ БД ===
$('#db-tables-list').addEventListener('click', async (e) => {
    if (e.target.classList.contains('analyze-db-btn')) {
        const tableName = e.target.dataset.table;
        const btn = e.target;
        btn.textContent = 'Загрузка...';
        btn.disabled = true;
        
        try {
            const result = await api(`/api/v1/data/db-table/${encodeURIComponent(tableName)}`, {
                method: 'POST'
            });
            const analysisRes = await api('/api/v1/analysis/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_id: result.file_id, template_name: null })
            });
            switchTab('analysis');
            loadReport(analysisRes.task_id);
        } catch (err) {
            alert('Ошибка анализа: ' + err.message);
        } finally {
            btn.textContent = 'Анализировать →';
            btn.disabled = false;
        }
    }
});

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

// === ОТЧЁТЫ - ЗАГРУЗКА СПИСКА ЗАДАЧ ===
async function loadAnalysisTasks() {
    const container = $('#analysis-empty');
    const listContainer = $('#analysis-tasks-list');
    
    try {
        // Получаем список недавних файлов
        const files = await api('/api/v1/data/recent?file_type=all&limit=20');
        
        if (!files || files.length === 0) {
            container.innerHTML = `
                <p class="hint">Нет выполненных анализов.</p>
                <p class="hint" style="margin-top:8px;">Загрузите файл на вкладке "Загрузка" или "Файлы".</p>
            `;
            return;
        }
        
        // Получаем задачи для каждого файла
        let tasksHtml = '<div class="list">';
        let hasTasks = false;
        
        for (const file of files) {
            try {
                // Пытаемся получить задачу по file_id
                const task = await api(`/api/v1/analysis/task-by-file/${file.id}`);
                if (task && task.task_id) {
                    hasTasks = true;
                    const statusColor = task.status === 'done' ? '#B8FF3C' : '#FFD23F';
                    tasksHtml += `
                        <div class="list-item" style="cursor:pointer;" data-task-id="${task.task_id}">
                            <div class="item-main">
                                <div class="name">${escapeHtml(file.filename)}</div>
                                <div class="meta">
                                    <span style="color:${statusColor};">${task.status.toUpperCase()}</span>
                                    &nbsp;·&nbsp; ${formatDate(task.created_at)}
                                    ${task.template_id ? `&nbsp;·&nbsp; Шаблон: ${escapeHtml(task.template_id)}` : ''}
                                </div>
                            </div>
                            <div class="item-actions">
                                <button class="btn load-task-btn" data-task-id="${task.task_id}">Загрузить отчёт →</button>
                            </div>
                        </div>
                    `;
                }
            } catch (e) {
                // Если задачи нет, пропускаем
                continue;
            }
        }
        
        tasksHtml += '</div>';
        
        if (!hasTasks) {
            container.innerHTML = `
                <p class="hint">Нет выполненных анализов.</p>
                <p class="hint" style="margin-top:8px;">Загрузите файл на вкладке "Загрузка" или "Файлы".</p>
            `;
        } else {
            container.innerHTML = `
                <p class="hint">Последние выполненные анализы:</p>
                ${tasksHtml}
                <div class="inline-form" style="margin-top:16px; border-top:1px solid var(--border); padding-top:16px;">
                    <input type="text" id="task-id-input" placeholder="Введите ID задачи">
                    <button id="load-task-btn" class="btn">Загрузить по ID</button>
                </div>
            `;
            
            // Обработчики для кнопок "Загрузить отчёт"
            container.querySelectorAll('.load-task-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const taskId = btn.dataset.taskId;
                    loadReport(taskId);
                });
            });
            
            // Обработчик для клика по строке
            container.querySelectorAll('.list-item').forEach(item => {
                item.addEventListener('click', () => {
                    const taskId = item.dataset.taskId;
                    if (taskId) loadReport(taskId);
                });
            });
            
            // Обработчик для кнопки "Загрузить по ID"
            const loadBtn = container.querySelector('#load-task-btn');
            if (loadBtn) {
                loadBtn.addEventListener('click', () => {
                    const input = container.querySelector('#task-id-input');
                    if (input && input.value.trim()) {
                        loadReport(input.value.trim());
                    }
                });
            }
        }
    } catch (err) {
        container.innerHTML = `
            <p class="hint">Ошибка загрузки списка: ${err.message}</p>
            <div class="inline-form" style="margin-top:16px;">
                <input type="text" id="task-id-input" placeholder="Введите ID задачи">
                <button id="load-task-btn" class="btn">Загрузить по ID</button>
            </div>
        `;
        const loadBtn = container.querySelector('#load-task-btn');
        if (loadBtn) {
            loadBtn.addEventListener('click', () => {
                const input = container.querySelector('#task-id-input');
                if (input && input.value.trim()) {
                    loadReport(input.value.trim());
                }
            });
        }
    }
}

// === ЗАГРУЗКА ОТЧЁТА ===
async function loadReport(taskId) {
    currentAnalysisTaskId = taskId;
    $('#analysis-empty').classList.add('hidden');
    $('#analysis-result').classList.remove('hidden');
    $('#metrics-grid').innerHTML = '<p class="hint">Загрузка...</p>';
    $('#download-pdf-btn').href = `/api/v1/analysis/report/${taskId}/pdf`;

    try {
        const status = await api(`/api/v1/analysis/status/${taskId}`);
        if (status.status === 'pending' || status.status === 'processing') {
            $('#metrics-grid').innerHTML = '<p class="hint">Анализ выполняется...</p>';
            startPolling(taskId);
            return;
        }
        const report = await api(`/api/v1/analysis/report/${taskId}`);
        currentReportData = report;
        renderReport(report);
    } catch (err) {
        $('#metrics-grid').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
        // Показываем кнопку возврата
        $('#analysis-empty').classList.remove('hidden');
        $('#analysis-result').classList.add('hidden');
        loadAnalysisTasks();
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
                currentReportData = report;
                renderReport(report);
            }
        } catch (err) {
            stopPolling();
            $('#metrics-grid').innerHTML = `<div class="status show error">Ошибка: ${err.message}</div>`;
        }
    }, 2000);
}
function stopPolling() { if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; } }

// === ГРАФИКИ ===
function renderChart(canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (chartInstances[canvasId]) {
        chartInstances[canvasId].destroy();
    }
    try {
        chartInstances[canvasId] = new Chart(canvas.getContext('2d'), config);
    } catch (e) {
        console.warn('Chart error:', canvasId, e.message);
    }
}

const CHART_COLORS = ['#00F5D4', '#B8FF3C', '#FF2E9A', '#B026FF', '#FFD23F', '#FF6B35', '#7DF9FF', '#FF5E7E', '#4FC3F7', '#FF8A65'];

function renderCharts(analysis, validation) {
    if (typeof Chart === 'undefined') {
        console.warn('Chart.js not loaded');
        return;
    }
    
    Chart.defaults.color = '#8892b0';
    Chart.defaults.font.family = "-apple-system, sans-serif";
    Chart.defaults.font.size = 11;
    
    const colDetails = analysis.missing_details || [];
    
    // 1. Типы колонок
    const typeCounts = {};
    colDetails.forEach(c => {
        const t = c.data_type || 'unknown';
        typeCounts[t] = (typeCounts[t] || 0) + 1;
    });
    const typeLabels = Object.keys(typeCounts);
    const typeColors = CHART_COLORS.slice(0, typeLabels.length);
    
    renderChart('chart-types', {
        type: 'doughnut',
        data: {
            labels: typeLabels,
            datasets: [{ 
                data: Object.values(typeCounts), 
                backgroundColor: typeColors.map(c => c + 'CC'),
                borderColor: typeColors,
                borderWidth: 2
            }]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false, 
            cutout: '55%',
            plugins: { 
                title: { display: true, text: `Типы колонок (${colDetails.length} колонок)`, color: '#e8ecf8', font: { size: 13, weight: '600' } },
                legend: { position: 'bottom', labels: { color: '#c8cee0', padding: 10, boxWidth: 12 } } 
            } 
        }
    });

    // 2. Пропуски
    const missingSorted = [...colDetails].filter(c => c.null_count > 0)
        .sort((a, b) => b.null_count - a.null_count);
    const missingColors = missingSorted.map((_, i) => CHART_COLORS[(i + 2) % CHART_COLORS.length]);
    
    renderChart('chart-missing', {
        type: 'bar',
        data: {
            labels: missingSorted.map(c => c.name),
            datasets: [{ 
                label: 'Пропусков', 
                data: missingSorted.map(c => c.null_count), 
                backgroundColor: missingColors.map(c => c + 'BB'),
                borderColor: missingColors,
                borderWidth: 1.5,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: `Пропуски по колонкам (${missingSorted.length} колонок с пропусками)`, color: '#e8ecf8', font: { size: 13, weight: '600' } },
                legend: { display: false }
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0', maxRotation: 45 } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0' } }
            }
        }
    });

    // 3. Выбросы
    const outliers = (analysis.outlier_details || []);
    const outlierColors = outliers.map((_, i) => CHART_COLORS[(i + 4) % CHART_COLORS.length]);
    
    renderChart('chart-outliers', {
        type: 'bar',
        data: {
            labels: outliers.map(o => o.column),
            datasets: [{ 
                label: 'Выбросов', 
                data: outliers.map(o => o.count), 
                backgroundColor: outlierColors.map(c => c + 'BB'),
                borderColor: outlierColors,
                borderWidth: 1.5,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: `Выбросы по колонкам (${outliers.length} колонок с выбросами)`, color: '#e8ecf8', font: { size: 13, weight: '600' } },
                legend: { display: false }
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0', maxRotation: 45 } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0' } }
            }
        }
    });

    // 4. Валидация
    const hasValidation = validation && (validation.passed || validation.failed || validation.warnings);
    const valData = hasValidation ? [validation.passed, validation.failed, validation.warnings] : [1, 0, 0];
    renderChart('chart-validation', {
        type: 'doughnut',
        data: {
            labels: ['Пройдено', 'Ошибок', 'Предупреждений'],
            datasets: [{ 
                data: valData, 
                backgroundColor: ['#B8FF3CCC', '#FF2E9ACC', '#FFD23FCC'],
                borderColor: ['#B8FF3C', '#FF2E9A', '#FFD23F'],
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '55%',
            plugins: {
                title: { 
                    display: true, 
                    text: hasValidation ? `Результаты валидации (${validation.total_checks || 0} проверок)` : 'Валидация не проводилась', 
                    color: '#e8ecf8', 
                    font: { size: 13, weight: '600' } 
                },
                legend: { position: 'bottom', labels: { color: '#c8cee0', padding: 10, boxWidth: 12 } }
            }
        }
    });
}

function renderReport(report) {
    const analysis = report.analysis || {};
    const validation = report.validation;

    setTimeout(() => renderCharts(analysis, validation), 300);

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
            <span>Пройдено: <b style="color:#B8FF3C">${validation.passed}</b></span>
            <span>Ошибок: <b style="color:#FF2E9A">${validation.failed}</b></span>
            <span>Предупреждений: <b style="color:#FFD23F">${validation.warnings}</b></span>`;
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