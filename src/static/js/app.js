/* LEI Lookup Tool - Frontend JavaScript */

document.addEventListener('DOMContentLoaded', function () {
    initCountryAutocomplete();
    initNavToggle();
    initDropZone();
    initFileInput();
    initLookupForm();
    initTableFilter();
    initSortableColumns();
    initTimestamps();
});

/* ===== Country Autocomplete ===== */
function initCountryAutocomplete() {
    var datalist = document.getElementById('country-list');
    if (!datalist) return;

    fetch('/api/countries')
        .then(function (r) { return r.json(); })
        .then(function (countries) {
            countries.forEach(function (c) {
                var opt = document.createElement('option');
                opt.value = c.name;
                opt.textContent = c.name + ' (' + c.code + ')';
                datalist.appendChild(opt);
            });
        })
        .catch(function () { /* silent fail */ });
}

/* ===== Nav Toggle ===== */
function initNavToggle() {
    var toggle = document.querySelector('.nav-toggle');
    var links = document.querySelector('.nav-links');
    if (!toggle || !links) return;

    toggle.addEventListener('click', function () {
        var expanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', !expanded);
        links.classList.toggle('open');
    });
}

/* ===== Drop Zone ===== */
function initDropZone() {
    var zone = document.getElementById('drop-zone');
    if (!zone) return;

    zone.addEventListener('dragover', function (e) {
        e.preventDefault();
        zone.classList.add('dragover');
    });

    zone.addEventListener('dragleave', function (e) {
        e.preventDefault();
        zone.classList.remove('dragover');
    });

    zone.addEventListener('drop', function (e) {
        e.preventDefault();
        zone.classList.remove('dragover');
        var fileInput = document.getElementById('file-input');
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelected(fileInput.files[0]);
        }
    });

    /* Allow keyboard activation */
    zone.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            document.getElementById('file-input').click();
        }
    });
}

/* ===== File Input ===== */
function initFileInput() {
    var fileInput = document.getElementById('file-input');
    if (!fileInput) return;

    fileInput.addEventListener('change', function () {
        if (fileInput.files.length > 0) {
            handleFileSelected(fileInput.files[0]);
        }
    });

    var clearBtn = document.getElementById('file-clear');
    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            fileInput.value = '';
            document.getElementById('file-info').hidden = true;
            document.getElementById('upload-submit').disabled = true;
        });
    }
}

function handleFileSelected(file) {
    var allowed = ['.xlsx', '.csv', '.docx'];
    var ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();

    if (allowed.indexOf(ext) === -1) {
        showToast('Unsupported file type. Please use .xlsx, .csv, or .docx', 'error');
        return;
    }

    var info = document.getElementById('file-info');
    var nameSpan = document.getElementById('file-name');
    info.hidden = false;
    nameSpan.textContent = file.name + ' (' + formatBytes(file.size) + ')';
    document.getElementById('upload-submit').disabled = false;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

/* ===== Lookup Form ===== */
function initLookupForm() {
    var form = document.getElementById('lookup-form');
    if (!form) return;

    form.addEventListener('submit', function () {
        var btn = document.getElementById('lookup-submit');
        var spinner = btn.querySelector('.spinner');
        var text = btn.querySelector('.btn-text');
        btn.disabled = true;
        if (spinner) spinner.hidden = false;
        if (text) text.textContent = 'Searching...';
    });
}

/* ===== SSE Progress ===== */
function connectProgress(jobId, totalEntities) {
    var progressSection = document.getElementById('progress-section');
    var progressBar = document.getElementById('progress-bar');
    var progressText = document.getElementById('progress-text');
    var progressEntity = document.getElementById('progress-entity');
    var progressTime = document.getElementById('progress-time');
    var downloadToolbar = document.getElementById('download-toolbar');
    var summaryBadges = document.getElementById('summary-badges');
    var tbody = document.getElementById('results-tbody');

    var startTime = Date.now();
    var timerInterval = setInterval(function () {
        var elapsed = Math.floor((Date.now() - startTime) / 1000);
        var min = Math.floor(elapsed / 60);
        var sec = elapsed % 60;
        if (progressTime) {
            progressTime.textContent = (min > 0 ? min + 'm ' : '') + sec + 's';
        }
    }, 1000);

    var source = new EventSource('/progress/' + jobId);

    source.onmessage = function (e) {
        var data;
        try { data = JSON.parse(e.data); } catch (_) { return; }

        if (data.type === 'progress') {
            var pct = (data.current / data.total * 100);
            progressBar.style.width = pct + '%';
            progressBar.parentElement.setAttribute('aria-valuenow', data.current);
            progressText.textContent = 'Processing ' + data.current + ' of ' + data.total;
            progressEntity.textContent = data.entity_name;
        }

        if (data.type === 'result') {
            /* Add row to table */
            var tr = document.createElement('tr');
            tr.innerHTML =
                '<td>' + (data.index + 1) + '</td>' +
                '<td class="name-cell">' + escapeHtml(data.entity_name) + '</td>' +
                '<td class="lei-cell">' + escapeHtml(data.lei) + '</td>' +
                '<td></td>' +
                '<td><span class="badge badge-' + data.match_type.toLowerCase() + '">' + escapeHtml(data.match_type) + '</span></td>' +
                '<td class="confidence-cell confidence-' + confidenceClass(data.confidence) + '">' + data.confidence.toFixed(1) + '%</td>' +
                '<td></td>' +
                '<td class="notes-cell" title="' + escapeHtml(data.notes) + '">' + escapeHtml(data.notes) + '</td>';
            tbody.appendChild(tr);

            var pct = (data.current / data.total * 100);
            progressBar.style.width = pct + '%';
            progressBar.parentElement.setAttribute('aria-valuenow', data.current);
        }

        if (data.type === 'complete') {
            clearInterval(timerInterval);
            progressSection.hidden = true;
            if (downloadToolbar) downloadToolbar.hidden = false;
            showToast('Processing complete! ' + data.total + ' entities processed.', 'success');
            source.close();
            /* Reload to get full server-rendered results with all columns */
            setTimeout(function () { window.location.reload(); }, 500);
        }

        if (data.type === 'error') {
            clearInterval(timerInterval);
            progressText.textContent = 'Error: ' + data.message;
            progressBar.style.background = 'var(--color-red)';
            showToast('Processing error: ' + data.message, 'error');
            source.close();
        }
    };

    source.onerror = function () {
        /* EventSource auto-reconnects, but after final close this fires */
    };
}

function confidenceClass(val) {
    if (val >= 85) return 'high';
    if (val >= 65) return 'medium';
    return 'low';
}

/* ===== Table Filter ===== */
function initTableFilter() {
    var input = document.getElementById('table-filter');
    if (!input) return;

    var timer = null;
    input.addEventListener('input', function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
            var query = input.value.toLowerCase();
            var rows = document.querySelectorAll('#results-tbody tr');
            rows.forEach(function (row) {
                var text = row.textContent.toLowerCase();
                row.style.display = text.indexOf(query) !== -1 ? '' : 'none';
            });
        }, 200);
    });
}

/* ===== Sortable Columns ===== */
function initSortableColumns() {
    var ths = document.querySelectorAll('.sortable');
    ths.forEach(function (th) {
        th.addEventListener('click', function () {
            var tbody = document.getElementById('results-tbody');
            if (!tbody) return;
            var rows = Array.from(tbody.querySelectorAll('tr'));
            var colIndex = Array.from(th.parentNode.children).indexOf(th);
            var asc = th.dataset.dir !== 'asc';
            th.dataset.dir = asc ? 'asc' : 'desc';

            rows.sort(function (a, b) {
                var at = a.children[colIndex].textContent.trim();
                var bt = b.children[colIndex].textContent.trim();
                var an = parseFloat(at);
                var bn = parseFloat(bt);
                if (!isNaN(an) && !isNaN(bn)) {
                    return asc ? an - bn : bn - an;
                }
                return asc ? at.localeCompare(bt) : bt.localeCompare(at);
            });

            rows.forEach(function (r) { tbody.appendChild(r); });
        });
    });
}

/* ===== Timestamps ===== */
function initTimestamps() {
    document.querySelectorAll('.timestamp').forEach(function (el) {
        var ts = parseFloat(el.dataset.ts);
        if (!isNaN(ts)) {
            el.textContent = new Date(ts * 1000).toLocaleString();
        }
    });
}

/* ===== Toast ===== */
function showToast(message, type) {
    var container = document.getElementById('toast-container');
    if (!container) return;

    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (type || 'success');
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(function () {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(20px)';
        toast.style.transition = '0.3s ease';
        setTimeout(function () { toast.remove(); }, 300);
    }, 4000);
}

/* ===== Utilities ===== */
function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
