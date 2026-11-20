/* Exercise the shipped bundle with DOM, time and HTTP doubles; no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function element(overrides = {}) {
  const listeners = new Map();
  return Object.assign({
    dataset: {}, value: '', textContent: '', hidden: false,
    classList: { add() {}, remove() {}, toggle() {} },
    querySelector() { return null; }, querySelectorAll() { return []; },
    matches() { return false; }, closest() { return null; },
    contains() { return true; }, setAttribute() {}, removeAttribute() {},
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    emit(type, details = {}) {
      const event = Object.assign({ type, target: this, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} }, details);
      for (const listener of listeners.get(type) || []) listener(event);
      return event;
    }, listeners,
  }, overrides);
}

function response(data, ok = true) {
  return { ok, status: ok ? 200 : 400, statusText: 'Error',
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(data), text: () => Promise.resolve(JSON.stringify(data)) };
}

function environment() {
  const document = element({ body: element(), documentElement: element(), getElementById() { return null; } });
  const timers = new Map();
  const requests = [], beacons = [], opened = [], feedback = [];
  let nextTimer = 1;
  let fetchHandler = () => Promise.resolve(response({}));
  const window = element({ document, location: { href: 'http://local.test/configs/aa/', pathname: '/configs/aa/' },
    navigator: { sendBeacon(url, body) { beacons.push({ url, body }); return true; } },
    setTimeout(fn, delay) { const id = nextTimer++; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return 0; }, clearInterval() {},
    open(...args) { opened.push(args); }, localStorage: { getItem() { return null; }, setItem() {} } });
  class FormDataDouble {
    constructor(form) { this.values = { ...(form.fields || {}) }; }
  }
  const context = vm.createContext({ window, document, URL, URLSearchParams, FormData: FormDataDouble,
    console, Event: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    fetch(url, options = {}) { requests.push({ url, options }); return fetchHandler(url, options); } });
  const bundle = fs.readFileSync(path.join(__dirname, '../../ciudades_del_mundo/static/ciudades_del_mundo/app.js'), 'utf8');
  // Test-only access to existing private functions, without adding a production API.
  const names = ['initConfigEditorSubmitGuards', 'scheduleConfigEditorAutosave', 'flushConfigEditorAutosave',
    'ensureConfigCityChildren', 'select2NormalizedMatcher', 'initClickableRows', 'bindMiddleClickNavigation',
    'initTaskDetailLog', 'bindTaskCancelForm', 'updateTaskDetailFromPayload', 'taskDetailStatusUrl', 'initStatsCountries',
    'initConfigTaskActions', 'initConfigExportActions', 'clientRowMatchesForm',
    'renderCountryTablePanel', 'renderStatsLevelControls', 'loadStatsCountryDetail', 'loadGroupCountryDetailPanel'];
  const hook = `globalThis.behavior = {${names.join(',')}};
    globalThis.observeTaskActions = function(callback) {
      pollConfigTask = function(url) { callback(url); };
    };
    globalThis.observeCityBuilder = function(callback) {
      updateManualConfigCities = function(form, rows, data) { callback(data); };
    };
    globalThis.observeStatsRendering = function(callback) {
      renderStatsCountryGrid = function(container, payload) { callback('render', payload); };
      setLoading = function() { callback('loading'); };
      setError = function() { callback('error'); };
    };
    globalThis.observeGroupRendering = function(callback) {
      renderGroupCountryDetailPayload = function(panel, payload) { callback(payload); };
    };
    globalThis.stubCountryDetailCharts = function() {
      renderCountryGeneralPanel = function() {};
      renderStatsChildrenCharts = function() {};
    };
    globalThis.observeStartup = function(callback) {
      initSelect2 = function() { callback('select2'); };
      initConfigEditor = function() { callback('config-editor'); };
      initConfigTaskActions = function() { callback('task-actions'); };
    };
    globalThis.setFeedback = function(callback) {
      createConfigToast = function() { return {element: {classList: {add() {}}}}; };
      setConfigToastStatus = function(toast, state, message) { callback(state, message); };
      scheduleConfigToastDismiss = function() {};
      refreshTaskTables = function() {};
    };`;
  assert.match(bundle, /\}\)\(\);\s*$/);
  vm.runInContext(bundle.replace(/\}\)\(\);\s*$/, hook + '\n})();'), context);
  context.setFeedback((state, message) => feedback.push({ state, message }));
  return { api: context.behavior, context, document, window, requests, beacons, opened, feedback, timers,
    setFetch(fn) { fetchHandler = fn; },
    async tick() {
      const first = timers.entries().next().value;
      assert.ok(first, 'A timer must be scheduled');
      timers.delete(first[0]); first[1].fn();
      await new Promise(setImmediate);
    }, async settle() { await new Promise(setImmediate); } };
}

function tableDom(env) {
  const nodes = [];
  env.document.createTextNode = text => element({ textContent: text });
  env.document.createElement = tagName => {
    const node = element({ tagName, children: [], style: {},
      appendChild(child) { this.children.push(child); return child; },
      querySelectorAll(selector) {
        const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
        return selector === '[data-sort]' ? descendants.filter(child => child.dataset.sort) : descendants;
      } });
    Object.defineProperty(node, 'innerHTML', { set() { this.children = []; } });
    nodes.push(node);
    return node;
  };
  return nodes;
}

test('reopening a group country refreshes deleted records while sharing in-flight requests', async () => {
  const env = environment();
  const card = element({ dataset: { groupDetailUrl: '/groups/countries/cuba/data/' } });
  const loading = element();
  const panelBox = element();
  const panel = element({ querySelector(selector) {
    if (selector === '[data-group-panel-loading]') return loading;
    if (selector === '[data-group-country-panel]') return panelBox;
    return null;
  } });
  const rendered = [];
  env.context.observeGroupRendering(data => rendered.push(data));
  let payload = { groups: [{ internal_name: 'A' }, { internal_name: 'DUPLICATE' }], division_rows: [] };
  let release;
  env.setFetch(() => new Promise(resolve => { release = () => resolve(response(payload)); }));
  const initial = env.api.loadGroupCountryDetailPanel(card, panel);
  assert.equal(env.api.loadGroupCountryDetailPanel(card, panel), initial);
  assert.equal(env.requests.length, 1);
  release();
  await initial;
  assert.equal(rendered[0].groups.length, 2);
  payload = { groups: [{ internal_name: 'A' }], division_rows: [] };
  loading.hidden = false;
  const refreshed = env.api.loadGroupCountryDetailPanel(card, panel);
  assert.equal(env.requests.length, 2, 'A loaded panel must revalidate SQL data when reopened');
  assert.equal(loading.hidden, true, 'Cached rows remain visible during revalidation');
  assert.equal(rendered.length, 1);
  assert.equal(env.api.loadGroupCountryDetailPanel(card, panel), refreshed);
  assert.equal(env.requests.length, 2);
  release();
  await refreshed;
  assert.equal(rendered.length, 2);
  assert.equal(rendered[1].groups.length, 1, 'The deleted record disappears after revalidation');
});

test('level one country table shows and filters every row without pagination', () => {
  const env = environment();
  const nodes = tableDom(env);
  const panel = env.document.createElement('article');
  const rows = Array.from({ length: 102 }, (_, i) => ({ name: `Region ${i}`, population: i }));
  env.api.renderCountryTablePanel(panel, { selected_level: 1, table: { rows } }, {}, '', null, null);
  const body = nodes.find(node => node.tagName === 'tbody');
  assert.equal(body.children.length, 102);
  assert.ok(!panel.children.some(node => node.className === 'country-table-pagination'));
  const filter = nodes.find(node => node.type === 'search');
  filter.value = 'Region 101';
  filter.emit('input');
  assert.equal(body.children.length, 1);
  assert.equal(body.children[0].children[0].textContent, 'Region 101');
});

test('country level controls omit page size at level one and retain it for deeper levels', () => {
  for (const level of [1, 2]) {
    const env = environment();
    tableDom(env);
    const panel = env.document.createElement('article');
    const data = { selected_level: level, levels: [{ value: 1 }, { value: 2 }],
      table: { pagination: { page_size: 20, total: 102, num_pages: 6 } } };
    env.api.renderStatsLevelControls(panel, data, {}, null, '', null);
    const selects = panel.querySelectorAll('*').filter(node => node.tagName === 'select');
    assert.equal(selects.length, level === 1 ? 1 : 2);
  }
});

test('countries first detail panel has all children and no pagination when its first stored level is two', async () => {
  const env = environment();
  const nodes = tableDom(env);
  env.context.stubCountryDetailCharts();
  const target = env.document.createElement('section');
  target.classList.contains = () => false;
  target.scrollIntoView = () => {};
  const children = Array.from({ length: 102 }, (_, i) => ({ name: `Region ${i}`, population: i }));
  env.setFetch(() => Promise.resolve(response({ selected_level: 2,
    levels: [{ value: 2 }, { value: 3 }], country: {},
    first_order: { child_groups: [{ children }],
      pagination: { page: 1, page_size: 102, total: 102, num_pages: 1 } } })));
  env.api.loadStatsCountryDetail(target, 'http://local.test/api/countries/example/', target);
  await env.settle();
  assert.match(env.requests[0].url, /include_children=0/);
  const body = nodes.find(node => node.tagName === 'tbody');
  assert.ok(body, 'Country detail children table must render');
  assert.equal(body.children.length, 102);
  const rendered = target.querySelectorAll('*');
  assert.ok(!rendered.some(node => (node.className || '').includes('country-table-pagination')));
  assert.equal(rendered.filter(node => node.tagName === 'select').length, 1);
});

test('unpopulated filter excludes saved data regardless of workflow status', () => {
  const env = environment();
  const status = element({ value: 'unpopulated' });
  const form = element({ dataset: { clientSideFilter: '1' },
    querySelector: selector => selector === "select[name='status']" ? status : null });
  for (const workflow of ['pending', 'validated', 'failed', 'populating', 'populated']) {
    const row = element({ dataset: { status: workflow, rows: '42' } });
    assert.equal(env.api.clientRowMatchesForm(row, form), false, workflow);
    row.dataset.rows = '0';
    assert.equal(env.api.clientRowMatchesForm(row, form), workflow !== 'populated', workflow);
  }
  status.value = 'failed';
  assert.equal(env.api.clientRowMatchesForm(element({ dataset: { status: 'failed', rows: '42' } }), form), true);
});

function editorFixture(env) {
  const csrf = element({ value: 'csrf-token' });
  const mode = element({ value: 'raw' });
  const form = element({ action: '/configs/aa/', fields: { content: 'old TOML' }, dataset: { configEditorForm: 'raw' },
    querySelector(selector) {
      if (selector.includes('csrfmiddlewaretoken')) return csrf;
      if (selector.includes('editor_mode')) return mode;
      return null;
    } });
  const editor = element({ dataset: { mode: 'edit' },
    querySelectorAll(selector) { return selector === '[data-config-editor-form]' ? [form] : []; } });
  env.api.initConfigEditorSubmitGuards(editor);
  return { form, editor, mode };
}

function statsFixture(env, cached) {
  const container = element({ dataset: { url: '/api/countries/' } });
  env.document.querySelector = selector => selector === '[data-stats-countries]' ? container : null;
  let stored = cached ? JSON.stringify({ url: container.dataset.url, payload: cached }) : null;
  env.window.sessionStorage = { getItem: () => stored, setItem(key, value) { stored = value; } };
  const calls = [];
  env.context.observeStatsRendering((type, payload) => calls.push({ type, payload }));
  return { calls, stored: () => stored };
}

test('country summary renders cached cards while the SQL refresh is still pending', async () => {
  const env = environment();
  const { calls, stored } = statsFixture(env, { countries: [{ code: 'old' }] });
  let finish;
  env.setFetch(() => new Promise(resolve => { finish = resolve; }));
  env.api.initStatsCountries(env.document);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, 'render');
  assert.equal(calls[0].payload.countries[0].code, 'old');
  assert.equal(env.requests.length, 1);
  finish(response({ countries: [{ code: 'fresh' }] }));
  await env.settle();
  assert.equal(calls[1].payload.countries[0].code, 'fresh');
  assert.equal(JSON.parse(stored()).payload.countries[0].code, 'fresh');
});

test('unchanged or unavailable country refresh preserves the visible cards', async () => {
  for (const failure of [false, true]) {
    const env = environment(), cached = { countries: [{ code: 'aa' }] };
    const { calls } = statsFixture(env, cached);
    env.setFetch(() => failure ? Promise.reject(new Error('offline')) : Promise.resolve(response(cached)));
    env.api.initStatsCountries(env.document);
    await env.settle();
    assert.deepEqual(calls.map(x => x.type), ['render']);
  }
});

test('country summary without cache shows loading and replaces removed countries with fresh SQL data', async () => {
  const env = environment(), { calls } = statsFixture(env, null);
  env.setFetch(() => Promise.resolve(response({ countries: [] })));
  env.api.initStatsCountries(env.document);
  assert.equal(calls[0].type, 'loading');
  await env.settle();
  assert.equal(calls[1].type, 'render');
  assert.equal(calls[1].payload.countries.length, 0);
});

test('DOMContentLoaded runs selectors before the config editor and later task bindings', () => {
  const env = environment(), calls = [], errors = [];
  env.context.console = { error: (...args) => errors.push(args) };
  env.context.observeStartup(name => calls.push(name));
  env.document.emit('DOMContentLoaded');
  assert.deepEqual(calls, ['select2', 'config-editor', 'task-actions']);
  assert.deepEqual(errors, []);
});

test('autosave debounces inputs, immediately saves changes and sends the latest SQL TOML form', async () => {
  const env = environment(), { form, editor } = editorFixture(env);
  env.api.initConfigEditorSubmitGuards(editor); // No duplicate listeners.
  form.emit('input'); form.fields.content = 'latest TOML'; form.emit('input');
  assert.equal(env.timers.size, 1);
  assert.equal([...env.timers.values()][0].delay, 300);
  form.emit('change');
  assert.equal([...env.timers.values()][0].delay, 0);
  await env.tick();
  assert.equal(env.requests.length, 1);
  const sent = env.requests[0];
  assert.equal(sent.options.method, 'POST');
  assert.equal(sent.options.body.values.content, 'latest TOML');
  assert.equal(sent.options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(form._configAutosavePending, false);
});

test('hydration does not autosave; unloading flushes edits with sendBeacon', () => {
  const env = environment(), { form, editor, mode } = editorFixture(env);
  editor.dataset.configHydrating = '1'; form.emit('change');
  assert.equal(env.timers.size, 0);
  delete editor.dataset.configHydrating;
  form.emit('input'); env.window.emit('beforeunload');
  assert.equal(env.beacons.length, 1);
  assert.equal(env.beacons[0].url, '/configs/aa/');
  assert.equal(mode.value, 'raw');
  assert.equal(env.timers.size, 0);
});

test('unloading falls back to a CSRF-protected keepalive POST when Beacon rejects delivery', () => {
  const env = environment(), { form } = editorFixture(env);
  env.window.navigator.sendBeacon = () => false;
  form.emit('input'); env.window.emit('beforeunload');
  assert.equal(env.requests.length, 1);
  assert.equal(env.requests[0].options.keepalive, true);
  assert.equal(env.requests[0].options.credentials, 'same-origin');
  assert.equal(env.requests[0].options.headers['X-CSRFToken'], 'csrf-token');
});

test('an older save response must not discard newer unsaved edits before unload', async () => {
  const env = environment(), { form } = editorFixture(env);
  let complete;
  env.setFetch(() => new Promise(resolve => { complete = resolve; }));
  form.emit('change'); await env.tick();
  form.fields.content = 'newer TOML'; form.emit('input');
  complete(response({})); await env.settle();
  env.window.emit('beforeunload');
  assert.equal(env.beacons.length, 1, 'New edits remain pending after the old response');
  assert.equal(env.beacons[0].body.values.content, 'newer TOML');
});

test('failed autosave keeps changes pending so unloading can recover them', async () => {
  const env = environment(), { form } = editorFixture(env);
  env.setFetch(() => Promise.resolve(response({ error: 'Simulated save failure' }, false)));
  form.emit('change'); await env.tick();
  assert.equal(form._configAutosavePending, true);
  env.window.emit('beforeunload');
  assert.equal(env.beacons.length, 1);
});

test('Popular submits to the task API with visible feedback instead of saving the editor', async () => {
  const env = environment(), { form } = editorFixture(env), polled = [];
  form.hasAttribute = () => false;
  const taskUrl = '/configs/austria/task/scrape/';
  const button = element({ form, textContent: 'Popular', dataset: { configAction: 'scrape' },
    matches: selector => selector === '[data-config-action]',
    hasAttribute: name => name === 'formaction', getAttribute: () => taskUrl });
  const root = element({ querySelectorAll: selector => selector === '[data-config-action]' ? [button] : [] });
  env.context.observeTaskActions(url => polled.push(url));
  env.setFetch(() => Promise.resolve(response({ ok: true, status: 'queued', status_url: '/tasks/test/status/' })));
  env.api.initConfigTaskActions(root);
  form.emit('submit', { submitter: button });
  await env.settle();
  assert.equal(env.requests.length, 1);
  assert.equal(new URL(env.requests[0].url).pathname, taskUrl);
  assert.equal(env.requests[0].options.method, 'POST');
  assert.equal(env.requests[0].options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(env.feedback[0].state, 'queued');
  assert.deepEqual(polled, ['/tasks/test/status/']);
});

test('TOML actions use their external form and the clicked button', async () => {
  const env = environment(), csrf = element({ value: 'merge-csrf' });
  const form = element({ action: '/configs/austria/cities/import-toml/', dataset: { actionLabel: 'Importar TOML' },
    querySelector: selector => selector.includes('csrfmiddlewaretoken') ? csrf : null });
  const button = element({ form, textContent: 'Importar TOML' });
  const root = element({ querySelectorAll: () => [form] });
  let finish;
  env.setFetch(() => new Promise(resolve => { finish = resolve; }));
  env.api.initConfigExportActions(root);
  form.emit('submit', { submitter: button });
  assert.equal(button.disabled, true);
  assert.equal(env.requests[0].url, form.action);
  assert.equal(env.requests[0].options.headers['X-CSRFToken'], 'merge-csrf');
  finish(response({ ok: true })); await env.settle();
  assert.equal(button.disabled, false);
});

test('saving a legal level refreshes the city selector only for the latest edit', async () => {
  const env = environment(), { form } = editorFixture(env), rendered = [];
  const loaded = element({ value: '1' }), querySelector = form.querySelector;
  form.querySelector = selector => selector === '[data-config-cities-loaded]' ? loaded : querySelector(selector);
  env.context.observeCityBuilder(data => rendered.push(data.legal_level));
  let finish;
  env.setFetch(() => new Promise(resolve => { finish = resolve; }));
  form.emit('change'); await env.tick();
  form.emit('input'); // A newer legal level is still waiting to be saved.
  finish(response({ city_builder: { legal_level: 2 } })); await env.settle();
  assert.deepEqual(rendered, []);
  await env.tick();
  finish(response({ city_builder: { legal_level: 3 } })); await env.settle();
  assert.deepEqual(rendered, [3]);
  assert.equal(loaded.value, '0', 'Unrelated saves must preserve unifications hidden by the new legal level');
});

test('city selector requests retain country and parent scope, filter the legal level and share in-flight work', async () => {
  const env = environment(); let finish;
  env.setFetch(() => new Promise(resolve => { finish = resolve; }));
  const form = { _configCityBuilderState: { data: { country_code: 'actual-sql-country', legal_level: '4',
    children_url: '/groups/source-data/', children_by_parent: {} } } };
  const first = env.api.ensureConfigCityChildren(form, 'parent-id');
  const second = env.api.ensureConfigCityChildren(form, 'parent-id');
  assert.equal(first, second);
  const url = new URL(env.requests[0].url, env.window.location.href);
  assert.equal(url.searchParams.get('country_code'), 'actual-sql-country');
  assert.equal(url.searchParams.get('parent_id'), 'parent-id');
  finish(response({ children: [{ id: 'city', name: 'City', level: 4 }, { id: 'wrong', name: 'Wrong', level: 3 }] }));
  const children = await first;
  assert.deepEqual(Array.from(children, child => child.id), ['city']);
  await env.api.ensureConfigCityChildren(form, 'parent-id');
  assert.equal(env.requests.length, 1);
});

test('selector matching preserves accent/case normalization and empty search options', () => {
  const env = environment(), option = { text: 'Tánger-Assilah', id: 'parent' };
  assert.equal(env.api.select2NormalizedMatcher({ term: 'TANGER' }, option), option);
  assert.equal(env.api.select2NormalizedMatcher({ term: '' }, option), option);
  assert.equal(env.api.select2NormalizedMatcher({ term: 'different' }, option), null);
});

test('row navigation supports keyboard/new tabs while ignoring controls and duplicate middle-click events', () => {
  const env = environment();
  const row = element({ dataset: { rowHref: '/groups/groups/aa/group/' } });
  const root = element({ querySelectorAll: () => [row] });
  env.api.initClickableRows(root); env.api.initClickableRows(root);
  row.emit('click', { ctrlKey: true });
  assert.equal(env.opened.length, 1);
  row.emit('click', { target: element({ closest: () => ({}) }) });
  assert.equal(env.opened.length, 1);
  row.emit('keydown', { key: 'Enter' });
  assert.equal(env.window.location.href, row.dataset.rowHref);
  row.emit('mouseup', { button: 1 }); row.emit('auxclick', { button: 1 });
  assert.equal(env.opened.length, 2);
  assert.equal(env.opened[1][2], 'noopener');
});

function taskFixture(env) {
  const output = element({ textContent: 'initial\n' }), cancel = element({ action: '/tasks/id/cancel/' });
  const button = element({ textContent: 'Cancel' }), token = element({ value: 'csrf-token' });
  cancel.querySelector = selector => selector.includes('csrfmiddlewaretoken') ? token : button;
  const status = element();
  const panel = element({ dataset: { statusUrl: '/tasks/id/status/', logOffset: '8', active: '1', cancelledLabel: 'Translated cancelled' },
    querySelector: () => cancel });
  const nodes = { '[data-task-detail]': panel, '[data-task-log-output]': output, '[data-task-status-text]': status,
    '[data-task-cancel-form]': cancel };
  env.document.querySelector = selector => nodes[selector] || null;
  return { panel, output, cancel, button, status };
}

test('task polling uses log offsets, appends deltas and stops after the terminal response', async () => {
  const env = environment(), { panel, output, cancel } = taskFixture(env);
  const replies = [{ status: 'running', is_active: true, output_delta: 'delta\n', output_offset: 14 },
    { status: 'succeeded', is_active: false, output_delta: 'done\n', output_offset: 19 }];
  env.setFetch(() => Promise.resolve(response(replies.shift())));
  env.api.initTaskDetailLog(env.document); env.api.initTaskDetailLog(env.document);
  await env.tick();
  assert.equal(new URL(env.requests[0].url).searchParams.get('since'), '8');
  await env.tick();
  assert.equal(new URL(env.requests[1].url).searchParams.get('since'), '14');
  assert.equal(output.textContent, 'initial\ndelta\ndone\n');
  assert.equal(panel.dataset.active, '0'); assert.equal(cancel.hidden, true);
  assert.equal(env.timers.size, 0);
});

test('task cancellation posts once, updates the log/status and restores the button', async () => {
  const env = environment(), { panel, cancel, button, status } = taskFixture(env);
  env.setFetch(() => Promise.resolve(response({ status: 'cancelled', is_active: false,
    output_delta: 'cancelled\n', output_offset: 18, message: 'Server cancellation' })));
  env.api.bindTaskCancelForm(panel); env.api.bindTaskCancelForm(panel);
  const event = cancel.emit('submit', { submitter: button });
  assert.equal(event.defaultPrevented, true); assert.equal(button.disabled, true);
  await env.settle();
  assert.equal(env.requests.length, 1);
  assert.equal(env.requests[0].url, '/tasks/id/cancel/');
  assert.equal(env.requests[0].options.method, 'POST');
  assert.equal(env.requests[0].options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(status.textContent, 'Translated cancelled');
  assert.equal(cancel.hidden, true); assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Cancel');
});


test('terminal task polling drains remaining log chunks before stopping', async () => {
  const env = environment(), { panel, output } = taskFixture(env);
  const replies = [
    { status: 'succeeded', is_active: false, output_delta: 'chunk', output_offset: 13, output_has_more: true },
    { status: 'succeeded', is_active: false, output_delta: 'tail', output_offset: 17, output_has_more: false }
  ];
  env.setFetch(() => Promise.resolve(response(replies.shift())));
  env.api.initTaskDetailLog(env.document);
  await env.tick();
  assert.equal(panel.dataset.active, '0');
  assert.equal(env.timers.size, 1);
  await env.tick();
  assert.equal(new URL(env.requests[1].url).searchParams.get('since'), '13');
  assert.equal(output.textContent, 'initial\nchunktail');
  assert.equal(env.timers.size, 0);
});
