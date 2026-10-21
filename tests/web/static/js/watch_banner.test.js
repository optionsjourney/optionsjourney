import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bannerView, BASE } from '../../../../src/optionsjourney/web/static/js/watch_banner.js';

test('no banner hides it with its whole class', () => {
  assert.deepEqual(bannerView(null), { hidden: true, text: '', className: `${BASE} notice--quiet` });
});

test('each tone sets its own class, never an old one', () => {
  for (const tone of ['danger', 'warning', 'quiet']) {
    const view = bannerView({ tone, text: 'Delta exit fired' });
    assert.equal(view.className, `${BASE} notice--${tone}`);
    assert.equal(view.hidden, false);
    assert.equal(view.text, 'Delta exit fired');
  }
});

test('an unknown tone reads as a warning', () => {
  assert.equal(bannerView({ tone: 'odd', text: 'x' }).className, `${BASE} notice--warning`);
});
