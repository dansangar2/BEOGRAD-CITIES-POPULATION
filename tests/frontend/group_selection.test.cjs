/* Exercise the actual group editor script without network or browser packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
  function node() {
    return { children: [], dataset: {}, innerHTML: '',
      setAttribute() {},
      appendChild(child) { this.children.push(child); },
      replaceChildren(fragment) { this.children = fragment.children; },
      querySelectorAll() { return []; } };
  }
  const available = node(), selected = node(), hidden = { value: '' };
  const section = {
    __sectionState: { area_id: 'casablanca', level: 3, children: [], pendingSelected: [
      { id: 'anfa', name: 'Anfa', operation: 'subtract' },
      { id: 'medina', name: 'Medina', operation: 'add' },
    ] },
    __selectedIds: {},
    querySelector(selector) { return selector === '[data-available-badges]' ? available : selected; },
    querySelectorAll() { return [available, selected]; },
  };
  const block = { dataset: { countryCode: 'morocco' }, __legacyNames: [],
    querySelector() { return null; },
    querySelectorAll() { return [section]; } };
  section.__ownerBlock = block;
  const blocks = { querySelectorAll() { return [block]; } };
  const form = { dataset: { sourceUrl: '/groups/source-data/' },
    querySelector(selector) {
      return selector === '[data-group-blocks-json]' ? hidden
        : selector === '[data-group-country-blocks]' ? blocks : null;
    } };
  const document = { addEventListener() {}, querySelector() { return form; }, getElementById() { return null; },
    createElement() { return node(); }, createDocumentFragment() { return node(); } };
  const context = vm.createContext({ document, URL, URLSearchParams,
    window: { location: { href: 'http://local.test/groups/' } },
    fetch() { return Promise.resolve({ ok: true, json: () => Promise.resolve({ children: [
      { id: 'anfa', name: 'Anfa' }, { id: 'medina', name: 'Medina' },
    ] }) }); } });
  const template = fs.readFileSync(path.join(__dirname, '../../ciudades_del_mundo/templates/ciudades_del_mundo/group_form.html'), 'utf8');
  let script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/\{% trans (["'])(.*?)\1 %\}/g, (_, quote, value) => value)
    .replace(/\{\{.*?\}\}/g, 'morocco');
  script = script.replace('  blocksContainer.innerHTML = "";',
    '  globalThis.editor = { ensureSectionChildrenLoaded, selectedItemsForSection, refreshHidden, selectedIdsSnapshot, restoreSelectedIds }; return;');
  vm.runInContext(script, context);
  return { editor: context.editor, section, hidden, selected };
}

test('unopened group sections preserve saved subtraction operations', () => {
  const f = fixture();
  f.editor.refreshHidden();
  const items = JSON.parse(f.hidden.value)[0].sections[0].selected;
  assert.equal(items[0].operation, 'subtract');
  assert.equal(items[1].operation, 'add');
});

test('loading district badges preserves operations and cancelling restores the snapshot', async () => {
  const f = fixture();
  await f.editor.ensureSectionChildrenLoaded(f.section);
  assert.equal(f.section.__selectedIds.anfa, 'subtract');
  assert.equal(f.section.__selectedIds.medina, 'add');
  assert.deepEqual(f.selected.children.flatMap(item => item.children).filter(item => item.dataset.childOperation).map(item => item.value), ['subtract', 'add']);
  const snapshot = f.editor.selectedIdsSnapshot(f.section);
  f.section.__selectedIds.anfa = 'add';
  f.editor.refreshHidden();
  assert.equal(JSON.parse(f.hidden.value)[0].sections[0].selected[0].operation, 'add');
  f.editor.restoreSelectedIds(f.section, snapshot);
  f.editor.refreshHidden();
  assert.equal(JSON.parse(f.hidden.value)[0].sections[0].selected[0].operation, 'subtract');
});
