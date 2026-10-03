(function (root) {
  function arrived(current, requested, title = '') {
    try {
      const actual = new URL(current);
      const target = new URL(requested);
      if (actual.protocol !== 'https:' || !(actual.hostname === 'meijer.com' || actual.hostname.endsWith('.meijer.com'))) return false;
      const query = target.searchParams.get('text');
      if (query) {
        const aliases = ['text', 'q', 'query', 'keyword', 'searchTerm', 'search'];
        const presented = aliases.map(key => actual.searchParams.get(key)).filter(Boolean);
        if (presented.length) return presented.some(value => value.toLowerCase() === query.toLowerCase());
        return /search|results/i.test(actual.pathname) && title.toLowerCase().includes(query.toLowerCase());
      }
      return actual.pathname === target.pathname;
    } catch { return false; }
  }
  async function waitForPage(api, tabId, requested) {
    let lastUrl = '';
    let injectionError = '';
    for (let poll = 0; poll < 80; poll++) {
      const tab = await api.tabs.get(tabId);
      lastUrl = tab.url || lastUrl;
      // Safari can keep a dynamic page's status at loading after results appear.
      // Probe the actual document instead of relying on tabs.status.
      if (arrived(tab.url, requested, tab.title)) {
        try {
          const output = await api.scripting.executeScript({target: {tabId}, func: () => ({
            url: location.href, title: document.title, ready: Boolean(document.body),
          })});
          const page = output[0]?.result;
          if (page?.ready && arrived(page.url, requested, page.title)) return;
        } catch (err) { injectionError = err.message; }
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (injectionError) throw new Error('Safari could not read Meijer. Allow this extension access to Meijer in Safari settings.');
    throw new Error(`Meijer search is not ready. Open its search tab and finish any store or sign-in prompts.${lastUrl ? ` Current page: ${lastUrl}` : ''}`);
  }
  root.lookupMeijerProducts = async function (api, items, progress) {
    const results = {};
    const errors = [];
    let searchTab = null;
    const unique = [...new Map(items.filter(item => item.raw_code).map(item => [item.raw_code, item])).values()];
    try {
      for (let index = 0; index < unique.length; index++) {
        const item = unique[index];
        progress(`Looking up Meijer products ${index + 1} of ${unique.length}: ${item.receipt_description || item.raw_code}`);
        const queries = [item.raw_code];
        if (item.receipt_description) queries.push(item.receipt_description);
        let candidates = [];
        const collected = new Map();
        for (const query of queries) {
          const url = `https://www.meijer.com/shopping/search.html?text=${encodeURIComponent(query)}`;
          try {
            if (searchTab) await api.tabs.update(searchTab.id, {url, active: false});
            else searchTab = await api.tabs.create({url, active: false});
            await waitForPage(api, searchTab.id, url);
            await api.scripting.executeScript({target: {tabId: searchTab.id}, files: ['product-capture.js']});
            const output = await api.scripting.executeScript({target: {tabId: searchTab.id}, func: async () => await globalThis.PantryProductCapture()});
            const captured = output[0]?.result || {};
            if (captured.error) {errors.push(captured.error); break;}
            candidates = (captured.candidates || []).map(product => ({...product,
              match_kind: product.receipt_code === item.raw_code ? 'exact_code' : 'search_result',
            }));
            candidates.forEach(candidate => collected.set(candidate.url, candidate));
            if (candidates.some(candidate => candidate.match_kind === 'exact_code')) break;
          } catch (err) {errors.push(err.message); break;}
        }
        candidates = [...collected.values()];
        const terms = (item.receipt_description || '').toLowerCase().replace(/frzn/g, 'frozen').split(/[^a-z]+/).filter(term => term.length > 2);
        const score = product => (product.match_kind === 'exact_code' ? 100 : 0) + terms.filter(term => product.name.toLowerCase().includes(term)).length;
        candidates.sort((a, b) => score(b) - score(a));
        // Product detail pages often provide brand/size omitted from search cards.
        if (candidates[0]?.match_kind === 'exact_code' && !errors.length) {
          try {
            const selected = candidates[0];
            await api.tabs.update(searchTab.id, {url: selected.url, active: false});
            await waitForPage(api, searchTab.id, selected.url);
            {
              await api.scripting.executeScript({target: {tabId: searchTab.id}, files: ['product-capture.js']});
              const output = await api.scripting.executeScript({target: {tabId: searchTab.id}, func: async () => await globalThis.PantryProductCapture()});
              const detail = output[0]?.result?.candidates?.find(product => product.url === selected.url);
              if (detail) candidates[0] = {...selected,
                name: detail.name || selected.name, brand: detail.brand || selected.brand,
                size: detail.size || selected.size, gtin: detail.gtin || selected.gtin};
            }
          } catch { /* Preserve the valid search match if details are unavailable. */ }
        }
        results[item.raw_code] = candidates.slice(0, 5);
        // Stop on access/sign-in/navigation trouble instead of opening repeated failing searches.
        if (errors.length) break;
      }
    } finally {
      // Close only the temporary search tab; never the user's original Meijer receipt.
      if (searchTab && !errors.length) await api.tabs.remove(searchTab.id).catch(() => {});
    }
    return {products: results, errors: [...new Set(errors)]};
  };
})(globalThis);
