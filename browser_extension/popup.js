const extension = globalThis.browser || globalThis.chrome;
const serverInput = document.getElementById('serverUrl');
const tokenInput = document.getElementById('apiToken');
const statusEl = document.getElementById('status');
const scanBtn = document.getElementById('scanBtn');
const saveBtn = document.getElementById('saveBtn');
const openBtn = document.getElementById('openBtn');
const reviewBtn = document.getElementById('reviewBtn');
let reviewTabId = null;
function status(message, error = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle('error', error);
}
function busy(value) {
  for (const el of [scanBtn, saveBtn, openBtn, serverInput, tokenInput]) el.disabled = value;
}
async function configure(address, token, capture = false) {
  // Called directly from a button handler so Safari can show its permission prompt.
  const granted = await extension.permissions.request({ origins: [address.permission, ...(capture ? ['https://*.meijer.com/*', 'https://*.costco.com/*'] : [])] });
  if (!granted) throw new Error('Allow access to your Pantry Keeper server to continue.');
  await extension.storage.local.set({ serverUrl: address.origin, apiToken: token });
  serverInput.value = address.origin;
}
async function serverRequest(origin, path, token, payload) {
  const multipart = payload instanceof FormData;
  let response;
  try {
    response = await fetch(`${origin}${path}`, {
      method: 'POST', signal: AbortSignal.timeout(60000),
      headers: { ...(!multipart ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: multipart ? payload : JSON.stringify(payload),
    });
  } catch {
    throw new Error('Could not reach Pantry Keeper. Check the server address, website permission, and network connection.');
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) throw new Error('The API token does not match your Pantry Keeper server.');
  if (!response.ok) {
    const error = new Error(data.detail || `Server returned ${response.status}.`);
    error.status = response.status;
    throw error;
  }
  return data;
}
const ready = extension.storage.local.get(['serverUrl', 'apiToken']).then(data => {
  serverInput.value = data.serverUrl || '';
  tokenInput.value = data.apiToken || '';
}).catch(err => status(`Could not load settings: ${err.message}`, true));
busy(true);
ready.finally(() => busy(false));

document.getElementById('settingsForm').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const address = PantryExtension.serverAddress(serverInput.value);
    const permission = configure(address, tokenInput.value.trim());
    busy(true);
    await permission;
    status('Settings saved.');
  } catch (err) { status(err.message, true); }
  finally { busy(false); }
});

scanBtn.addEventListener('click', async () => {
  reviewBtn.hidden = true;
  try {
    const address = PantryExtension.serverAddress(serverInput.value);
    const token = tokenInput.value.trim();
    const permission = configure(address, token, true);
    busy(true);
    status('Reading receipt…');
    await permission;
    const [tab] = await extension.tabs.query({ active: true, currentWindow: true });
    if (tab?.id == null || !PantryExtension.isReceiptPage(tab.url)) {
      throw new Error('Open a receipt on meijer.com or costco.com before scanning.');
    }
    let data;
    if (PantryExtension.isReceiptPDF(tab.url)) {
      // Safari's built-in PDF viewer has no usable page text or document images.
      status('Downloading the receipt PDF…');
      let response;
      try {
        response = await fetch(tab.url, { signal: AbortSignal.timeout(20000) });
      } catch {
        throw new Error('Could not download the PDF. Allow receipt website access and try again.');
      }
      if (!response.ok) throw new Error(`Could not download the receipt PDF (${response.status}).`);
      const blob = await response.blob();
      if (blob.size > 20 * 1024 * 1024) throw new Error('Receipt PDF is too large (maximum 20 MB).');
      const form = new FormData();
      form.append('file', blob, 'receipt.pdf');
      status('Reading the receipt PDF in Pantry Keeper…');
      data = await serverRequest(address.origin, '/api/receipts/browser-pdf', token, form);
    } else {
      try {
        await extension.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
      } catch {
        throw new Error('Allow this extension access to the receipt website in Safari, then try again.');
      }
      const result = await extension.tabs.sendMessage(tab.id, { type: 'PANTRY_KEEPER_CAPTURE' });
      if (!result?.ok) throw new Error('No receipt content found. Wait for the receipt to finish loading and try again.');
      const hasItemText = /(?:E\s+)?\d{3,14}\s+.+?\s+\d+\.\d{2}\s*[A-Z]?\s*$/m.test(result.text || '');
      if (hasItemText || (!result.image_data && !result.image_url)) {
        status('Sending receipt text to Pantry Keeper…');
        try {
          data = await serverRequest(address.origin, '/api/receipts/browser', token, {
            text: result.text, page_url: result.url, page_title: result.title,
          });
        } catch (err) {
          if (err.status !== 422 || (!result.image_data && !result.image_url)) throw err;
        }
      }
      if (!data) {
        status('Scanning the receipt image…');
        const imageUrl = result.image_data || result.image_url;
        if (!imageUrl?.startsWith('data:image/') && !PantryExtension.isReceiptPage(imageUrl)) {
          throw new Error('Could not access this receipt image. Upload a screenshot in Pantry Keeper.');
        }
        let imageResponse;
        try {
          imageResponse = await fetch(imageUrl, { signal: AbortSignal.timeout(20000) });
        } catch {
          throw new Error('Could not download the receipt image. Allow receipt website access or upload a screenshot.');
        }
        if (!imageResponse.ok) throw new Error('Could not download the receipt image. Try uploading a screenshot.');
        const blob = await imageResponse.blob();
        if (blob.size > 20 * 1024 * 1024) throw new Error('Receipt image is too large. Upload a smaller screenshot.');
        const form = new FormData();
        form.append('file', blob, 'receipt.png');
        data = await serverRequest(address.origin, '/api/receipts/browser-image', token, form);
      }
    }
    if (!data.parsed?.items?.length) throw new Error('No receipt items recognized. Open an individual receipt; you can also upload a screenshot in Pantry Keeper.');
    if (!data.draft_id) throw new Error('Update your Pantry Keeper server to support extension receipt drafts.');

    // Never fall back to every parsed line: the server must classify UPCs first.
    if (!Array.isArray(data.lookup_items)) throw new Error('Update Pantry Keeper so it can check the database before Meijer lookup.');
    const lookup = data.lookup_items.length
      ? await lookupMeijerProducts(extension, data.lookup_items, message => status(message))
      : {products: {}, errors: []};
    if (data.lookup_items.length) {
      await serverRequest(address.origin, `/api/receipts/browser-drafts/${encodeURIComponent(data.draft_id)}/meijer-products`, token, lookup);
    }

    const reviewTab = await extension.tabs.create({
      url: `${address.origin}/#${new URLSearchParams({ draft: data.draft_id })}`, active: false,
    });
    reviewTabId = reviewTab.id;
    reviewBtn.hidden = false;
    const expected = data.parsed.expected_item_count;
    status(`Captured ${data.parsed.items.length} receipt line(s)${expected != null ? `; receipt lists ${expected} item(s)` : ''}. Your receipt stays open. Click Review scanned receipt to continue.${lookup.errors.length ? ` Product lookup needs attention: ${lookup.errors[0]}` : ''}`);
  } catch (err) { status(err.message, true); }
  finally { busy(false); }
});
openBtn.addEventListener('click', async () => {
  try {
    const address = PantryExtension.serverAddress(serverInput.value);
    await extension.tabs.create({ url: address.origin });
  } catch (err) { status(err.message, true); }
});

reviewBtn.addEventListener('click', async () => {
  try {
    if (reviewTabId != null) await extension.tabs.update(reviewTabId, { active: true });
  } catch (err) { status(`Could not open review: ${err.message}`, true); }
});
