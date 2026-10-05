(() => {
  if (globalThis.__pantryCaptureInstalled) return;
  globalThis.__pantryCaptureInstalled = true;
  const api = globalThis.browser || globalThis.chrome;
  api.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== 'PANTRY_KEEPER_CAPTURE') return;
    // Prefer the open receipt dialog over account history and other receipts.
    const dialogs = Array.from(document.querySelectorAll?.('[role="dialog"], dialog[open], .modal.show, .modal.in, .modal[aria-hidden="false"], .modal-dialog') || []);
    const scope = dialogs.find(el => el.getBoundingClientRect().width > 0 && /receipt/i.test(el.innerText || '')) || document.body;
    let text = scope?.innerText?.trim() || '';
    // Logos may be images rather than selectable text. The trusted store page
    // supplies a header marker; uploaded images still use OCR store detection.
    if (/(^|\.)costco\.com$/i.test(location.hostname || '') && !/\bcost\s*co\b/i.test(text)) text = 'COSTCO WHOLESALE\n' + text;
    const images = Array.from(scope?.querySelectorAll?.('img') || document.images || []).filter(image => {
      const width = image.naturalWidth || image.width;
      const height = image.naturalHeight || image.height;
      return width >= 150 && height >= 200 && image.getBoundingClientRect().width > 0;
    }).sort((a, b) => {
      const score = image => (image.naturalWidth * image.naturalHeight) *
        (/DigitalReceipt/i.test(image.currentSrc || image.src || '') ? 10 : 1);
      return score(b) - score(a);
    });
    const image = images[0];
    let imageData = null;
    // Export same-origin images (including blob images) without another download.
    if (image) {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        canvas.getContext('2d').drawImage(image, 0, 0);
        imageData = canvas.toDataURL('image/png');
      } catch { /* Cross-origin image: the extension downloads the original instead. */ }
    }
    sendResponse({ ok: Boolean(text || image), text, image_url: image?.currentSrc || image?.src || null,
      image_data: imageData, url: location.href, title: document.title });
  });
})();
