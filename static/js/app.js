/**
 * Frontend JavaScript Engine for Shopify Taxonomy Auto-Classifier
 * Handles API integrations, live worker polling, product inspection drawer,
 * instant taxonomy search, pre-import validation, and batch controls.
 */

// Toast notification helper
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${type === 'success' ? '✓' : type === 'error' ? '✕' : 'ℹ'}</span>
    <span class="toast-msg">${message}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Global state
let currentJobId = null;
let pollTimer = null;
let activeProductId = null;
let currentPage = 1;

// Initialize on DOM loaded
document.addEventListener('DOMContentLoaded', () => {
  initDashboardMetrics();
  initBatchJobPolling();
  initModals();
  initProductsTable();
  initTaxonomySearch();
});

// Dashboard Metrics & Live Job Monitor
function initDashboardMetrics() {
  const metricsContainer = document.getElementById('dashboard-metrics');
  if (!metricsContainer) return;

  // Load metrics once on page load (polling only runs when a job is active)
  fetchMetrics();
}

async function fetchMetrics() {
  try {
    const res = await fetch('/api/v1/metrics/');
    if (!res.ok) return;
    const data = await res.json();

    // Update KPI values
    updateElem('kpi-total-products', data.total_products.toLocaleString());
    updateElem('kpi-processed', data.processed_count.toLocaleString());
    updateElem('kpi-auto-classified', data.auto_classified_count.toLocaleString());
    updateElem('kpi-needs-review', data.needs_review_count.toLocaleString());
    updateElem('kpi-failed', data.failed_count.toLocaleString());
    updateElem('kpi-avg-confidence', `${data.average_confidence}%`);

    // Show/hide retry and failed queue links if failed items exist
    const retryBtn = document.getElementById('btn-retry-batch');
    const failedLink = document.getElementById('link-failed-queue');
    if (data.failed_count > 0) {
      if (retryBtn) retryBtn.style.display = 'inline-flex';
      if (failedLink) failedLink.style.display = 'inline-flex';
    } else {
      if (retryBtn && (!data.latest_job || data.latest_job.status !== 'running')) retryBtn.style.display = 'none';
      if (failedLink) failedLink.style.display = 'none';
    }

    if (data.latest_job) {
      updateJobProgressUI(data.latest_job);
      // Only start background polling if the job is actively running
      if (data.latest_job.status === 'running' && !pollTimer) {
        startPolling(data.latest_job.id);
      }
    }
  } catch (err) {
    console.error("Failed to fetch metrics:", err);
  }
}

function updateElem(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// Background Batch Job Controls
function initBatchJobPolling() {
  const startBtn = document.getElementById('btn-start-batch');
  const pauseBtn = document.getElementById('btn-pause-batch');
  const resumeBtn = document.getElementById('btn-resume-batch');
  const retryBtn = document.getElementById('btn-retry-batch');

  if (startBtn) {
    startBtn.addEventListener('click', async () => {
      startBtn.disabled = true;
      startBtn.innerHTML = '<span class="spinner"></span> Launching...';
      try {
        const res = await fetch('/api/v1/jobs/start/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chunk_size: 100, check_images: false })
        });
        const data = await res.json();
        if (data.success) {
          showToast("Batch classification job launched successfully!", "success");
          currentJobId = data.job.id;
          updateJobProgressUI(data.job);
          startPolling(currentJobId);
        } else {
          showToast(data.message || "Failed to start job", "error");
        }
      } catch (e) {
        showToast("Error starting batch job", "error");
      } finally {
        startBtn.disabled = false;
        startBtn.innerHTML = '▶ Start Batch Classification';
      }
    });
  }

  if (pauseBtn) {
    pauseBtn.addEventListener('click', async () => {
      if (!currentJobId) return;
      try {
        const res = await fetch(`/api/v1/jobs/${currentJobId}/pause/`, { method: 'POST' });
        const data = await res.json();
        showToast("Pause requested. Finishing active chunk...", "info");
      } catch (e) {
        showToast("Error pausing job", "error");
      }
    });
  }

  if (resumeBtn) {
    resumeBtn.addEventListener('click', async () => {
      if (!currentJobId) return;
      try {
        const res = await fetch(`/api/v1/jobs/${currentJobId}/resume/`, { method: 'POST' });
        const data = await res.json();
        showToast("Job resumed from where it left off!", "success");
        startPolling(currentJobId);
      } catch (e) {
        showToast("Error resuming job", "error");
      }
    });
  }

  if (retryBtn) {
    retryBtn.addEventListener('click', async () => {
      try {
        const url = currentJobId ? `/api/v1/jobs/${currentJobId}/retry/` : '/api/v1/jobs/retry/';
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast("Retry worker started for failed items!", "success");
          currentJobId = data.job.id;
          startPolling(currentJobId);
        } else {
          showToast(data.message || "No failed items to retry", "info");
        }
      } catch (e) {
        showToast("Error launching retry worker", "error");
      }
    });
  }
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/jobs/${jobId}/status/`);
      if (!res.ok) return;
      const job = await res.json();
      updateJobProgressUI(job);

      // Live update KPI counters as chunks finish
      if (job.processed_items !== undefined) {
        updateElem('kpi-processed', (job.processed_items || 0).toLocaleString());
        updateElem('kpi-auto-classified', (job.auto_classified_items || 0).toLocaleString());
        updateElem('kpi-needs-review', (job.needs_review_items || 0).toLocaleString());
        updateElem('kpi-failed', (job.failed_items || 0).toLocaleString());
      }

      if (job.status === 'completed' || job.status === 'failed' || job.status === 'paused') {
        clearInterval(pollTimer);
        pollTimer = null;
        fetchMetrics();
        if (job.status === 'completed') {
          showToast("🎉 Batch classification completed 100% for all items!", "success");
          if (typeof loadProductsPage === 'function') loadProductsPage(currentPage);
        }
      }
    } catch (e) {
      console.error(e);
    }
  }, 600);
}

function updateJobProgressUI(job) {
  currentJobId = job.id;
  const progressFill = document.getElementById('job-progress-fill');
  const progressText = document.getElementById('job-progress-text');
  const statusBadge = document.getElementById('job-status-badge');
  const typeBadge = document.getElementById('job-type-badge');
  const throughputEl = document.getElementById('job-throughput');
  const durationEl = document.getElementById('job-duration');
  const processedEl = document.getElementById('job-processed-stat');

  const isCompleted = job.status === 'completed';
  const pct = isCompleted ? 100 : (job.progress_percentage || 0);
  const processedCount = isCompleted ? job.total_items : job.processed_items;
  const currentChunk = isCompleted ? job.total_chunks : job.current_chunk;

  if (progressFill) progressFill.style.width = `${pct}%`;
  if (progressText) progressText.textContent = `${pct}%`;
  if (throughputEl) throughputEl.textContent = `${job.throughput_ips} items/sec`;
  if (durationEl) durationEl.textContent = `${job.duration_seconds}s`;
  if (processedEl) processedEl.textContent = `${processedCount.toLocaleString()} / ${job.total_items.toLocaleString()} items (${currentChunk}/${job.total_chunks} chunks)`;

  if (typeBadge && job.batch_type) {
    typeBadge.style.display = 'inline-flex';
    typeBadge.textContent = job.batch_type === 'retry' ? 'RETRY RUN' : 'BATCH RUN';
  }

  if (statusBadge) {
    statusBadge.textContent = job.status.toUpperCase();
    statusBadge.className = `badge ${
      job.status === 'running' ? 'badge-info' :
      job.status === 'completed' ? 'badge-success' :
      job.status === 'paused' ? 'badge-warning' :
      job.status === 'failed' ? 'badge-danger' : 'badge-secondary'
    }`;
  }

  // Toggle button visibility
  const startBtn = document.getElementById('btn-start-batch');
  const pauseBtn = document.getElementById('btn-pause-batch');
  const resumeBtn = document.getElementById('btn-resume-batch');

  if (startBtn && pauseBtn && resumeBtn) {
    if (job.status === 'running') {
      startBtn.style.display = 'none';
      pauseBtn.style.display = 'inline-flex';
      resumeBtn.style.display = 'none';
    } else if (job.status === 'paused') {
      startBtn.style.display = 'none';
      pauseBtn.style.display = 'none';
      resumeBtn.style.display = 'inline-flex';
    } else {
      startBtn.style.display = 'inline-flex';
      pauseBtn.style.display = 'none';
      resumeBtn.style.display = 'none';
    }
  }
}

// Interactive Product Table & Review Drawer
function initProductsTable() {
  const tableBody = document.getElementById('products-table-body');
  if (!tableBody) return;

  loadProductsPage(1);

  // Search and filter inputs
  const searchInput = document.getElementById('product-search-input');
  const statusFilter = document.getElementById('status-filter');
  const imgFilter = document.getElementById('image-status-filter');
  const confSlider = document.getElementById('confidence-slider');
  const confValDisplay = document.getElementById('confidence-slider-val');

  if (searchInput) {
    let debounce;
    searchInput.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => loadProductsPage(1), 300);
    });
  }

  if (statusFilter) {
    statusFilter.addEventListener('change', () => loadProductsPage(1));
  }

  if (imgFilter) {
    imgFilter.addEventListener('change', () => loadProductsPage(1));
  }

  if (confSlider && confValDisplay) {
    confSlider.addEventListener('input', (e) => {
      confValDisplay.textContent = `${e.target.value}%`;
      loadProductsPage(1);
    });
  }
}

async function loadProductsPage(page = 1) {
  currentPage = page;
  const tableBody = document.getElementById('products-table-body');
  if (!tableBody) return;

  const query = document.getElementById('product-search-input')?.value || '';
  const status = document.getElementById('status-filter')?.value || '';
  const imgStatus = document.getElementById('image-status-filter')?.value || '';
  const minConf = document.getElementById('confidence-slider')?.value || '';

  tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 2.5rem;"><span class="spinner"></span> Loading catalogue items...</td></tr>`;

  try {
    const url = `/api/v1/products/?page=${page}&q=${encodeURIComponent(query)}&status=${encodeURIComponent(status)}&image_status=${encodeURIComponent(imgStatus)}&min_confidence=${encodeURIComponent(minConf)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to load products');
    const data = await res.json();

    renderProductsTable(data.results);
    renderPagination(data.count, page, 25);
  } catch (e) {
    tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--danger); padding: 2rem;">Error loading products: ${e.message}</td></tr>`;
  }
}

function renderProductsTable(products) {
  const tableBody = document.getElementById('products-table-body');
  if (!tableBody) return;

  if (!products || products.length === 0) {
    tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 3rem; color: var(--text-muted);">No products found matching criteria.</td></tr>`;
    return;
  }

  tableBody.innerHTML = products.map(prod => {
    const cls = prod.classification || {};
    const effCat = cls.effective_category;
    const catPath = effCat ? effCat.full_name : (cls.predicted_category ? cls.predicted_category.full_name : '<span style="color: var(--text-dim);">Unclassified</span>');
    const conf = cls.confidence_score !== undefined ? Math.round(cls.confidence_score * 100) : 0;

    // Status Badge
    let badgeClass = 'badge-secondary';
    let statusLabel = cls.status || 'Pending';
    if (cls.status === 'auto_classified') { badgeClass = 'badge-info'; statusLabel = 'Auto Classified'; }
    else if (cls.status === 'approved') { badgeClass = 'badge-success'; statusLabel = 'Approved'; }
    else if (cls.status === 'needs_review') { badgeClass = 'badge-warning'; statusLabel = 'Needs Review'; }
    else if (cls.status === 'failed') { badgeClass = 'badge-danger'; statusLabel = 'Failed'; }

    // Confidence class
    const confClass = conf >= 70 ? 'conf-val-high' : conf >= 45 ? 'conf-val-med' : 'conf-val-low';

    // Thumbnail
    const imgHtml = prod.image_url 
      ? `<img src="${prod.image_url}" class="prod-thumb" alt="${prod.product_number}" onerror="this.outerHTML='<div class=\\'prod-thumb-placeholder\\'>IMG</div>'"/>`
      : `<div class="prod-thumb-placeholder">${cls.image_status === 'broken' ? 'BROKEN' : 'NO IMG'}</div>`;

    return `
      <tr onclick="openProductDrawer(${prod.id})" style="cursor: pointer;">
        <td style="width: 55px;">${imgHtml}</td>
        <td>
          <div style="font-weight:600; color:#fff;">${prod.product_number}</div>
          <div style="font-size:0.75rem; color:var(--text-dim);">${prod.model_number || ''}</div>
        </td>
        <td>
          <div style="font-weight:500; max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${prod.name}</div>
          <div style="font-size:0.75rem; color:var(--text-muted);">
            ${prod.brand ? `<strong style="color:#93c5fd;">${prod.brand}</strong> • ` : ''}
            ${prod.product_category || ''} ${prod.product_sub_category ? '› ' + prod.product_sub_category : ''}
          </div>
        </td>
        <td>
          <div style="font-size:0.85rem; max-width:300px; color:#c7d2fe;">${catPath}</div>
          ${cls.review_decision === 'human_overridden' ? '<span style="font-size:0.7rem; color:#34d399;">(Human Overridden)</span>' : ''}
        </td>
        <td>
          <div class="confidence-meter">
            <span style="font-weight:700; font-size:0.85rem; min-width:32px;">${conf}%</span>
            <div class="conf-bar-bg">
              <div class="conf-bar-val ${confClass}" style="width: ${conf}%;"></div>
            </div>
          </div>
        </td>
        <td><span class="badge ${badgeClass}">${statusLabel}</span></td>
        <td style="text-align: right;" onclick="event.stopPropagation();">
          <button class="btn btn-sm btn-secondary" onclick="openProductDrawer(${prod.id})">Review / Inspect</button>
          ${cls.status === 'failed' 
            ? `<button class="btn btn-sm btn-danger" style="margin-left:4px;" onclick="retrySingleProductItem(${prod.id})">↻ Retry</button>` 
            : cls.status !== 'approved' 
              ? `<button class="btn btn-sm btn-success" style="margin-left:4px;" onclick="approveProductQuick(${prod.id})">✓ Approve</button>` 
              : ''}
        </td>
      </tr>
    `;
  }).join('');
}

function renderPagination(totalCount, currentPage, pageSize) {
  const container = document.getElementById('pagination-container');
  if (!container) return;

  const totalPages = Math.ceil(totalCount / pageSize);
  if (totalPages <= 1) {
    container.innerHTML = `<span style="font-size:0.85rem; color:var(--text-muted);">Showing all ${totalCount} products</span>`;
    return;
  }

  container.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between; width:100%; flex-wrap:wrap; gap:0.5rem;">
      <span style="font-size:0.85rem; color:var(--text-muted);">Page ${currentPage} of ${totalPages} (${totalCount.toLocaleString()} products)</span>
      <div style="display:flex; gap:0.4rem;">
        <button class="btn btn-sm btn-secondary" ${currentPage <= 1 ? 'disabled' : ''} onclick="loadProductsPage(${currentPage - 1})">◀ Prev</button>
        <button class="btn btn-sm btn-secondary" ${currentPage >= totalPages ? 'disabled' : ''} onclick="loadProductsPage(${currentPage + 1})">Next ▶</button>
      </div>
    </div>
  `;
}

// Product Review Drawer
async function openProductDrawer(productId) {
  activeProductId = productId;
  const overlay = document.getElementById('drawer-overlay');
  const drawer = document.getElementById('product-drawer');
  if (!drawer) return;

  overlay.classList.add('active');
  drawer.classList.add('active');

  const content = document.getElementById('drawer-content');
  content.innerHTML = `<div style="text-align:center; padding: 3rem;"><span class="spinner"></span> Loading product details & classification signals...</div>`;

  try {
    const res = await fetch(`/api/v1/products/${productId}/`);
    if (!res.ok) throw new Error('Product not found');
    const prod = await res.json();
    renderProductDrawerContent(prod);
  } catch (e) {
    content.innerHTML = `<div style="color:var(--danger); padding:2rem;">Failed to load product: ${e.message}</div>`;
  }
}

function closeProductDrawer() {
  document.getElementById('drawer-overlay')?.classList.remove('active');
  document.getElementById('product-drawer')?.classList.remove('active');
  activeProductId = null;
}

function renderProductDrawerContent(prod) {
  const content = document.getElementById('drawer-content');
  const cls = prod.classification || {};
  const predCat = cls.predicted_category;
  const apprCat = cls.approved_category;
  const effCat = cls.effective_category || apprCat || predCat;
  const conf = Math.round((cls.confidence_score || 0) * 100);
  const breakdown = cls.confidence_breakdown || {};
  const evidenceList = breakdown.evidence || [];
  const signals = breakdown.signals || {};
  const alternatives = cls.alternatives || [];

  const breadcrumbs = effCat ? effCat.breadcrumbs.map((b, i, arr) => 
    `<span class="cat-node ${i === arr.length - 1 ? 'leaf' : ''}">${b}</span>`
  ).join(' <span class="cat-separator">›</span> ') : 'None';

  content.innerHTML = `
    <!-- Top Product Header -->
    <div style="display:flex; gap:1.25rem; align-items:flex-start; margin-bottom:1.5rem;">
      ${prod.image_url 
        ? `<img src="${prod.image_url}" style="width:90px; height:90px; border-radius:var(--radius-md); object-fit:cover; border:1px solid var(--border-color);" onerror="this.outerHTML='<div class=\\'prod-thumb-placeholder\\' style=\\'width:90px; height:90px;\\'>IMG ERROR</div>'"/>` 
        : `<div class="prod-thumb-placeholder" style="width:90px; height:90px;">${cls.image_status === 'broken' ? 'BROKEN URL' : 'NO IMG'}</div>`}
      <div style="flex:1;">
        <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.2rem;">
          <span class="badge badge-info">${prod.product_number}</span>
          ${prod.brand ? `<span class="badge badge-secondary">Brand: ${prod.brand}</span>` : ''}
          <span class="badge ${cls.image_status === 'available' ? 'badge-success' : cls.image_status === 'broken' ? 'badge-danger' : 'badge-secondary'}">
            Image: ${cls.image_status || 'unknown'}
          </span>
        </div>
        <h3 style="font-size:1.15rem; color:#fff; margin:0.2rem 0;">${prod.name}</h3>
        <div style="font-size:0.8rem; color:var(--text-muted);">
          Source: <strong>${prod.product_category || 'N/A'}</strong> › <strong>${prod.product_sub_category || 'N/A'}</strong>
        </div>
      </div>
    </div>

    <!-- Assigned / Predicted Shopify Category -->
    <div class="card" style="margin-bottom:1.25rem; background:rgba(99, 102, 241, 0.06); border-color:rgba(99, 102, 241, 0.25);">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.6rem;">
        <span style="font-size:0.8rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-muted); font-weight:600;">
          ${apprCat ? '★ Approved Category (Human Verified)' : 'Predicted Category (AI)'}
        </span>
        <span class="badge ${cls.status === 'approved' ? 'badge-success' : cls.status === 'auto_classified' ? 'badge-info' : 'badge-warning'}">
          ${(cls.status || 'Pending').toUpperCase()}
        </span>
      </div>
      <div style="font-size:1.15rem; font-weight:700; color:#fff; margin-bottom:0.3rem;">
        ${effCat ? effCat.name : 'Unclassified'}
      </div>
      <div class="cat-breadcrumb" style="margin-bottom:0.6rem;">${breadcrumbs}</div>
      <div style="display:flex; align-items:center; gap:0.5rem; background:rgba(0,0,0,0.2); padding:0.4rem 0.75rem; border-radius:var(--radius-sm); width:fit-content; max-width:100%;">
        <span style="font-size:0.75rem; color:var(--text-muted); font-weight:600; text-transform:uppercase;">Shopify Category ID:</span>
        <code style="font-size:0.8rem; color:#a5b4fc; font-family:var(--font-mono); word-break:break-all;">${effCat ? (effCat.id || 'N/A') : 'N/A'}</code>
      </div>

      <div style="margin-top:0.85rem; display:flex; align-items:center; justify-content:space-between; background:rgba(0,0,0,0.25); padding:0.6rem 0.9rem; border-radius:var(--radius-md);">
        <span style="font-size:0.85rem; color:var(--text-muted);">Confidence Score</span>
        <div style="display:flex; align-items:center; gap:0.5rem;">
          <span style="font-weight:700; color:#fff; font-size:1.1rem;">${conf}%</span>
          <div class="conf-bar-bg" style="width:80px; height:8px;">
            <div class="conf-bar-val ${conf >= 70 ? 'conf-val-high' : conf >= 45 ? 'conf-val-med' : 'conf-val-low'}" style="width:${conf}%;"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Classification Evidence & Signal Breakdown -->
    <div class="card" style="margin-bottom:1.25rem;">
      <h4 style="font-size:0.85rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em;">Classification Evidence & Signals</h4>
      <div class="evidence-list">
        ${evidenceList.length > 0 ? evidenceList.map(ev => `
          <div class="evidence-item ${ev.startsWith('✓') ? 'strong' : ev.startsWith('⚠') ? 'warning' : ''}">
            ${ev}
          </div>
        `).join('') : '<div style="font-size:0.8rem; color:var(--text-dim);">No detailed evidence generated.</div>'}
      </div>

      <!-- Signals Grid -->
      ${Object.keys(signals).length > 0 ? `
        <div class="signals-grid">
          ${Object.entries(signals).map(([k, v]) => `
            <div class="signal-box">
              <div class="signal-name">${k.replace('_', ' ')}</div>
              <div class="signal-val">${Math.round((v.score || 0) * 100)}% <span style="font-size:0.75rem; color:var(--text-muted); font-weight:normal;">(wt: ${Math.round((v.normalized_weight || 0) * 100)}%)</span></div>
            </div>
          `).join('')}
        </div>
      ` : ''}
    </div>

    <!-- Top Alternative Suggestions -->
    <div style="margin-bottom:1.25rem;">
      <h4 style="font-size:0.85rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:0.6rem;">Alternative Candidates</h4>
      ${alternatives.length > 0 ? alternatives.map(alt => `
        <div class="alt-card">
          <div>
            <div style="font-weight:600; color:#fff; font-size:0.9rem;">${alt.name}</div>
            <div style="font-size:0.75rem; color:var(--text-muted);">${alt.full_name}</div>
          </div>
          <div style="display:flex; align-items:center; gap:0.6rem;">
            <span style="font-weight:600; font-size:0.85rem; color:var(--text-muted);">${alt.confidence_percent}</span>
            <button class="btn btn-sm btn-secondary" onclick="assignCategory('${alt.id}', '${alt.name.replace(/'/g, "\\'")}')">Choose</button>
          </div>
        </div>
      `).join('') : '<div style="font-size:0.85rem; color:var(--text-dim);">No alternatives generated.</div>'}
    </div>

    <!-- Manual Taxonomy Category Search -->
    <div style="margin-bottom:1.25rem;">
      <label class="form-label">Search & Assign Any Other Category</label>
      <div style="position:relative;">
        <input type="text" id="manual-cat-search" class="form-control" placeholder="Search category (e.g. Sectional Sofas, Armchairs, Lighting)..." oninput="handleManualCatSearch(this.value)"/>
        <div id="cat-search-results" style="position:absolute; top:100%; left:0; right:0; background:var(--bg-secondary); border:1px solid var(--border-color); border-radius:var(--radius-md); max-height:220px; overflow-y:auto; z-index:30; display:none;"></div>
      </div>
    </div>

    <!-- Shopify Category Attributes (Source: Shopify Taxonomy) -->
    <div class="card" style="margin-bottom:1.25rem; background:rgba(99, 102, 241, 0.04); border-color:rgba(99, 102, 241, 0.25);">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem; flex-wrap:wrap; gap:0.5rem;">
        <div>
          <h4 style="font-size:0.95rem; color:#fff; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; margin:0; display:flex; align-items:center; gap:0.45rem;">
            <span style="color:var(--primary-color);">🏷️</span> SHOPIFY CATEGORY ATTRIBUTES
          </h4>
          <div style="font-size:0.75rem; color:#a5b4fc; margin-top:0.15rem;">SHOPIFY STANDARD TAXONOMY</div>
        </div>
        <span class="badge badge-info" style="font-size:0.7rem; letter-spacing:0.02em;">Category-Specific</span>
      </div>

      <div style="background:rgba(0, 0, 0, 0.25); border:1px solid rgba(255, 255, 255, 0.08); border-radius:var(--radius-md); padding:0.6rem 0.85rem; margin-bottom:0.85rem;">
        <div style="font-size:0.7rem; color:var(--text-muted); text-transform:uppercase; font-weight:600; margin-bottom:0.15rem;">
          CATEGORY CONTEXT
        </div>
        <div style="font-size:0.85rem; color:#e2e8f0; font-weight:600;">
          ${effCat ? (effCat.full_name || effCat.name) : 'Unclassified Category'}
        </div>
      </div>

      <div class="attr-grid" id="drawer-shopify-attrs">
        ${(() => {
          const rawShopifyAttrs = cls.shopify_category_attributes || [];
          const forbidden = new Set([
            'SHOPIFY_CATEGORY_ATTRIBUTES', 'PRODUCT_SPECIFICATIONS',
            'ASSEMBLY REQUIRED', 'WEIGHT CAPACITY', 'PRODUCT WEIGHT', 'DIMENSIONS',
            'COUNTRY OF ORIGIN', 'COLLECTION', 'COLLECTION NAME', 'BRAND',
            'MODEL NUMBER', 'SET INCLUDES', 'IS A SET'
          ]);

          const validAttrs = rawShopifyAttrs.filter(attr => {
            if (!attr || !attr.attribute_name) return false;
            const nameUpper = String(attr.attribute_name).toUpperCase().trim();
            return !forbidden.has(nameUpper);
          });

          if (validAttrs.length > 0) {
            return validAttrs.map(attr => {
              const isDetected = attr.status === 'detected' || (attr.normalized_value || attr.raw_value || attr.matched_shopify_value);
              const normVal = attr.normalized_value || attr.raw_value || 'Not detected';
              const rawVal = attr.raw_value || '';
              const matchedVal = attr.matched_shopify_value || '';
              return `
                <div class="attr-card" style="background:rgba(255, 255, 255, 0.04); border-color:${isDetected ? 'rgba(99, 102, 241, 0.25)' : 'rgba(255, 255, 255, 0.06)'};">
                  <div class="attr-name" style="color:#94a3b8; font-weight:600; font-size:0.75rem; display:flex; justify-content:space-between; align-items:center;">
                    <span>${attr.attribute_name}</span>
                    ${isDetected 
                      ? (matchedVal ? `<span class="attr-norm-badge" style="background:rgba(16,185,129,0.2); color:#34d399;">Canonical</span>` : `<span class="attr-norm-badge" style="background:rgba(99,102,241,0.2); color:#a5b4fc;">Detected</span>`)
                      : `<span style="font-size:0.65rem; color:var(--text-dim); background:rgba(255,255,255,0.05); padding:1px 5px; border-radius:3px;">Not detected</span>`}
                  </div>
                  <div class="attr-val" style="color:${isDetected ? '#f8fafc' : 'var(--text-dim)'}; font-size:0.95rem; margin-top:0.35rem; font-style:${isDetected ? 'normal' : 'italic'}; font-weight:${isDetected ? '600' : 'normal'};">
                    ${isDetected ? (matchedVal || normVal) : 'Not detected'}
                  </div>
                  ${isDetected && rawVal && rawVal.toLowerCase() !== (matchedVal || normVal).toLowerCase() ? `
                    <div class="attr-raw-tag" style="margin-top:0.35rem; color:#94a3b8; font-size:0.75rem;">
                      <span style="color:#64748b;">Raw:</span> "${rawVal}"
                    </div>
                  ` : ''}
                  ${isDetected && matchedVal && matchedVal.toLowerCase() !== normVal.toLowerCase() ? `
                    <div class="attr-raw-tag" style="margin-top:0.2rem; color:#34d399; font-size:0.75rem;">
                      <span style="color:#059669;">Matched:</span> ${matchedVal}
                    </div>
                  ` : ''}
                  <div style="margin-top:0.35rem; font-size:0.65rem; color:var(--text-dim); font-family:var(--font-mono);">
                    ${attr.attribute_id || ''}
                  </div>
                </div>
              `;
            }).join('');
          }

          return '<div style="color:var(--text-dim); font-size:0.85rem; padding:0.5rem 0;">No Shopify taxonomy attributes defined for this category.</div>';
        })()}
      </div>
    </div>

    <!-- Description & Raw Signals -->
    <div class="card" style="margin-bottom:1.5rem; background:rgba(0,0,0,0.2);">
      <h4 style="font-size:0.85rem; color:var(--text-muted); margin-bottom:0.4rem;">Product Description & Raw Bullets</h4>
      <p style="font-size:0.85rem; color:#d1d5db; line-height:1.6; max-height:120px; overflow-y:auto;">
        ${prod.description || '<em style="color:var(--text-dim);">No description provided</em>'}
      </p>
      ${prod.materials ? `<div style="font-size:0.8rem; margin-top:0.5rem; color:#93c5fd;"><strong>Materials:</strong> ${prod.materials}</div>` : ''}
      ${prod.bullets ? `<div style="font-size:0.8rem; margin-top:0.25rem; color:#a7f3d0;"><strong>Bullets:</strong> ${prod.bullets.replace(/\n/g, ' • ')}</div>` : ''}
    </div>

    <!-- Action Buttons -->
    <div style="display:flex; gap:0.75rem; margin-top:auto; padding-top:1rem; border-top:1px solid var(--border-color);">
      <button class="btn btn-success" style="flex:1;" onclick="approveActiveProduct()">✓ Approve Classification</button>
      <button class="btn btn-secondary" onclick="retrySingleProductItem(${prod.id})">↻ Retry Item</button>
      <button class="btn btn-secondary" onclick="closeProductDrawer()">Close</button>
    </div>
  `;
}

// Category Assignment & Approval Actions
async function assignCategory(categoryId, categoryName) {
  if (!activeProductId) return;
  try {
    const res = await fetch(`/api/v1/products/${activeProductId}/update-category/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_id: categoryId, reviewer: 'Manual Review' })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Category updated to "${categoryName}"!`, "success");
      openProductDrawer(activeProductId);
      loadProductsPage(currentPage);
      fetchMetrics();
    }
  } catch (e) {
    showToast("Failed to assign category", "error");
  }
}

async function approveActiveProduct() {
  if (!activeProductId) return;
  try {
    const res = await fetch(`/api/v1/products/${activeProductId}/approve/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewer: 'Reviewer' })
    });
    const data = await res.json();
    if (data.success) {
      showToast(data.message, "success");
      closeProductDrawer();
      loadProductsPage(currentPage);
      fetchMetrics();
    }
  } catch (e) {
    showToast("Failed to approve product", "error");
  }
}

async function approveProductQuick(productId) {
  try {
    const res = await fetch(`/api/v1/products/${productId}/approve/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reviewer: 'Quick Review' })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Product approved!", "success");
      loadProductsPage(currentPage);
      fetchMetrics();
    }
  } catch (e) {
    showToast("Error approving product", "error");
  }
}

async function retrySingleProductItem(productId) {
  try {
    showToast("Retrying classification...", "info");
    const res = await fetch(`/api/v1/products/${productId}/retry/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ check_image: true })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Product re-classified successfully!", "success");
      if (activeProductId === productId) openProductDrawer(productId);
      loadProductsPage(currentPage);
      fetchMetrics();
    } else {
      showToast(data.error || "Retry failed", "error");
    }
  } catch (e) {
    showToast("Error retrying item", "error");
  }
}

// Category search autocomplete
let searchDebounce;
function handleManualCatSearch(val) {
  clearTimeout(searchDebounce);
  const container = document.getElementById('cat-search-results');
  if (!container) return;

  if (!val || val.length < 2) {
    container.style.display = 'none';
    return;
  }

  searchDebounce = setTimeout(async () => {
    try {
      const res = await fetch(`/api/v1/taxonomy/search/?q=${encodeURIComponent(val)}`);
      const cats = await res.json();
      if (cats.length === 0) {
        container.innerHTML = '<div style="padding:0.75rem; color:var(--text-muted); font-size:0.85rem;">No matching taxonomy categories</div>';
      } else {
        container.innerHTML = cats.map(c => `
          <div style="padding:0.6rem 0.9rem; border-bottom:1px solid var(--border-color); cursor:pointer; font-size:0.85rem;" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='transparent'" onclick="assignCategory('${c.id}', '${c.name.replace(/'/g, "\\'")}')">
            <div style="font-weight:600; color:#fff;">${c.name}</div>
            <div style="font-size:0.75rem; color:var(--text-muted);">${c.full_name}</div>
          </div>
        `).join('');
      }
      container.style.display = 'block';
    } catch (e) {
      console.error(e);
    }
  }, 250);
}

function initTaxonomySearch() {
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#manual-cat-search') && !e.target.closest('#cat-search-results')) {
      const container = document.getElementById('cat-search-results');
      if (container) container.style.display = 'none';
    }
  });
}

// Modal handling with Pre-Upload Validation Preview
function initModals() {
  const uploadBtn = document.getElementById('btn-open-upload');
  const uploadModal = document.getElementById('upload-modal');
  const closeUploadBtn = document.getElementById('btn-close-upload');
  const uploadForm = document.getElementById('upload-form');
  const fileInput = document.getElementById('upload-file-input');

  if (uploadBtn && uploadModal) {
    uploadBtn.addEventListener('click', () => uploadModal.classList.add('active'));
  }
  if (closeUploadBtn && uploadModal) {
    closeUploadBtn.addEventListener('click', () => uploadModal.classList.remove('active'));
  }

  // Pre-validate file when selected
  if (fileInput) {
    fileInput.addEventListener('change', async () => {
      if (!fileInput.files.length) return;
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);

      let previewBox = document.getElementById('upload-validation-preview');
      if (!previewBox) {
        previewBox = document.createElement('div');
        previewBox.id = 'upload-validation-preview';
        fileInput.parentElement.appendChild(previewBox);
      }
      previewBox.innerHTML = '<div style="font-size:0.85rem; color:var(--text-muted); margin-top:0.75rem;"><span class="spinner"></span> Validating catalogue integrity...</div>';

      try {
        const res = await fetch('/api/v1/import/validate/', {
          method: 'POST',
          body: formData
        });
        const summary = await res.json();
        if (summary.is_valid) {
          previewBox.innerHTML = `
            <div class="validation-preview-card">
              <div style="font-weight:700; color:#fff; font-size:0.9rem; margin-bottom:0.5rem;">File Integrity Validation</div>
              <div class="preview-metric-row"><span>Rows Detected:</span><strong>${summary.total_rows.toLocaleString()}</strong></div>
              <div class="preview-metric-row"><span>New Products to Create:</span><strong style="color:#34d399;">${summary.new_products.toLocaleString()}</strong></div>
              <div class="preview-metric-row"><span>Existing Products (Upsert):</span><strong>${summary.existing_products.toLocaleString()}</strong></div>
              <div class="preview-metric-row"><span>Duplicates in File:</span><strong>${summary.duplicates_in_file}</strong></div>
              <div class="preview-metric-row"><span>Missing Optional Fields:</span><span>${summary.missing_optional_fields}</span></div>
              ${summary.warnings && summary.warnings.length > 0 ? `
                <div style="margin-top:0.5rem; font-size:0.75rem; color:#fbbf24;">
                  ${summary.warnings.slice(0, 2).map(w => `<div>⚠ ${w}</div>`).join('')}
                </div>
              ` : ''}
            </div>
          `;
        } else {
          previewBox.innerHTML = `<div style="color:var(--danger); font-size:0.85rem; margin-top:0.5rem;">Validation Error: ${summary.errors?.[0] || 'Invalid file'}</div>`;
        }
      } catch (e) {
        previewBox.innerHTML = `<div style="color:var(--danger); font-size:0.85rem; margin-top:0.5rem;">Validation failed: ${e.message}</div>`;
      }
    });
  }

  if (uploadForm) {
    uploadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!fileInput.files.length) {
        showToast("Please select a file to upload", "error");
        return;
      }
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);

      const submitBtn = uploadForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner"></span> Ingesting Catalogue...';

      try {
        const res = await fetch('/api/v1/import/', {
          method: 'POST',
          body: formData
        });
        const data = await res.json();
        if (data.valid_rows !== undefined) {
          showToast(`Ingested ${data.imported} new products (${data.updated} existing updated)!`, "success");
          uploadModal.classList.remove('active');
          uploadForm.reset();
          const previewBox = document.getElementById('upload-validation-preview');
          if (previewBox) previewBox.innerHTML = '';
          fetchMetrics();
          if (typeof loadProductsPage === 'function') loadProductsPage(1);
        } else {
          showToast(data.errors?.[0] || "Upload failed", "error");
        }
      } catch (err) {
        showToast("File upload error", "error");
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = 'Confirm & Ingest Products';
      }
    });
  }
}

// Global Export Dropdown Toggle
function toggleExportMenu(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  const menu = document.getElementById('export-dropdown-menu');
  const chevron = document.getElementById('export-chevron');
  if (menu) {
    const isShown = menu.style.display === 'flex';
    menu.style.display = isShown ? 'none' : 'flex';
    if (chevron) chevron.style.transform = isShown ? 'rotate(0deg)' : 'rotate(180deg)';
  }
}

function closeExportMenu() {
  const menu = document.getElementById('export-dropdown-menu');
  const chevron = document.getElementById('export-chevron');
  if (menu) menu.style.display = 'none';
  if (chevron) chevron.style.transform = 'rotate(0deg)';
}

document.addEventListener('click', (e) => {
  const dropdown = document.getElementById('export-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    closeExportMenu();
  }
});

