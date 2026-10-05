const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const dir = path.join(__dirname, '..', 'browser_extension');
const core = vm.createContext({ URL });
vm.runInContext(fs.readFileSync(path.join(dir, 'core.js'), 'utf8'), core);

test('server validation and Meijer domain restrictions', () => {
  assert.equal(core.PantryExtension.serverAddress(' http://192.168.1.20:8000/ ').origin, 'http://192.168.1.20:8000');
  assert.equal(core.PantryExtension.serverAddress('192.168.1.20:8000').origin, 'http://192.168.1.20:8000');
  assert.equal(core.PantryExtension.serverAddress('pantry.local:8000').origin, 'http://pantry.local:8000');
  assert.equal(core.PantryExtension.serverAddress('https://pantry.example.com').permission, 'https://pantry.example.com/*');
  for (const url of ['http://0.0.0.0:8000', 'file:///tmp/x', 'https://user:pass@server', 'https://server/path', 'https://server?token=x']) {
    assert.throws(() => core.PantryExtension.serverAddress(url));
  }
  assert.equal(core.PantryExtension.isMeijerPage('https://www.meijer.com/mperks/receipts.html'), true);
  for (const url of ['https://meijer.com.attacker.test', 'http://meijer.com', 'https://example.com']) {
    assert.equal(core.PantryExtension.isMeijerPage(url), false);
  }
});

function popup(options = {}) {
  const elements = {};
  for (const id of ['serverUrl', 'apiToken', 'status', 'scanBtn', 'saveBtn', 'openBtn', 'reviewBtn', 'settingsForm']) {
    elements[id] = { value: '', disabled: false, textContent: '', listeners: {},
      classList: { toggle() {} }, addEventListener(name, fn) { this.listeners[name] = fn; } };
  }
  const calls = [];
  const extension = {
    storage: { local: {
      get: async () => ({serverUrl: 'http://192.168.1.20:8000', apiToken: 'secret-token'}),
      set: async value => calls.push(['settings', value]),
    } },
    permissions: { request: async value => { calls.push(['permission', value]); return options.permission !== false; } },
    tabs: {
      query: async () => [{ id: 1, url: options.url || 'https://www.meijer.com/receipt' }],
      sendMessage: async () => ({ ok: true, text: options.imageOnly ? '' : (options.receiptText || '012345678901 MILK 3.99'), image_url: options.imageOnly ? 'https://static.meijer.com/DigitalReceipt/receipt.png' : null, url: 'https://www.meijer.com/receipt', title: 'Receipt' }),
      create: async value => {calls.push(['tab', value]); return {id: 55};},
      update: async (id, value) => calls.push(['activate', id, value]),
    },
    scripting: { executeScript: async value => {
      if (options.injectionError) throw new Error('denied');
      calls.push(['script', value]);
    } },
  };
  const context = vm.createContext({ browser: extension, URL, URLSearchParams, AbortSignal, FormData,
    document: { getElementById: id => elements[id] },
    fetch: async (url, request) => {
      calls.push(['fetch', url, request]);
      if (options.networkError) throw new Error('offline');
      if (url.startsWith('https://static.meijer.com/')) return {ok: true, blob: async () => new Blob(['image'], {type: 'image/png'})};
      return { ok: !options.responseStatus, status: options.responseStatus || 200,
        json: async () => ({ draft_id: 'random-draft', lookup_items: options.missingLookup ? undefined : (options.lookupItems ?? []), parsed: { items: [{raw_code:'11111111111'}, {raw_code:'22222222222'}] } }) };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(dir, 'core.js'), 'utf8'), context);
  context.lookupMeijerProducts = async (api, items) => {
    calls.push(['meijer', JSON.parse(JSON.stringify(items))]);
    return {products: {}, errors: []};
  };
  vm.runInContext(fs.readFileSync(path.join(dir, 'popup.js'), 'utf8'), context);
  return { elements, calls };
}
async function scan(options) {
  const state = popup(options);
  await new Promise(resolve => setImmediate(resolve));
  await state.elements.scanBtn.listeners.click();
  return state;
}
test('scan sends authorized receipt and opens draft without receipt text or token in URL', async () => {
  const { calls, elements } = await scan();
  assert.equal(calls[0][0], 'permission');
  const request = calls.find(call => call[0] === 'fetch');
  assert.equal(request[2].headers.Authorization, 'Bearer secret-token');
  assert.equal(JSON.parse(request[2].body).text, '012345678901 MILK 3.99');
  assert.equal(calls.find(call => call[0] === 'tab')[1].url, 'http://192.168.1.20:8000/#draft=random-draft');
  assert.match(elements.status.textContent, /Captured 2/);
  assert.equal(calls.find(call => call[0] === 'tab')[1].active, false);
  assert.equal(elements.reviewBtn.hidden, false);
  await elements.reviewBtn.listeners.click();
  assert.equal(calls.find(call => call[0] === 'activate')[1], 55);
  assert.equal(elements.scanBtn.disabled, false);
  assert.equal(calls.filter(call => call[0] === 'fetch').length, 1);
  assert.equal(calls.filter(call => call[0] === 'tab').length, 1);
  assert.equal(calls.some(call => call[0] === 'fetch' && call[1].includes('meijer-products')), false);
});
for (const [name, options, message] of [
  ['permission denied', { permission: false }, /Allow access/],
  ['wrong website', { url: 'https://example.com' }, /Open a receipt on meijer/],
  ['Meijer permission missing', { injectionError: true }, /Allow this extension access to the receipt website/],
  ['bad token', { responseStatus: 401 }, /API token/],
  ['offline server', { networkError: true }, /Could not reach/],
]) {
  test(name, async () => {
    const { calls, elements } = await scan(options);
    assert.match(elements.status.textContent, message);
    assert.equal(calls.some(call => call[0] === 'tab'), false);
    assert.equal(elements.scanBtn.disabled, false);
  });
}
test('repeated script injection installs only one capture listener', () => {
  const listeners = [];
  const context = vm.createContext({ browser: {runtime: {onMessage: {addListener: fn => listeners.push(fn)}}},
    document: {body: {innerText: '  receipt text  '}, title: 'Receipt'}, location: {href: 'https://www.meijer.com/receipt'} });
  const source = fs.readFileSync(path.join(dir, 'content.js'), 'utf8');
  vm.runInContext(source, context);
  vm.runInContext(source, context);
  assert.equal(listeners.length, 1);
  let result;
  listeners[0]({type: 'PANTRY_KEEPER_CAPTURE'}, {}, value => { result = value; });
  assert.equal(result.text, 'receipt text');
  assert.equal(result.ok, true);
});
test('manifest resources exist', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  assert.equal(manifest.manifest_version, 3);
  for (const file of [...Object.values(manifest.icons), ...Object.values(manifest.action.default_icon), manifest.action.default_popup]) {
    assert.equal(fs.existsSync(path.join(dir, file)), true, file);
  }
});

async function appHandoff(expired = false) {
  const nodes = new Map();
  const calls = [];
  const node = () => ({ textContent: '', innerHTML: '', classList: { remove() {}, add() {}, toggle() {} },
    addEventListener() {}, appendChild() {} });
  const context = vm.createContext({ URLSearchParams, location: {hash: '#draft=random-draft', pathname: '/', search: ''},
    history: {replaceState: (...args) => calls.push(['history', ...args])},
    document: {
      querySelector: selector => { if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector); },
      createElement: node,
    },
    fetch: async (url, options) => {
      calls.push(['fetch', url]);
      let data = {};
      let ok = true;
      if (url === '/api/dashboard') data = {products: [], receipt_count: 0, unknown_count: 0};
      else if (url === '/api/unknown-products') data = [];
      else if (url.includes('/browser-drafts/')) {
        ok = !expired;
        data = expired ? {detail: 'Receipt draft expired. Scan it again in Safari.'} : {
          source_type: 'safari_extension', source_format: 'webpage_text',
          parsed: {items: [{raw_code: '012345678901'}]}, raw_text: 'receipt',
        };
      } else if (url === '/api/receipts/resolve-preview') data = {items: [{raw_code: '012345678901', status: 'resolved', product: {name:'Milk'}}]};
      return {ok, status: ok ? 200 : 410, json: async () => data};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(dir, '..', 'static', 'scripts', 'app.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, calls, context};
}
test('app loads the browser draft into review and clears the fragment', async () => {
  const {nodes, calls, context} = await appHandoff();
  assert.equal(calls.some(call => call[0] === 'fetch' && call[1] === '/api/receipts/browser-drafts/random-draft'), true);
  assert.equal(calls.find(call => call[0] === 'history')[3], '/');
  assert.equal(vm.runInContext('currentScan.source_format', context), 'webpage_text');
  assert.match(nodes.get('#scanStatus').textContent, /Browser receipt received/);
});
test('app explains expired draft failure', async () => {
  const {nodes} = await appHandoff(true);
  assert.match(nodes.get('#scanStatus').textContent, /expired/);
});


test('image receipt downloads the original and uploads it for OCR with the API token', async () => {
  const {calls, elements} = await scan({imageOnly: true});
  const imageFetch = calls.find(call => call[0] === 'fetch' && call[1].startsWith('https://static.meijer.com/'));
  assert.ok(imageFetch);
  const upload = calls.find(call => call[0] === 'fetch' && call[1].endsWith('/browser-image'));
  assert.ok(upload);
  assert.ok(upload[2].body instanceof FormData);
  assert.equal(upload[2].headers.Authorization, 'Bearer secret-token');
  assert.equal(upload[2].headers['Content-Type'], undefined);
  assert.equal(calls.some(call => call[0] === 'tab'), true);
  assert.match(elements.status.textContent, /Captured 2/);
});

test('capture finds image-only receipts when the page has no text', () => {
  let listener;
  const image = {naturalWidth: 700, naturalHeight: 1800, currentSrc: 'https://static.meijer.com/DigitalReceipt/a.png', getBoundingClientRect: () => ({width: 400})};
  const context = vm.createContext({browser: {runtime: {onMessage: {addListener: fn => {listener = fn;}}}},
    document: {body: {innerText: ''}, images: [image], title: 'Receipt', createElement: () => {throw new Error('tainted canvas');}},
    location: {href: image.currentSrc}});
  vm.runInContext(fs.readFileSync(path.join(dir, 'content.js'), 'utf8'), context);
  let result;
  listener({type: 'PANTRY_KEEPER_CAPTURE'}, {}, value => {result = value;});
  assert.equal(result.ok, true);
  assert.equal(result.text, '');
  assert.equal(result.image_url, image.currentSrc);
});


test('PDF receipt bypasses Safari content injection and uploads the original PDF', async () => {
  const {calls, elements} = await scan({url: 'https://static.meijer.com/DigitalReceipt/a/meijer_digital_receipt.pdf'});
  assert.equal(calls.some(call => call[0] === 'script'), false);
  const upload = calls.find(call => call[0] === 'fetch' && call[1].endsWith('/browser-pdf'));
  assert.ok(upload);
  assert.ok(upload[2].body instanceof FormData);
  assert.equal(upload[2].body.get('file').name, 'receipt.pdf');
  assert.equal(upload[2].headers.Authorization, 'Bearer secret-token');
  assert.match(elements.status.textContent, /Captured 2/);
});

test('PDF detection accepts query strings and rejects non-Meijer files', () => {
  assert.equal(core.PantryExtension.isReceiptPDF('https://static.meijer.com/receipt.pdf?download=1'), true);
  assert.equal(core.PantryExtension.isReceiptPDF('https://example.com/receipt.pdf'), false);
  assert.equal(core.PantryExtension.isReceiptPDF('https://www.meijer.com/receipts.html'), false);
});


test('review selects suggestions and imports edited product fields with the receipt', async () => {
  const {JSDOM} = require('jsdom');
  const html = fs.readFileSync(path.join(dir, '..', 'templates', 'index.html'), 'utf8');
  const dom = new JSDOM(html, {url:'http://pantry.test', runScripts:'outside-only'});
  const calls = [];
  const suggestion = {name:'Meijer Steamable Mixed Vegetables', brand:'Meijer', size:'12 oz', match_kind:'search_result', lookup_source:'meijer', url:'https://www.meijer.com/shopping/product/mixed/71928395643.html'};
  dom.window.fetch = async (url, options) => {
    const payload = options?.body ? JSON.parse(options.body) : null;
    calls.push({url, payload});
    let data;
    if (url === '/api/dashboard') data = {products:[],receipt_count:0,unknown_count:0};
    else if (url === '/api/unknown-products') data = [];
    else if (url === '/api/receipts/resolve-preview') data = {items:[{raw_code:'71928395643',receipt_description:'FRZN VEGETABLE',quantity:1,status:'suggested',suggestion,candidates:[suggestion]}]};
    else if (url === '/api/receipts/import') data = {receipt_id:1, imported_items:1,resolved_items:1,unresolved_items:0};
    else data = {id:1};
    return {ok:true,json:async () => data};
  };
  dom.window.eval(fs.readFileSync(path.join(dir, '..', 'static', 'scripts', 'app.js'), 'utf8'));
  await dom.window.showReview({parsed:{items:[{raw_code:'71928395643'}]},meijer_products:{'71928395643':[suggestion]}});
  const select = dom.window.document.querySelector('[data-product-match]');
  assert.equal(select.value, '0');
  assert.equal(dom.window.document.querySelector('[data-match-brand]').value, 'Meijer');
  assert.equal(dom.window.document.querySelector('[data-match-size]').value, '12 oz');
  dom.window.document.querySelector('[data-match-name]').value = 'Mixed Vegetables';
  await dom.window.importCurrentReceipt();
  const imported = calls.find(call => call.url === '/api/receipts/import');
  const saved = {payload: imported.payload.selected_products['71928395643']};
  assert.equal(saved.payload.name, 'Mixed Vegetables');
  assert.equal(saved.payload.brand, 'Meijer');
  assert.equal(saved.payload.size, '12 oz');
  assert.equal(saved.payload.lookup_source, 'meijer');
  assert.equal(calls.filter(call => call.url === '/api/products').length, 0);
  await dom.window.showReview({parsed:{items:[{raw_code:'71928395643'}]}});
  const unselected = dom.window.document.querySelector('[data-product-match]');
  unselected.value = '';
  unselected.dispatchEvent(new dom.window.Event('change'));
  await dom.window.importCurrentReceipt();
  assert.deepEqual(calls.filter(call => call.url === '/api/receipts/import').at(-1).payload.selected_products, {});
  dom.window.close();
});


function webApp(handler) {
  const {JSDOM} = require('jsdom');
  const dom = new JSDOM(fs.readFileSync(path.join(dir, '..', 'templates', 'index.html'), 'utf8'),
    {url:'http://pantry.test', runScripts:'outside-only'});
  const calls = [];
  const opened = [];
  dom.window.open = (...args) => opened.push(args);
  dom.window.fetch = async (url, options) => {
    const payload = options?.body ? JSON.parse(options.body) : null;
    calls.push({url, payload});
    const defaults = url === '/api/dashboard' ? {products:[],receipt_count:0,unknown_count:0} : [];
    return {ok:true, json:async () => handler(url, payload) ?? defaults};
  };
  dom.window.eval(fs.readFileSync(path.join(dir, '..', 'static', 'scripts', 'app.js'), 'utf8'));
  return {dom, calls, opened};
}

test('unknown review has manual search/copy controls and saves unit and notes', async () => {
  const url = 'https://www.meijer.com/shopping/search.html?text=0071928395643';
  const {dom, calls, opened} = webApp((endpoint, payload) => {
    if (endpoint === '/api/receipts/resolve-preview') return {items:[{
      upc:'0071928395643',raw_code:'0071928395643',status:'unresolved',meijer_search_url:url}]};
    if (endpoint === '/api/products') return {id:1,name:payload.name,upc:payload.upc};
    if (endpoint === '/api/receipts/import') return {receipt_id:1,imported_items:1,resolved_items:1,unresolved_items:0};
  });
  await dom.window.showReview({parsed:{items:[{raw_code:'0071928395643'}]}});
  assert.deepEqual(opened, []);
  const doc = dom.window.document;
  assert.equal(doc.querySelector('[data-field="upc"]').readOnly, true);
  assert.match(doc.querySelector('#reviewTableBody').textContent, /Needs Identification/);
  const search = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Search Meijer');
  search.click();
  assert.deepEqual(opened, [[url, '_blank', 'noopener,noreferrer']]);
  let copied;
  Object.defineProperty(dom.window, 'isSecureContext', {value:true});
  Object.defineProperty(dom.window.navigator, 'clipboard', {value:{writeText:async value => {copied = value;}}});
  const copy = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Copy Search URL');
  copy.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(copied, url);
  doc.querySelector('[data-field="name"]').value = 'Vegetables';
  doc.querySelector('[data-field="unit"]').value = 'bag';
  doc.querySelector('[data-field="notes"]').value = 'Keep frozen';
  doc.querySelector('[data-save-product]').click();
  await new Promise(resolve => setImmediate(resolve));
  const saved = calls.find(call => call.url === '/api/products').payload;
  assert.equal(saved.upc, '0071928395643');
  assert.equal(saved.unit, 'bag');
  assert.equal(saved.notes, 'Keep frozen');
  assert.match(doc.querySelector('#reviewTableBody').textContent, /Known · Pantry Keeper/);
  assert.equal(doc.querySelector('#reviewTableBody .search-actions'), null);
  await dom.window.importCurrentReceipt();
  assert.ok(calls.find(call => call.url === '/api/receipts/import'));
  dom.window.close();
});

test('known product review offers no external search and manual UPC entry reuses local info', async () => {
  const product = {id:1,upc:'0071928395643',name:'Local Vegetables'};
  const {dom, calls, opened} = webApp(endpoint => {
    if (endpoint === '/api/receipts/resolve-preview') return {items:[{raw_code:product.upc,status:'resolved',source:'Pantry Keeper',product}]};
    if (endpoint.startsWith('/api/products/lookup?')) return {upc:product.upc,status:'resolved',source:'Pantry Keeper',product};
  });
  await dom.window.showReview({parsed:{items:[{raw_code:product.upc}]}});
  assert.equal(dom.window.document.querySelector('#reviewTableBody .search-actions'), null);
  dom.window.document.querySelector('#manualUpc').value = '00-71928395643';
  await dom.window.lookupManualUpc({preventDefault() {}});
  assert.match(dom.window.document.querySelector('#manualProductResult').textContent, /Local Vegetables/);
  assert.equal(dom.window.document.querySelector('#manualProductResult [data-save-product]'), null);
  assert.equal(calls.filter(call => call.url === '/api/products').length, 0);
  assert.deepEqual(opened, []);
  dom.window.close();
});

test('unknown review entered manually is included atomically on receipt import', async () => {
  const {dom, calls} = webApp(endpoint => {
    if (endpoint === '/api/receipts/resolve-preview') return {items:[{upc:'71928395643',raw_code:'71928395643',status:'unresolved'}]};
    if (endpoint === '/api/receipts/import') return {receipt_id:1,imported_items:1,resolved_items:1,unresolved_items:0};
  });
  await dom.window.showReview({parsed:{items:[{raw_code:'71928395643'}]}});
  dom.window.document.querySelector('[data-field="name"]').value = 'Manual Vegetables';
  dom.window.document.querySelector('[data-field="category"]').value = 'Frozen';
  dom.window.document.querySelector('[data-field="unit"]').value = 'bag';
  dom.window.document.querySelector('[data-field="notes"]').value = 'Dinner';
  await dom.window.importCurrentReceipt();
  const payload = calls.find(call => call.url === '/api/receipts/import').payload.selected_products['71928395643'];
  assert.equal(payload.name, 'Manual Vegetables');
  assert.equal(payload.unit, 'bag');
  assert.equal(payload.notes, 'Dinner');
  assert.equal(calls.filter(call => call.url === '/api/products').length, 0);
  dom.window.close();
});

test('Meijer lookup searches a receipt code, collects exact matches, and closes only its search tab', async () => {
  const calls = [];
  let currentUrl;
  const context = vm.createContext({URL, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(dir, 'product-lookup.js'), 'utf8'), context);
  const api = {
    tabs: {
      create: async value => {calls.push(['create', value]); currentUrl = value.url; return {id: 77};},
      update: async (id, value) => {currentUrl = value.url; return {id};},
      get: async () => ({status: 'complete', url: currentUrl}),
      remove: async id => calls.push(['remove', id]),
    },
    scripting: {executeScript: async value => value.func ? [{result: value.func.toString().includes('ready:') ? {ready:true,url:currentUrl,title:''} : {candidates: [{
      name: 'Meijer Steamable Mixed Vegetables, 12 oz', brand: 'Meijer', size: '12 oz',
      url: 'https://www.meijer.com/shopping/product/mixed-vegetables/71928395643.html', receipt_code: '71928395643',
    }]}}] : []},
  };
  const result = await context.lookupMeijerProducts(api, [{raw_code: '71928395643', receipt_description: 'FRZN VEGETABLE'}], () => {});
  assert.match(calls[0][1].url, /text=71928395643/);
  assert.equal(result.products['71928395643'][0].match_kind, 'exact_code');
  assert.equal(result.products['71928395643'][0].size, '12 oz');
  assert.deepEqual(calls.filter(call => call[0] === 'remove'), [['remove', 77]]);
});

test('Meijer access trouble keeps the search tab open and reports an actionable error', async () => {
  let currentUrl;
  let removed = false;
  const context = vm.createContext({URL, setTimeout});
  vm.runInContext(fs.readFileSync(path.join(dir, 'product-lookup.js'), 'utf8'), context);
  const api = {
    tabs: {create: async value => {currentUrl = value.url; return {id: 77};}, get: async () => ({status:'complete',url:currentUrl}), remove:async () => {removed = true;}},
    scripting: {executeScript:async value => value.func ? [{result: value.func.toString().includes('ready:') ? {ready:true,url:currentUrl,title:''} : {candidates:[],error:'Choose your store.'}}] : []},
  };
  const result = await context.lookupMeijerProducts(api, [{raw_code:'12345678901'}], () => {});
  assert.equal(result.errors[0], 'Choose your store.');
  assert.equal(removed, false);
});

test('Meijer product collector extracts structured name, brand, size, and receipt code', async () => {
  const product = { '@type': 'Product', name: 'Meijer Steamable Mixed Vegetables, 12 oz', brand: {'name':'Meijer'}, url:'https://www.meijer.com/shopping/product/mixed/71928395643.html'};
  const context = vm.createContext({URL, Date, setTimeout,
    location:{href:'https://www.meijer.com/shopping/search.html?text=71928395643'},
    document:{querySelectorAll: selector => selector.startsWith('script') ? [{textContent:JSON.stringify(product)}] : [], body:{innerText:''}},
  });
  vm.runInContext(fs.readFileSync(path.join(dir, 'product-capture.js'), 'utf8'), context);
  const result = await context.PantryProductCapture();
  assert.equal(result.candidates[0].receipt_code, '71928395643');
  assert.equal(result.candidates[0].brand, 'Meijer');
  assert.equal(result.candidates[0].size, '12 oz');
});


test('product card capture reads rendered Meijer links and ignores external products', async () => {
  const {JSDOM} = require('jsdom');
  const dom = new JSDOM(`<article class="product-card"><a href="/shopping/product/mixed/71928395643.html"><h3>Meijer Steamable Mixed Vegetables, 12 oz</h3></a><span itemprop="brand">Meijer</span></article><article><a href="https://attacker.test/shopping/product/fake/12345678901.html"><h3>Fake product</h3></a></article>`, {url:'https://www.meijer.com/shopping/search.html?text=71928395643'});
  const context = vm.createContext({URL,Date,setTimeout,document:dom.window.document,location:dom.window.location});
  vm.runInContext(fs.readFileSync(path.join(dir, 'product-capture.js'), 'utf8'), context);
  const result = await context.PantryProductCapture();
  assert.equal(result.candidates.length, 1);
  assert.equal(result.candidates[0].name, 'Meijer Steamable Mixed Vegetables, 12 oz');
  assert.equal(result.candidates[0].brand, 'Meijer');
  assert.equal(result.candidates[0].size, '12 oz');
  dom.window.close();
});


test('Safari lookup accepts redirected q searches while tab status remains loading', async () => {
  let currentUrl;
  const context = vm.createContext({URL,setTimeout});
  vm.runInContext(fs.readFileSync(path.join(dir, 'product-lookup.js'), 'utf8'), context);
  const product = {name:'Mixed Vegetables, 12 oz',size:'12 oz',receipt_code:'71928395643',url:'https://www.meijer.com/shopping/product/mixed/71928395643.html'};
  const api = {
    tabs: {
      create:async value => {currentUrl = value.url.replace('?text=', '?q='); return {id:77};},
      update:async (id,value) => {currentUrl = value.url;},
      get:async () => ({status:'loading',url:currentUrl,title:'Results for 71928395643 | Meijer'}),
      remove:async () => {},
    },
    scripting: {executeScript:async value => !value.func ? [] : [{result:
      value.func.toString().includes('ready:') ? {ready:true,url:currentUrl,title:'Results for 71928395643 | Meijer'} : {candidates:[product]}}]},
  };
  const result = await context.lookupMeijerProducts(api,[{raw_code:'71928395643'}],() => {});
  assert.equal(result.errors.length,0);
  assert.equal(result.products['71928395643'][0].match_kind,'exact_code');
});


test('scanner searches only the unknown list supplied by the database-first server', async () => {
  const unknown = {raw_code:'22222222222',upc:'22222222222',receipt_description:'Unknown'};
  const {calls} = await scan({lookupItems:[unknown]});
  const lookup = calls.find(call => call[0] === 'meijer');
  assert.deepEqual(lookup[1], [unknown]);
  assert.equal(lookup[1].some(item => item.raw_code === '11111111111'), false);
  assert.equal(calls.filter(call => call[0] === 'fetch' && call[1].includes('meijer-products')).length, 1);
});

test('scanner does not search every line if database classification is missing', async () => {
  const {calls, elements} = await scan({missingLookup:true});
  assert.match(elements.status.textContent, /check the database before Meijer/);
  assert.equal(calls.some(call => call[0] === 'meijer'), false);
});


test('Costco domains and PDFs are accepted without allowing lookalike hosts', () => {
  assert.equal(core.PantryExtension.isReceiptPage('https://www.costco.com/myaccount/'), true);
  assert.equal(core.PantryExtension.isReceiptPDF('https://www.costco.com/receipt.pdf?download=1'), true);
  for (const url of ['http://costco.com', 'https://costco.com.attacker.test', 'https://fakecostco.com']) {
    assert.equal(core.PantryExtension.isReceiptPage(url), false);
  }
});

test('Costco receipt captures into the existing review without Meijer product searches', async () => {
  const {calls, elements} = await scan({url: 'https://www.costco.com/myaccount/', receiptText: 'COSTCO\nE 5331 ORG CLASSICO 12.79 N'});
  const request = calls.find(call => call[0] === 'fetch' && call[1].endsWith('/api/receipts/browser'));
  assert.ok(request);
  assert.match(JSON.parse(request[2].body).text, /COSTCO/);
  assert.equal(calls.some(call => call[0] === 'meijer'), false);
  assert.equal(calls.filter(call => call[0] === 'tab').length, 1);
  assert.match(elements.status.textContent, /Captured/);
});

test('Costco capture prefers the open receipt dialog and adds store text for an image logo', () => {
  const listeners = [];
  const dialog = {innerText: 'In-Warehouse Receipt\nE 5331 ORG CLASSICO 12.79 N',
    getBoundingClientRect: () => ({width: 500}), querySelectorAll: () => []};
  const context = vm.createContext({browser: {runtime: {onMessage: {addListener: fn => listeners.push(fn)}}},
    document: {body: {innerText: 'Account history\nOther receipts'}, querySelectorAll: () => [dialog], images: []},
    location: {hostname: 'www.costco.com', href: 'https://www.costco.com/myaccount/'}});
  vm.runInContext(fs.readFileSync(path.join(dir, 'content.js'), 'utf8'), context);
  let capture;
  listeners[0]({type: 'PANTRY_KEEPER_CAPTURE'}, {}, value => {capture = value;});
  assert.match(capture.text, /^COSTCO WHOLESALE/);
  assert.match(capture.text, /ORG CLASSICO/);
  assert.doesNotMatch(capture.text, /Account history|Other receipts/);
});
