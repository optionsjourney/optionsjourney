import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tipHtml, placeTip, tipEngaged, TIP_SHOWN } from '../../../../src/optionsjourney/web/static/js/tip.js';

test('a tip has the icon, the described-by link and escaped text', () => {
  const html = tipHtml('trade-pop', 'Chance of <any> profit', 'POP');
  assert.ok(html.includes('class="tip tip--start"'));
  assert.ok(html.includes('aria-describedby="tip-trade-pop"'));
  assert.ok(html.includes('aria-label="More about POP"'));
  assert.ok(html.includes('role="tooltip" id="tip-trade-pop"'));
  assert.ok(html.includes('Chance of &lt;any&gt; profit'));
});

test('a Learn more link is added when given', () => {
  const html = tipHtml('a', 'T', 'A', { more: '/help/trade#pop' });
  assert.ok(html.includes('<a class="tip__more" href="/help/trade#pop" target="_blank" rel="noopener">Learn more</a>'));
  assert.ok(!tipHtml('a', 'T', 'A').includes('Learn more'));
});

test('end puts the tip at the right edge', () => {
  assert.ok(tipHtml('a', 'T', 'A', { end: true }).includes('tip tip--end'));
});

// placeTip: anchor and viewport in px; the tip is 200 x 60
const SIZE = { width: 200, height: 60 };
const WIDE = { width: 1280, height: 800 };
const at = (left, top, w = 16, h = 16) => ({ left, top, right: left + w, bottom: top + h });

test('a tip that fits goes above its anchor, lined up with it', () => {
  assert.deepEqual(placeTip(at(300, 400), SIZE, WIDE), { left: 300, top: 400 - 8 - 60, below: false });
  assert.equal(placeTip(at(300, 400), SIZE, WIDE, { align: 'end' }).left, 316 - 200);
  assert.equal(placeTip(at(300, 400), SIZE, WIDE, { align: 'center' }).left, 308 - 100);
});

test('near the top of the screen it opens below instead', () => {
  assert.deepEqual(placeTip(at(300, 30), SIZE, WIDE), { left: 300, top: 46 + 8, below: true });
});

test('it is kept 8px inside the left and right edges', () => {
  assert.equal(placeTip(at(2, 400), SIZE, WIDE, { align: 'center' }).left, 8);
  assert.equal(placeTip(at(1270, 400), SIZE, WIDE).left, 1280 - 8 - 200);
});

test('on a 390px phone a wide tip fills the width less the margins', () => {
  const phone = { width: 390, height: 844 };
  const wide = { width: 374, height: 120 };
  for (const x of [4, 190, 370]) {
    const p = placeTip(at(x, 500), wide, phone);
    assert.equal(p.left, 8);
    assert.ok(p.left + wide.width <= phone.width - 8);
  }
});

test('when it fits neither way it takes the side with more room and stays on screen', () => {
  const short = { width: 390, height: 300 };
  const tall = { width: 200, height: 280 };
  assert.equal(placeTip(at(100, 40), tall, short).below, true);
  const up = placeTip(at(100, 250), tall, short);
  assert.equal(up.below, false);
  assert.equal(up.top, 8);
});

test('a tip focused but not open (an i clicked closed) does not count as engaged', () => {
  assert.ok(!TIP_SHOWN.includes('focus-within'));
  assert.ok(TIP_SHOWN.includes(':focus-visible') && TIP_SHOWN.includes('.tip--open'));
  // a root where only a plain-focused tip would match
  const focusedOnly = { querySelector: s => (s.includes(':focus-within') ? {} : null) };
  assert.equal(tipEngaged(focusedOnly), false);
  // a browser without :has() still finds an open tip
  const noHas = { querySelector: s => { if (s.includes(':has(')) throw new SyntaxError(s); return s.includes('.tip--open') ? {} : null; } };
  assert.equal(tipEngaged(noHas), true);
});
