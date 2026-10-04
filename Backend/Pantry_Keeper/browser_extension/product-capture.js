(() => {
  const clean = value => typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
  const sizeOf = name => (name.match(/(?:\d+(?:\.\d+)?\s*(?:fl\s*oz|oz|ounces?|lb|lbs|pounds?|g|kg|ml|l|ct|count|pk|pack))(?:\s*[,x]\s*\d+\s*(?:oz|ct))?/i) || [])[0] || null;
  function productUrl(value) {
    try {
      const url = new URL(value, location.href);
      return url.protocol === 'https:' && (url.hostname === 'meijer.com' || url.hostname.endsWith('.meijer.com')) && url.pathname.includes('/shopping/product/') ? url.href : null;
    } catch { return null; }
  }
  function collect() {
    const products = new Map();
    function add(value) {
      const url = productUrl(value.url);
      const name = clean(value.name);
      if (!url || !name) return;
      const previous = products.get(url) || {};
      products.set(url, { ...previous, name, url,
        brand: clean(value.brand) || previous.brand || null,
        size: clean(value.size) || previous.size || sizeOf(name),
        gtin: clean(value.gtin) || previous.gtin || null,
        receipt_code: (new URL(url).pathname.match(/\/(\d{8,14})(?:\.html)?\/?$/) || [])[1] || null,
      });
    }
    function visit(value) {
      if (!value || typeof value !== 'object') return;
      if (Array.isArray(value)) { value.forEach(visit); return; }
      const types = [].concat(value['@type'] || []);
      if (types.includes('Product')) add({name: value.name, url: value.url || value['@id'] || location.href,
        brand: typeof value.brand === 'string' ? value.brand : value.brand?.name,
        size: value.size || value.weight?.value, gtin: value.gtin12 || value.gtin13 || value.gtin14 || value.gtin });
      Object.values(value).forEach(visit);
    }
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try { visit(JSON.parse(script.textContent)); } catch { /* Other page scripts may not be valid JSON. */ }
    }
    for (const link of document.querySelectorAll('a[href*="/shopping/product/"]')) {
      const card = link.closest('[data-testid*="product"], [data-qa*="product"], .product-card, .product-tile, article, li') || link;
      const heading = card.querySelector('h2, h3, h4, [data-testid*="name"], [class*="product-name"], [class*="product-title"]');
      const name = heading?.textContent || link.getAttribute('aria-label') || link.querySelector('img')?.alt || clean(link.textContent);
      const brand = card.querySelector('[itemprop="brand"], [data-testid*="brand"], [class*="product-brand"]')?.textContent;
      add({name, url: link.href, brand});
    }
    if (productUrl(location.href)) {
      add({url: location.href,
        name: document.querySelector('h1, [itemprop="name"]')?.textContent,
        brand: document.querySelector('[itemprop="brand"], [data-testid*="brand"], [class*="product-brand"]')?.textContent,
        size: document.querySelector('[itemprop="size"]')?.textContent});
    }
    return [...products.values()].slice(0, 50);
  }
  globalThis.PantryProductCapture = async function () {
    const started = Date.now();
    while (Date.now() - started < 12000) {
      const candidates = collect();
      if (candidates.length) return { candidates, page_url: location.href };
      const text = document.body?.innerText || '';
      if (/access denied|verify you are human|captcha/i.test(text)) return {candidates: [], error: 'Meijer needs your attention in its search tab.'};
      if (/no results found|no products found|0 results/i.test(text)) return {candidates: []};
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    return {candidates: [], error: /select (?:a |your )?store|sign in to continue/i.test(document.body?.innerText || '') ? 'Choose your store or sign in on Meijer, then scan again.' : null};
  };
})();
