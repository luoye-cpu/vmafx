// Copyright 2026 Lusoris; SPDX-License-Identifier: EUPL-1.2
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function region(width, scroll, attributes = {}) {
  return {
    clientWidth: width, scrollWidth: scroll, attributes: {...attributes},
    hasAttribute(name) { return Object.hasOwn(this.attributes, name); },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; }
  };
}
const small = region(100, 100);
const wide = region(100, 300);
const native = region(100, 100, {tabindex: '-1'});
const authorWide = region(100, 300, {tabindex: '-1'});
const regions = [small, wide, native, authorWide];
let resize;
let navigation;
const events = {};
const document = {
  readyState: 'complete',
  querySelectorAll(selector) {
    assert.equal(selector, '.md-typeset__scrollwrap', 'leave pre/code/table/sidebar to Material');
    return regions;
  },
  addEventListener(name, callback) { events[name] = callback; }
};
class ResizeObserver {
  constructor(callback) { resize = callback; }
  observe() {}
  disconnect() {}
}
const window = {
  document$: {subscribe(callback) { navigation = callback; }},
  addEventListener(name, callback) { events[name] = callback; }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../docs/javascripts/accessibility.js'), 'utf8'),
  {document, window, ResizeObserver});
assert.equal(small.hasAttribute('tabindex'), false);
assert.equal(wide.attributes.tabindex, '0');
assert.equal(native.attributes.tabindex, '-1');
assert.equal(authorWide.attributes.tabindex, '-1', 'preserve author tabindex on overflowing wrapper');
wide.clientWidth = 400;
authorWide.clientWidth = 400;
resize();
assert.equal(wide.hasAttribute('tabindex'), false, 'remove only our stop after overflow ends');
assert.equal(authorWide.attributes.tabindex, '-1', 'preserve author tabindex after overflow ends');
small.scrollWidth = 250;
events.resize();
assert.equal(small.attributes.tabindex, '0', 'resize adds stop when overflow begins');
small.scrollWidth = 100;
navigation();
assert.equal(small.hasAttribute('tabindex'), false);
assert.equal(native.attributes.tabindex, '-1', 'preserve author-owned tabindex');
console.log('scroll-focus: PASS (non-overflow, overflow, growth, shrink, navigation, author tabindex)');
