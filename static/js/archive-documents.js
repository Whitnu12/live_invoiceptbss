(function () {
    const pageRoot = document.querySelector('.archive-page');
    if (!pageRoot) return;

    const canWrite = pageRoot.dataset.canWrite === 'true';
    const list = document.getElementById('archiveList');
    const listStatus = document.getElementById('archiveListStatus');
    const summary = document.getElementById('archiveSummary');
    const filterForm = document.getElementById('archiveFilterForm');
    const uploadForm = document.getElementById('archiveUploadForm');
    const uploadStatus = document.getElementById('archiveUploadStatus');
    const pagination = document.getElementById('archivePagination');
    const pageLabel = document.getElementById('archivePageLabel');
    const previousButton = document.getElementById('archivePrevious');
    const nextButton = document.getElementById('archiveNext');
    const previewModal = document.getElementById('archivePreviewModal');
    const previewFrame = document.getElementById('archivePreviewFrame');
    const previewTitle = document.getElementById('archivePreviewTitle');
    const previewClose = document.getElementById('archivePreviewClose');
    const state = { page: 1, pages: 1, total: 0, requestId: 0 };
    const params = new URLSearchParams(window.location.search);

    if (params.has('q')) document.getElementById('archiveQuery').value = params.get('q');
    if (params.has('category')) document.getElementById('archiveCategory').value = params.get('category');
    if (params.has('contract')) document.getElementById('archiveContract').value = params.get('contract');

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character];
        });
    }

    function formatBytes(value) {
        const size = Number(value) || 0;
        if (size < 1024) return size + ' B';
        if (size < 1024 * 1024) return (size / 1024).toFixed(0) + ' KB';
        return (size / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function getFilters() {
        return {
            q: document.getElementById('archiveQuery').value.trim(),
            contract: document.getElementById('archiveContract').value.trim(),
            category: document.getElementById('archiveCategory').value
        };
    }

    function updateAddress(filters) {
        const url = new URL(window.location.href);
        Object.entries(filters).forEach(([key, value]) => {
            if (value) url.searchParams.set(key, value);
            else url.searchParams.delete(key);
        });
        window.history.replaceState({}, '', url);
    }

    function renderRows(rows) {
        if (!rows.length) {
            list.innerHTML = '<div class="archive-empty"><strong>Belum ada dokumen yang cocok</strong>Ubah kata kunci atau kategori. Dokumen baru akan muncul di sini setelah disimpan.</div>';
            return;
        }
        list.innerHTML = rows.map(function (row) {
            const isPrivate = ['official_invoice', 'tax_invoice', 'vat_payment', 'withholding_tax'].includes(row.category);
            const fileType = String(row.original_filename || '').split('.').pop().toUpperCase();
            const identifiers = [
                '<span>Kontrak <b>' + escapeHtml(row.contract_number || '—') + '</b></span>',
                row.pi_number ? '<span>PI <b>' + escapeHtml(row.pi_number) + '</b></span>' : '',
                row.reference_number ? '<span>PO/Ref <b>' + escapeHtml(row.reference_number) + '</b></span>' : '',
                '<span>' + escapeHtml(row.uploaded_at || 'Tanggal tidak tersedia') + '</span>',
                '<span>' + escapeHtml(formatBytes(row.file_size)) + '</span>'
            ].filter(Boolean).join('');
            const note = row.note ? '<p class="archive-row-note">' + escapeHtml(row.note) + '</p>' : '';
            const actions = '<button type="button" class="archive-action" data-preview-id="' + escapeHtml(row.id) + '" data-preview-name="' + escapeHtml(row.original_filename) + '">Lihat</button>' +
                '<a class="archive-action" href="/api/archive-documents/' + encodeURIComponent(row.id) + '/download">Unduh</a>' +
                (canWrite && row.can_delete ? '<button type="button" class="archive-action archive-action-delete" data-delete-id="' + escapeHtml(row.id) + '" data-delete-name="' + escapeHtml(row.original_filename) + '">Hapus</button>' : '');
            return '<article class="archive-row">' +
                '<div class="archive-row-main"><h3 class="archive-row-title"><span class="archive-file-name" title="' + escapeHtml(row.original_filename) + '">' + escapeHtml(row.original_filename) + '</span>' +
                    '<span class="archive-file-type' + (fileType === 'PDF' ? ' is-pdf' : '') + '">' + escapeHtml(fileType || 'FILE') + '</span>' +
                    '<span class="archive-category-badge' + (isPrivate ? ' is-private' : '') + '">' + escapeHtml(row.category_label || row.category) + (isPrivate ? ' · Privat' : '') + '</span></h3>' +
                    '<p class="archive-row-meta">' + identifiers + '</p>' + note + '</div>' +
                '<div class="archive-row-actions">' + actions + '</div></article>';
        }).join('');
    }

    async function loadArchives(requestedPage) {
        const currentRequest = ++state.requestId;
        state.page = requestedPage || 1;
        const filters = getFilters();
        updateAddress(filters);
        const query = new URLSearchParams({ ...filters, page: state.page, page_size: 25 });
        listStatus.classList.remove('is-error');
        listStatus.textContent = 'Memuat arsip…';
        list.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch('/api/archive-documents?' + query.toString(), { headers: { Accept: 'application/json' } });
            const result = await response.json();
            if (currentRequest !== state.requestId) return;
            if (!response.ok || !result.success) throw new Error(result.message || 'Gagal memuat arsip.');
            state.total = result.total;
            state.pages = Math.max(1, result.pages || 1);
            summary.textContent = result.total + (result.total === 1 ? ' dokumen' : ' dokumen') + ' aktif';
            listStatus.textContent = result.total ? 'Diurutkan dari dokumen terbaru.' : '';
            renderRows(result.data || []);
            pagination.hidden = state.pages <= 1;
            pageLabel.textContent = 'Halaman ' + state.page + ' dari ' + state.pages;
            previousButton.disabled = state.page <= 1;
            nextButton.disabled = state.page >= state.pages;
        } catch (error) {
            if (currentRequest !== state.requestId) return;
            summary.textContent = 'Daftar arsip tidak tersedia';
            listStatus.classList.add('is-error');
            listStatus.textContent = error.message || 'Gagal memuat arsip.';
            list.innerHTML = '<div class="archive-empty"><strong>Tidak dapat memuat arsip</strong>Periksa koneksi lalu coba muat ulang.</div>';
            pagination.hidden = true;
        } finally {
            if (currentRequest === state.requestId) list.removeAttribute('aria-busy');
        }
    }

    filterForm.addEventListener('submit', function (event) {
        event.preventDefault();
        loadArchives(1);
    });
    document.getElementById('archiveRefresh').addEventListener('click', function () { loadArchives(state.page); });
    previousButton.addEventListener('click', function () { if (state.page > 1) loadArchives(state.page - 1); });
    nextButton.addEventListener('click', function () { if (state.page < state.pages) loadArchives(state.page + 1); });

    let searchTimer;
    document.getElementById('archiveQuery').addEventListener('input', function () {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(function () { loadArchives(1); }, 350);
    });

    list.addEventListener('click', async function (event) {
        const previewButton = event.target.closest('[data-preview-id]');
        if (previewButton) {
            previewTitle.textContent = previewButton.dataset.previewName || 'Dokumen';
            previewFrame.src = '/api/archive-documents/' + encodeURIComponent(previewButton.dataset.previewId) + '/preview';
            previewModal.classList.add('is-open');
            previewModal.setAttribute('aria-hidden', 'false');
            document.body.style.overflow = 'hidden';
            previewClose.focus();
            return;
        }
        const button = event.target.closest('[data-delete-id]');
        if (!button) return;
        const filename = button.dataset.deleteName || 'dokumen ini';
        if (!window.confirm('Hapus "' + filename + '" dari arsip? File fisiknya juga akan dihapus dan tindakan ini tidak dapat dibatalkan.')) return;
        button.disabled = true;
        try {
            const response = await fetch('/api/archive-documents/' + encodeURIComponent(button.dataset.deleteId), { method: 'DELETE', headers: { Accept: 'application/json' } });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || 'Gagal menghapus dokumen.');
            listStatus.classList.remove('is-error');
            listStatus.textContent = result.message;
            await loadArchives(state.page > 1 && state.total % 25 === 1 ? state.page - 1 : state.page);
        } catch (error) {
            listStatus.classList.add('is-error');
            listStatus.textContent = error.message || 'Gagal menghapus dokumen.';
            button.disabled = false;
        }
    });

    function closePreview() {
        if (!previewModal.classList.contains('is-open')) return;
        previewModal.classList.remove('is-open');
        previewModal.setAttribute('aria-hidden', 'true');
        previewFrame.src = 'about:blank';
        document.body.style.overflow = '';
    }
    previewClose.addEventListener('click', closePreview);
    previewModal.addEventListener('click', function (event) {
        if (event.target === previewModal) closePreview();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closePreview();
    });

    if (uploadForm) {
        uploadForm.addEventListener('submit', async function (event) {
            event.preventDefault();
            const button = uploadForm.querySelector('[type="submit"]');
            button.disabled = true;
            uploadStatus.classList.remove('is-error');
            uploadStatus.textContent = 'Mengunggah dan menyimpan dokumen…';
            try {
                const response = await fetch('/api/archive-documents', { method: 'POST', body: new FormData(uploadForm), headers: { Accept: 'application/json' } });
                const result = await response.json();
                if (!response.ok || !result.success) throw new Error(result.message || 'Gagal menyimpan dokumen.');
                uploadForm.reset();
                uploadStatus.textContent = result.message;
                await loadArchives(1);
            } catch (error) {
                uploadStatus.classList.add('is-error');
                uploadStatus.textContent = error.message || 'Gagal menyimpan dokumen.';
            } finally {
                button.disabled = false;
            }
        });
    }

    loadArchives(1);
}());
