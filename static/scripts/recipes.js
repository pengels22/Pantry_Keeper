'use strict';
const $ = (selector) => document.querySelector(selector);
const units = ['each', 'oz', 'lb', 'g', 'kg', 'ml', 'L', 'tsp', 'tbsp', 'cup', 'pint', 'quart', 'gallon'];
let inventory = [], proposal = null, session = null, history = [], busy = false;
function node(tag, text, parent) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = String(text);
  if (parent) parent.append(element);
  return element;
}
function status(message) {
  $('#recipeStatus').textContent = message;
  $('#recipeStatus').classList.toggle('hidden', !message);
}
async function api(url, method = 'GET', body) {
  const response = await fetch(url, {method, headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check the input values.');
  return data;
}
async function action(work) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => { button.disabled = true; });
  status('Working…');
  try { await work(); status(''); }
  catch (error) { status(error.message); }
  finally {
    busy = false;
    document.querySelectorAll('button').forEach(button => { button.disabled = false; });
    updateButtons();
  }
}
function activeSession() { return session && ['planning', 'selected', 'reserved'].includes(session.status); }
function updateButtons() {
  $('#selectRecipe').disabled = busy || activeSession();
  $('#startCooking').classList.toggle('hidden', session?.status !== 'selected');
  $('#finishCooking').classList.toggle('hidden', session?.status !== 'reserved');
  $('#cancelRecipe').classList.toggle('hidden', !activeSession());
}
function inputField(parent, label, value, type = 'number') {
  const wrapper = node('label', label, parent), input = node('input', undefined, wrapper);
  input.type = type;
  input.value = value ?? '';
  if (type === 'number') { input.min = '0'; input.step = 'any'; }
  return input;
}
function unitField(parent, label, selected) {
  const wrapper = node('label', label, parent), select = node('select', undefined, wrapper);
  units.forEach(unit => { const option = node('option', unit, select); option.value = unit; });
  select.value = selected || 'each';
  return select;
}
function inventorySummary(item) {
  return item.measurement_required ? `${item.name}: measurements needed (legacy quantity ${item.quantity})` :
    `${item.name}: ${item.available_quantity} ${item.usable_unit} available · ${item.reserved_quantity} reserved · ${item.usable_quantity} physically in stock`;
}
async function loadInventory() {
  inventory = [];
  for (let offset = 0; ; offset += 200) {
    const page = await api(`/api/inventory?offset=${offset}&limit=200`);
    inventory.push(...page);
    if (page.length < 200) break;
  }
  renderInventory();
}
function renderInventory() {
  const list = $('#measurementList'), manual = $('#manualIngredients');
  list.replaceChildren(); manual.replaceChildren();
  inventory.forEach(item => {
    const details = node('details', undefined, list);
    node('summary', inventorySummary(item), details);
    const form = node('form', undefined, details); form.className = 'form-grid';
    const packageQuantity = inputField(form, 'Package count (optional)', item.package_quantity);
    const packageSize = inputField(form, 'Size per package', item.package_size);
    const packageUnit = unitField(form, 'Package unit', item.package_unit || item.usable_unit);
    const usable = inputField(form, 'Usable quantity (leave blank to calculate from packages)', item.usable_quantity);
    const usableUnit = unitField(form, 'Usable unit', item.usable_unit);
    const save = node('button', 'Save Measurements', form); save.type = 'submit';
    form.addEventListener('submit', event => {
      event.preventDefault();
      action(async () => {
        const body = {usable_unit: usableUnit.value, usable_quantity: usable.value === '' ? null : Number(usable.value)};
        if (packageQuantity.value !== '' || packageSize.value !== '') {
          body.package_quantity = packageQuantity.value === '' ? null : Number(packageQuantity.value);
          body.package_size = packageSize.value === '' ? null : Number(packageSize.value);
          body.package_unit = packageUnit.value;
        }
        await api(`/api/inventory/${item.inventory_id}/measurements`, 'PUT', body);
        await loadInventory();
      });
    });
    const historyButton = node('button', 'View Transaction History', details); historyButton.type = 'button'; historyButton.className = 'secondary';
    const entries = node('ul', undefined, details);
    historyButton.addEventListener('click', () => action(async () => {
      const transactions = await api(`/api/inventory/${item.inventory_id}/transactions`);
      entries.replaceChildren();
      transactions.forEach(entry => node('li', `${entry.transaction_type}: ${entry.change_amount} ${entry.unit || ''} (${entry.quantity_field}) · ${new Date(entry.created_at + 'Z').toLocaleString()}`, entries));
      if (!transactions.length) node('li', 'No transactions yet.', entries);
    }));
    if (!item.measurement_required) {
      const row = node('div', undefined, manual); row.dataset.inventoryId = item.inventory_id;
      node('p', inventorySummary(item), row);
      const amount = inputField(row, 'Recipe amount', 0); amount.dataset.usageAmount = '';
      const unit = unitField(row, 'Recipe unit', item.usable_unit); unit.dataset.usageUnit = '';
    }
  });
  if (!inventory.length) node('p', 'Import or add inventory from Pantry Keeper first.', list);
}
function showProposal(value) {
  proposal = value;
  $('#proposalPanel').classList.remove('hidden');
  $('#proposalTitle').textContent = value.recipe;
  $('#proposalInstructions').textContent = value.instructions;
  $('#proposalItems').replaceChildren();
  value.ingredients.forEach(item => node('p', `✓ ${item.name}: ${item.amount} ${item.unit} requested · ${item.available_quantity} ${item.usable_unit} available`, $('#proposalItems')));
  updateButtons();
}
function showSession(value) {
  session = value;
  const url = new URL(location.href); url.searchParams.set('session', value.id); window.history.replaceState({}, '', url);
  $('#sessionPanel').classList.remove('hidden'); $('#commitForm').classList.add('hidden');
  $('#sessionTitle').textContent = value.title; $('#sessionState').textContent = `Status: ${value.status}`;
  $('#sessionInstructions').textContent = value.instructions;
  $('#sessionItems').replaceChildren();
  value.items.forEach(item => node('p', value.status === 'reserved' ? `${item.name}: ${item.reserved_amount} ${item.reserved_unit} reserved` :
    value.status === 'completed' ? `${item.name}: ${item.consumed_amount} ${item.consumed_unit} consumed` :
    `${item.name}: ${item.requested_amount} ${item.requested_unit} requested · ${inventorySummary(item.inventory)}`, $('#sessionItems')));
  updateButtons();
}
async function loadSessions() {
  const values = await api('/api/recipes/sessions'); $('#sessionList').replaceChildren();
  values.forEach(value => { const option = node('option', `${value.title} · ${value.status}`, $('#sessionList')); option.value = value.id; });
  if (session) $('#sessionList').value = session.id;
}
$('#chatForm').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const message = $('#chatInput').value.trim(); if (!message) throw new Error('Enter a recipe request.');
    const response = await api('/api/recipes/chat', 'POST', {message, history: history.slice(-20)});
    node('p', `You: ${message}`, $('#conversation'));
    const reply = node('p', `Assistant: ${response.reply}`, $('#conversation')); reply.className = 'recipe-instructions';
    history.push({role: 'user', content: message}, {role: 'assistant', content: response.reply.slice(0, 10000)});
    $('#chatInput').value = '';
    if (response.proposal) showProposal(response.proposal);
  });
});
$('#manualRecipe').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const ingredients = [...$('#manualIngredients').children].map(row => ({inventory_id: Number(row.dataset.inventoryId),
      amount: Number(row.querySelector('[data-usage-amount]').value), unit: row.querySelector('[data-usage-unit]').value})).filter(item => item.amount > 0);
    showProposal(await api('/api/recipes/propose', 'POST', {recipe: $('#manualRecipeTitle').value, instructions: $('#manualInstructions').value, ingredients}));
    $('#proposalPanel').scrollIntoView({behavior: 'smooth'});
  });
});
$('#selectRecipe').addEventListener('click', () => action(async () => {
  if (activeSession()) throw new Error('Complete or cancel the current recipe first.');
  const clean = {recipe: proposal.recipe, instructions: proposal.instructions, ingredients: proposal.ingredients.map(({inventory_id, name, amount, unit, notes}) => ({inventory_id, name, amount, unit, notes}))};
  showSession(await api('/api/recipes/session', 'POST', {proposal: clean})); await loadSessions();
}));
$('#startCooking').addEventListener('click', () => action(async () => {
  showSession(await api(`/api/recipes/${session.id}/reserve`, 'POST')); await loadInventory(); await loadSessions();
}));
$('#cancelRecipe').addEventListener('click', () => action(async () => {
  showSession(await api(`/api/recipes/${session.id}/cancel`, 'POST')); await loadInventory(); await loadSessions();
}));
$('#finishCooking').addEventListener('click', () => {
  if (busy || session?.status !== 'reserved') return;
  $('#actualUsage').replaceChildren(); $('#confirmCooked').checked = false;
  session.items.forEach(item => {
    const row = node('div', undefined, $('#actualUsage')); row.dataset.inventoryId = item.inventory_id; row.dataset.unit = item.reserved_unit;
    const input = inputField(row, `${item.name} (${item.reserved_unit})`, item.reserved_amount); input.required = true;
  });
  $('#commitForm').classList.remove('hidden');
});
$('#backToRecipe').addEventListener('click', () => $('#commitForm').classList.add('hidden'));
$('#commitForm').addEventListener('submit', event => {
  event.preventDefault();
  if (!$('#confirmCooked').checked) return;
  action(async () => {
    const actual_usage = [...$('#actualUsage').children].map(row => ({inventory_id: Number(row.dataset.inventoryId), amount: Number(row.querySelector('input').value), unit: row.dataset.unit}));
    showSession(await api(`/api/recipes/${session.id}/commit`, 'POST', {confirmed: true, actual_usage}));
    await loadInventory(); await loadSessions();
  });
});
$('#refreshInventory').addEventListener('click', () => action(loadInventory));
$('#openSession').addEventListener('click', () => action(async () => {
  if (!$('#sessionList').value) throw new Error('No recipe sessions yet.');
  showSession(await api(`/api/recipes/${$('#sessionList').value}`));
}));
action(async () => {
  await loadInventory(); await loadSessions();
  const id = new URL(location.href).searchParams.get('session');
  if (id && /^\d+$/.test(id)) showSession(await api(`/api/recipes/${id}`));
});
