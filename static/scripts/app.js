let currentScan = null;

const $ = (sel) => document.querySelector(sel);

function setStatus(message, isError = false) {
  const el = $("#scanStatus");
  el.textContent = message;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
}

function money(v) {
  return v == null ? "" : `$${Number(v).toFixed(2)}`;
}

async function api(url, options = {}) {
  const res = await fetch(url, options);
  let body = {};
  try { body = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(body.detail || `Request failed: ${res.status}`);
  return body;
}

async function loadDashboard() {
  const data = await api("/api/dashboard");
  $("#productCount").textContent = data.products.length;
  $("#receiptCount").textContent = data.receipt_count;
  $("#unknownCount").textContent = data.unknown_count;

  const tbody = $("#inventoryTableBody");
  tbody.innerHTML = "";
  for (const p of data.products) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.receipt_code_raw)}</td>
      <td><input data-quantity type="number" min="0" step="any" value="${p.inventory_quantity ?? 0}" aria-label="Quantity"><button data-save>Save</button></td>
      <td>${escapeHtml(p.inventory_location || "")}</td>
    `;
    tr.querySelector('[data-save]').addEventListener('click', async () => {
      const quantity = Number(tr.querySelector('[data-quantity]').value);
      if (!Number.isFinite(quantity) || quantity < 0) return setStatus('Enter a nonnegative quantity.', true);
      try {
        await api(`/api/inventory/${p.id}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quantity }),
        });
        await loadDashboard();
        setStatus('Inventory updated.');
      } catch (err) { setStatus(err.message, true); }
    });
    tbody.appendChild(tr);
  }
}

async function handleImage(file, sourceType) {
  if (!file) return;
  setStatus("Scanning receipt image…");
  const fd = new FormData();
  fd.append("source_type", sourceType);
  fd.append("file", file);

  try {
    const scan = await api("/api/receipts/scan-image", {
      method: "POST",
      body: fd,
    });
    await showReview(scan);
    setStatus("Receipt scanned. Review the results below.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function showReview(scan) {
  currentScan = scan;
  const parsed = scan.parsed;
  const found = parsed.items.length;
  const expected = parsed.expected_item_count;
  $('#receiptItemSummary').textContent = expected != null
    ? `${found} receipt line(s) scanned; receipt lists ${expected} item(s).${found < expected ? ' Check for missing items before importing.' : ''}`
    : `${found} receipt line(s) scanned.`;
  if (scan.meijer_lookup_errors?.length) {
    $('#receiptItemSummary').textContent += ` Meijer lookup: ${scan.meijer_lookup_errors.join(' ')}`;
  }

  const resolved = await api("/api/receipts/resolve-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parsed, meijer_products: scan.meijer_products || {} }),
  });

  currentScan.resolution = resolved;

  const meta = $("#receiptMeta");
  meta.innerHTML = `
    <div><small>Date</small><strong>${escapeHtml(parsed.purchase_date || "Unknown")}</strong></div>
    <div><small>Store</small><strong>${escapeHtml(parsed.store_number || "Unknown")}</strong></div>
    <div><small>Terminal</small><strong>${escapeHtml(parsed.terminal || "Unknown")}</strong></div>
    <div><small>Total</small><strong>${money(parsed.total)}</strong></div>
  `;

  const tbody = $("#reviewTableBody");
  tbody.innerHTML = "";
  for (const item of resolved.items) {
    let status = item.status;
    let label = status;
    if (status === "resolved" && item.product) label = item.product.name;
    if (status === "suggested" && item.suggestion) label = `Suggested: ${item.suggestion.name}`;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(item.raw_code || "")}</td>
      <td>${escapeHtml(item.receipt_description || "")}</td>
      <td>${item.quantity ?? 1}</td>
      <td>${money(item.line_total)}</td>
      <td>
        <span class="badge ${status}">${escapeHtml(status)}</span>
        <div>${escapeHtml(label)}</div>
        <a href="${item.meijer_search_url}" target="_blank" rel="noopener">Search Meijer</a>
      </td>
    `;
    if (status === 'suggested') {
      const candidates = item.candidates || [item.suggestion];
      const cell = tr.lastElementChild;
      const select = document.createElement('select');
      select.dataset.productMatch = item.raw_code;
      select.setAttribute('aria-label', `Product match for ${item.receipt_description}`);
      select.innerHTML = '<option value="">Leave unidentified</option>' + candidates.map((candidate, index) =>
        `<option value="${index}">${escapeHtml(candidate.name)}${candidate.size ? ' — ' + escapeHtml(candidate.size) : ''}</option>`).join('');
      select.value = candidates[0].match_kind === 'exact_code' ? '0' : '';
      const form = document.createElement('div');
      form.className = 'match-fields';
      form.innerHTML = '<input data-match-name aria-label="Product name" placeholder="Product name"><input data-match-brand aria-label="Brand" placeholder="Brand"><input data-match-size aria-label="Size" placeholder="Size">';
      const note = document.createElement('small');
      const productLink = document.createElement('a');
      productLink.textContent = 'View Meijer product';
      productLink.target = '_blank';
      productLink.rel = 'noopener';
      const update = () => {
        const candidate = select.value === '' ? null : candidates[Number(select.value)];
        form.querySelector('[data-match-name]').value = candidate?.name || '';
        form.querySelector('[data-match-brand]').value = candidate?.brand || '';
        form.querySelector('[data-match-size]').value = candidate?.size || '';
        note.textContent = candidate ? (candidate.match_kind === 'exact_code' ? 'Receipt code matches Meijer. Saved when you import.' : 'Check this product before importing.') : 'Choose a product to identify this receipt code.';
        productLink.hidden = !candidate?.url;
        if (candidate?.url) productLink.href = candidate.url;
        select.matchCandidate = candidate;
      };
      select.addEventListener('change', update);
      cell.appendChild(select);
      cell.appendChild(form);
      cell.appendChild(note);
      cell.appendChild(productLink);
      update();
    }
    tbody.appendChild(tr);
  }

  $("#reviewPanel").classList.remove("hidden");
}

async function importCurrentReceipt() {
  if (!currentScan) return;
  try {
    $('#importReceiptBtn').disabled = true;
    for (const select of document.querySelectorAll('[data-product-match]')) {
      if (select.value === '') continue;
      const form = select.parentElement.querySelector('.match-fields');
      const candidate = select.matchCandidate;
      const name = form.querySelector('[data-match-name]').value.trim();
      if (!name) throw new Error('A selected product needs a name.');
      await api('/api/products', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({receipt_code_raw: select.dataset.productMatch, name,
          brand: form.querySelector('[data-match-brand]').value.trim() || null,
          size: form.querySelector('[data-match-size]').value.trim() || null,
          category: candidate.category || null, lookup_source: candidate.lookup_source || 'manual'}),
      });
    }
    const result = await api("/api/receipts/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_type: currentScan.source_type,
        source_format: currentScan.source_format,
        raw_text: currentScan.raw_text,
        ocr_text: currentScan.ocr_text,
        parsed: currentScan.parsed,
      }),
    });
    setStatus(`Receipt #${result.receipt_id}: imported ${result.imported_items} receipt line(s). ${result.resolved_items} identified line(s) added to inventory; ${result.unresolved_items} line(s) are saved under Unknown Products.`);
    $("#reviewPanel").classList.add("hidden");
    currentScan = null;
    await Promise.all([loadDashboard(), loadUnknown()]);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    $('#importReceiptBtn').disabled = false;
  }
}

async function loadUnknown() {
  const rows = await api("/api/unknown-products");
  const container = $("#unknownProducts");
  container.innerHTML = "";

  if (!rows.length) {
    container.innerHTML = `<p>No unknown products.</p>`;
    return;
  }

  for (const row of rows) {
    const card = document.createElement("article");
    card.className = "unknown-card";
    card.innerHTML = `
      <div class="unknown-header">
        <div>
          <strong>${escapeHtml(row.raw_code || "")}</strong>
          <div>${escapeHtml(row.description || "")}</div>
        </div>
        <a class="button secondary" href="${row.meijer_search_url}" target="_blank" rel="noopener">Search Meijer</a>
      </div>
      <div class="form-grid">
        <input data-field="name" placeholder="Product name">
        <input data-field="brand" placeholder="Brand">
        <input data-field="size" placeholder="Size (e.g. 12 oz)">
        <input data-field="category" placeholder="Category">
        <input data-field="default_location" placeholder="Default location">
      </div>
      <div class="form-actions">
        <button data-action="save">Save Product</button>
      </div>
    `;
    card.querySelector('[data-action="save"]').addEventListener("click", async () => {
      const payload = {};
      for (const input of card.querySelectorAll("[data-field]")) {
        payload[input.dataset.field] = input.value.trim() || null;
      }
      if (!payload.name) {
        alert("Enter a product name.");
        return;
      }
      try {
        await api(`/api/unknown-products/${row.receipt_item_id}/resolve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        await Promise.all([loadDashboard(), loadUnknown()]);
      } catch (err) {
        alert(err.message);
      }
    });
    container.appendChild(card);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$("#uploadInput").addEventListener("change", (e) => handleImage(e.target.files[0], "upload"));
$("#cameraInput").addEventListener("change", (e) => handleImage(e.target.files[0], "camera"));
$("#importReceiptBtn").addEventListener("click", importCurrentReceipt);
$("#refreshBtn").addEventListener("click", () => Promise.all([loadDashboard(), loadUnknown()]));
$("#loadUnknownBtn").addEventListener("click", loadUnknown);


Promise.all([loadDashboard(), loadUnknown()]).catch((err) => setStatus(err.message, true));

async function receiveBrowserReceipt() {
  const params = new URLSearchParams(location.hash.slice(1));
  const text = params.get('receipt');
  const draft = params.get('draft');
  if (!text && !draft) return;
  history.replaceState(null, '', location.pathname + location.search);
  try {
    const scan = draft ? await api(`/api/receipts/browser-drafts/${encodeURIComponent(draft)}`) : await api('/api/receipts/scan-text', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, source_type: 'safari_extension' }),
    });
    await showReview(scan);
    setStatus('Browser receipt received. Review it before importing.');
  } catch (err) { setStatus(err.message, true); }
}
receiveBrowserReceipt();
