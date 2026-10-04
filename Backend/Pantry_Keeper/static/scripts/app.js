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

async function apiFile(url, file) {
  const fd = new FormData();
  fd.append("file", file);
  return api(url, { method: "POST", body: fd });
}

function openExternalProductSearch(url) {
  // Browser security prevents a web application from forcing Safari Private
  // Browsing. This function is intentionally isolated so a future local macOS
  // helper can open searches in a private window.
  window.open(url, "_blank", "noopener,noreferrer");
}

async function copySearchUrl(url) {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(url);
    } else {
      const input = document.createElement('textarea');
      input.value = url;
      document.body.appendChild(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) throw new Error('Copy unavailable');
    }
    setStatus('Search URL copied. Paste it into a Safari Private Browsing window.');
  } catch {
    window.prompt('Copy this URL into Safari Private Browsing:', url);
  }
}

function addSearchControls(container, url) {
  if (!url) return;
  const controls = document.createElement('div');
  controls.className = 'search-actions';
  for (const [label, action] of [['Search Meijer', () => openExternalProductSearch(url)],
                               ['Copy Search URL', () => copySearchUrl(url)]]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = label;
    button.addEventListener('click', action);
    controls.appendChild(button);
  }
  container.appendChild(controls);
}

function productFieldsHtml(upc, suggestion = {}) {
  return `<label>UPC<input data-field="upc" value="${escapeHtml(upc)}" readonly></label>` +
    [['name', 'Product Name'], ['brand', 'Brand'], ['size', 'Size'], ['category', 'Category'],
     ['unit', 'Default Unit'], ['notes', 'Notes'], ['default_location', 'Default Location']]
      .map(([field, label]) => `<label>${label}<input data-field="${field}" data-match-${field}
        value="${escapeHtml(suggestion[field] || '')}" aria-label="${label}"></label>`).join('');
}

function readProductFields(container) {
  const payload = {};
  for (const input of container.querySelectorAll('[data-field]')) {
    payload[input.dataset.field] = input.value.trim() || null;
  }
  return payload;
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
      <td>${escapeHtml(p.name)}${p.usable_quantity != null ? `<br><small>Usable: ${escapeHtml(p.usable_quantity)} ${escapeHtml(p.usable_unit || '')} · Reserved: ${escapeHtml(p.reserved_quantity || 0)}</small>` : ''}</td>
      <td>${escapeHtml(p.upc || p.receipt_code_raw)}</td>
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
        <span class="badge ${status}">${status === 'resolved' ? 'Known · Pantry Keeper' : status === 'suggested' ? 'Suggested · External Lookup' : 'Unknown · Needs Identification'}</span>
        <div>${escapeHtml(label)}</div>

      </td>
    `;
    if (status !== 'resolved') addSearchControls(tr.lastElementChild, item.meijer_search_url);
    if (status === 'suggested') {
      const candidates = item.candidates || [item.suggestion];
      const cell = tr.lastElementChild;
      const select = document.createElement('select');
      select.dataset.productMatch = item.raw_code;
      select.setAttribute('aria-label', `Product match for ${item.receipt_description}`);
      select.innerHTML = '<option value="">Leave unidentified</option>' + candidates.map((candidate, index) =>
        `<option value="${index}">${escapeHtml(candidate.name)}${candidate.size ? ' — ' + escapeHtml(candidate.size) : ''}</option>`).join('');
      select.value = '0';
      const form = document.createElement('div');
      form.className = 'match-fields';
      form.innerHTML = productFieldsHtml(item.upc || item.normalized_code || item.raw_code);
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
        for (const field of ['category', 'unit', 'notes', 'default_location']) {
          form.querySelector(`[data-field="${field}"]`).value = candidate?.[field] || '';
        }
        note.textContent = candidate ? (candidate.match_kind === 'exact_code' ? 'Receipt code matches Meijer. Saved when you import.' : 'Suggested match. Check it before importing; saved unless you choose Leave unidentified.') : 'Choose a product to identify this receipt code.';
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
    } else if (status === 'unresolved') {
      const cell = tr.lastElementChild;
      const editor = document.createElement('details');
      editor.innerHTML = `<summary>Enter Product Manually</summary><div class="match-fields" data-manual-upc="${escapeHtml(item.upc || item.raw_code)}">
        ${productFieldsHtml(item.upc || item.raw_code)}
        <button type="button" data-save-product>Save Product</button></div>`;
      editor.querySelector('[data-save-product]').addEventListener('click', async () => {
        const form = editor.querySelector('[data-manual-upc]');
        try {
          const product = await api('/api/products', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...readProductFields(form), lookup_source: form.dataset.lookupSource || 'manual'})});
          cell.innerHTML = `<span class="badge resolved">Known · Pantry Keeper</span><div>${escapeHtml(product.name)}</div>`;
          await Promise.all([loadDashboard(), loadUnknown()]);
          setStatus('Product saved. Import the receipt to add this purchase to inventory.');
        } catch (err) { setStatus(err.message, true); }
      });
      cell.appendChild(editor);
      if (['RATE_LIMITED', 'DEFERRED', 'ERROR'].includes(item.lookup_status)) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'secondary';
        retry.textContent = 'Try public UPC lookup';
        retry.addEventListener('click', async () => {
          try {
            const result = await api(`/api/products/lookup?upc=${encodeURIComponent(item.upc || item.raw_code)}`);
            const form = editor.querySelector('[data-manual-upc]');
            if (result.suggestion) {
              for (const input of form.querySelectorAll('[data-field]')) {
                if (input.dataset.field !== 'upc' && result.suggestion[input.dataset.field]) input.value = result.suggestion[input.dataset.field];
              }
              editor.open = true;
              form.dataset.lookupSource = result.suggestion.lookup_source;
              setStatus('Public lookup suggestion loaded. Review and save the product.');
            } else setStatus(result.product ? `Known product: ${result.product.name}` : 'No suggestion available yet. You can enter this product manually.');
          } catch (err) { setStatus(err.message, true); }
        });
        cell.appendChild(retry);
      }
    }
    tbody.appendChild(tr);
  }

  $("#reviewPanel").classList.remove("hidden");
}

async function importCurrentReceipt() {
  if (!currentScan) return;
  try {
    $('#importReceiptBtn').disabled = true;
    const selectedProducts = {};
    for (const select of document.querySelectorAll('[data-product-match]')) {
      if (select.value === '') continue;
      const form = select.parentElement.querySelector('.match-fields');
      const candidate = select.matchCandidate;
      const name = form.querySelector('[data-match-name]').value.trim();
      if (!name) throw new Error('A selected product needs a name.');
      selectedProducts[select.dataset.productMatch] = {...readProductFields(form),
        lookup_source: candidate.lookup_source || 'manual'};
    }
    for (const form of document.querySelectorAll('[data-manual-upc]')) {
      const payload = readProductFields(form);
      if (payload.name) selectedProducts[form.dataset.manualUpc] = {...payload, lookup_source: form.dataset.lookupSource || 'manual'};
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
        selected_products: selectedProducts,
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
    container.innerHTML = '<p>No unknown products.</p>';
    return;
  }
  for (const row of rows) {
    const card = document.createElement('article');
    card.className = 'unknown-card';
    card.innerHTML = `<div class="unknown-header"><div><strong>Unknown Product · UPC: ${escapeHtml(row.upc || row.raw_code)}</strong>
      <div>${escapeHtml(row.description || '')}</div><span class="badge unresolved">Needs Identification</span></div></div>
      <details open><summary>Enter Product Manually</summary><div class="form-grid">${productFieldsHtml(row.upc || row.raw_code, row.suggestion || {})}</div></details>
      <div class="form-actions"><button data-action="save">Save Product</button></div>`;
    addSearchControls(card.querySelector('.unknown-header'), row.meijer_search_url);
    card.querySelector('[data-action="save"]').addEventListener('click', async () => {
      try {
        const payload = readProductFields(card);
        payload.lookup_source = row.suggestion?.lookup_source || 'manual';
        await api(`/api/unknown-products/${row.receipt_item_id}/resolve`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        await Promise.all([loadDashboard(), loadUnknown()]);
        setStatus('Product saved. All pending purchases of this UPC are now in inventory.');
      } catch (err) { setStatus(err.message, true); }
    });
    container.appendChild(card);
  }
}

async function lookupManualUpc(event) {
  event.preventDefault();
  try {
    const result = await api(`/api/products/lookup?upc=${encodeURIComponent($('#manualUpc').value)}`);
    const container = $('#manualProductResult');
    container.innerHTML = '';
    if (result.product) {
      container.innerHTML = `<span class="badge resolved">Known · Pantry Keeper</span><p>${escapeHtml(result.product.name)}</p>`;
      return;
    }
    container.innerHTML = `<span class="badge ${result.suggestion ? 'suggested' : 'unresolved'}">${result.suggestion ? 'Suggested · External Lookup' : 'Unknown · Needs Identification'}</span>
      <div class="form-grid">${productFieldsHtml(result.upc, result.suggestion || {})}</div>
      <div class="form-actions"><button type="button" data-save-product>Save Product</button></div>`;
    addSearchControls(container, result.meijer_search_url);
    container.querySelector('[data-save-product]').addEventListener('click', async () => {
      try {
        const payload = {...readProductFields(container), lookup_source: result.suggestion?.lookup_source || 'manual'};
        const product = await api('/api/products', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        container.innerHTML = `<span class="badge resolved">Known · Pantry Keeper</span><p>${escapeHtml(product.name)}</p>`;
        await Promise.all([loadDashboard(), loadUnknown()]);
        setStatus('Product saved to Pantry Keeper.');
      } catch (err) { setStatus(err.message, true); }
    });
  } catch (err) { setStatus(err.message, true); }
}

function renderRecipeResult(result) {
  const container = $("#recipeResult");
  const inStock = result.in_stock || [];
  const shopping = result.shopping_list || [];
  container.innerHTML = `
    <div class="recipe-columns">
      <article>
        <h3>In Stock (${inStock.length})</h3>
        <ul>${inStock.map((item) => `<li><strong>${escapeHtml(item.ingredient)}</strong><br><small>${escapeHtml(item.matched_product?.name || '')} · Qty ${escapeHtml(item.matched_product?.available_quantity ?? item.matched_product?.inventory_quantity ?? '')}</small></li>`).join('') || '<li>Nothing matched current inventory.</li>'}</ul>
      </article>
      <article>
        <h3>Shopping List (${shopping.length})</h3>
        <ul>${shopping.map((item) => `<li>${escapeHtml(item.ingredient)}</li>`).join('') || '<li>No additional ingredients needed.</li>'}</ul>
      </article>
    </div>`;
}

async function compareRecipeText(event) {
  event.preventDefault();
  try {
    const text = $("#recipeText").value.trim();
    const result = await api("/api/recipes/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    renderRecipeResult(result);
    setStatus("Recipe compared against current inventory.");
  } catch (err) { setStatus(err.message, true); }
}

async function uploadRecipeFile(file) {
  if (!file) return;
  try {
    const result = await apiFile("/api/recipes/upload", file);
    renderRecipeResult(result);
    setStatus("Recipe file compared against current inventory.");
  } catch (err) { setStatus(err.message, true); }
}

async function importInventoryCsv(file) {
  if (!file) return;
  try {
    const result = await apiFile("/api/inventory/import", file);
    await loadDashboard();
    setStatus(`Inventory CSV imported. ${result.imported} new row(s), ${result.updated} updated row(s).`);
  } catch (err) { setStatus(err.message, true); }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$('#manualUpcForm').addEventListener('submit', lookupManualUpc);
$('#recipeTextForm').addEventListener('submit', compareRecipeText);

$("#uploadInput").addEventListener("change", (e) => handleImage(e.target.files[0], "upload"));
$("#cameraInput").addEventListener("change", (e) => handleImage(e.target.files[0], "camera"));
$("#recipeFileInput").addEventListener("change", (e) => uploadRecipeFile(e.target.files[0]));
$("#inventoryCsvInput").addEventListener("change", (e) => importInventoryCsv(e.target.files[0]));
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
