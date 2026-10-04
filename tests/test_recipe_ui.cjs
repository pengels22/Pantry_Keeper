const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');

function setup({missingKey = false} = {}) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'templates/recipes.html'), 'utf8'), {
    url: 'http://pantry.test/recipes', runScripts: 'outside-only'
  });
  const calls = [];
  const item = {inventory_id: 142, product_id: 12, name: 'Chicken <img src=x onerror=alert(1)>', quantity: 1,
    usable_quantity: 4, usable_unit: 'each', available_quantity: 4, reserved_quantity: 0, measurement_required: false};
  const proposal = {recipe: 'Chicken dinner', instructions: 'Cook thoroughly.', ingredients: [{inventory_id: 142,
    name: item.name, amount: 2, unit: 'each', available_quantity: 4, usable_unit: 'each', notes: ''}]};
  let session;
  dom.window.HTMLElement.prototype.scrollIntoView = () => {};
  dom.window.fetch = async (url, request = {}) => {
    const body = request.body ? JSON.parse(request.body) : null;
    calls.push({url, method: request.method || 'GET', body});
    let data, code = 200;
    if (url.startsWith('/api/inventory?')) data = [{...item}];
    else if (url === '/api/recipes/sessions') data = session ? [{id: session.id, title: session.title, status: session.status}] : [];
    else if (url === '/api/recipes/chat') {
      if (missingKey) { data = {detail: 'Recipe chat needs OPENAI_API_KEY configured on the server.'}; code = 503; }
      else data = {reply: 'Try chicken dinner.', proposal};
    } else if (url === '/api/recipes/session') {
      session = {id: 7, title: proposal.recipe, instructions: proposal.instructions, status: 'selected', items: [{inventory_id: 142,
        name: item.name, requested_amount: 2, requested_unit: 'each', reserved_amount: 0, reserved_unit: 'each', consumed_amount: 0, inventory: {...item}}]};
      data = session;
    } else if (url.endsWith('/reserve')) {
      item.reserved_quantity = 2; item.available_quantity = 2;
      session.status = 'reserved'; session.items[0].reserved_amount = 2; data = session;
    } else if (url.endsWith('/commit')) {
      assert.equal(body.confirmed, true);
      const amount = body.actual_usage[0].amount;
      item.usable_quantity -= amount; item.reserved_quantity = 0; item.available_quantity = item.usable_quantity;
      session.status = 'completed'; session.items[0].consumed_amount = amount; session.items[0].consumed_unit = 'each'; data = session;
    } else if (url.endsWith('/cancel')) {
      item.reserved_quantity = 0; item.available_quantity = item.usable_quantity; session.status = 'cancelled'; data = session;
    } else throw new Error(`Unexpected request: ${url}`);
    return {ok: code === 200, status: code, json: async () => JSON.parse(JSON.stringify(data))};
  };
  dom.window.eval(fs.readFileSync(path.join(root, 'static/scripts/recipes.js'), 'utf8'));
  return {dom, calls, item, $: selector => dom.window.document.querySelector(selector)};
}
async function idle(state) {
  for (let i = 0; i < 100; i++) {
    await new Promise(resolve => setImmediate(resolve));
    if (!state.$('#sendChat').disabled) return;
  }
  throw new Error('UI did not finish request');
}
function submit(state, selector) {
  state.$(selector).dispatchEvent(new state.dom.window.Event('submit', {bubbles: true, cancelable: true}));
}
async function selectAndReserve(state) {
  await idle(state);
  state.$('#chatInput').value = 'What can I make with chicken?'; submit(state, '#chatForm'); await idle(state);
  state.$('#selectRecipe').click(); await idle(state);
  assert.equal(state.calls.some(call => call.url.endsWith('/reserve')), false);
  assert.equal(state.calls.some(call => call.url.endsWith('/commit')), false);
  state.$('#startCooking').click(); await idle(state);
}

test('recipe UI reserves then requires review and explicit confirmation before consumption', async () => {
  const state = setup();
  try {
    await selectAndReserve(state);
    assert.equal(state.item.usable_quantity, 4);
    assert.equal(state.item.reserved_quantity, 2);
    assert.equal(state.dom.window.document.querySelectorAll('img').length, 0);
    state.$('#finishCooking').click();
    assert.equal(state.calls.some(call => call.url.endsWith('/commit')), false);
    assert.equal(state.$('#commitForm').classList.contains('hidden'), false);
    submit(state, '#commitForm'); await idle(state);
    assert.equal(state.calls.some(call => call.url.endsWith('/commit')), false);
    state.$('#actualUsage input').value = '1'; state.$('#confirmCooked').checked = true;
    submit(state, '#commitForm'); await idle(state);
    const commit = state.calls.find(call => call.url.endsWith('/commit'));
    assert.deepEqual(commit.body, {confirmed: true, actual_usage: [{inventory_id: 142, amount: 1, unit: 'each'}]});
    assert.equal(state.item.usable_quantity, 3);
    assert.match(state.$('#sessionState').textContent, /completed/);
    assert.equal(state.$('#commitForm').classList.contains('hidden'), true);
  } finally { state.dom.window.close(); }
});

test('cancelling recipe releases reservations and leaves physical stock alone', async () => {
  const state = setup();
  try {
    await selectAndReserve(state); state.$('#cancelRecipe').click(); await idle(state);
    assert.equal(state.item.usable_quantity, 4);
    assert.equal(state.item.reserved_quantity, 0);
    assert.equal(state.calls.some(call => call.url.endsWith('/commit')), false);
    assert.match(state.$('#sessionState').textContent, /cancelled/);
  } finally { state.dom.window.close(); }
});

test('missing API key leaves manual planning and measurement setup available', async () => {
  const state = setup({missingKey: true});
  try {
    await idle(state); state.$('#chatInput').value = 'Dinner'; submit(state, '#chatForm'); await idle(state);
    assert.match(state.$('#recipeStatus').textContent, /OPENAI_API_KEY/);
    assert.ok(state.$('#manualIngredients input'));
    assert.ok(state.$('#measurementList form'));
    assert.equal(state.calls.some(call => call.url.endsWith('/commit')), false);
  } finally { state.dom.window.close(); }
});
