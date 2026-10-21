import { test } from 'node:test';
import assert from 'node:assert/strict';
import { money, plText } from '../../../../src/optionsjourney/web/static/js/open_pl.js';

test('money is signed, with cents', () => {
  assert.equal(money(1234.5), '+$1,234.50');
  assert.equal(money(-3), '-$3.00');
  assert.equal(money(0), '$0.00');
});

test('open P/L and P/L Day show a % of the premium beside them (#213)', () => {
  assert.equal(plText(30, 200), '+$30.00 (+15.0%)');
  assert.equal(plText(-12, 400), '-$12.00 (-3.0%)');
  assert.equal(plText(30, 200, ' Fri'), '+$30.00 (+15.0%) Fri'); // P/L Day of a day before
});

test('no premium: no %; no price: nothing', () => {
  assert.equal(plText(30, 0), '+$30.00');
  assert.equal(plText(null, 200), '');
  assert.equal(plText(undefined, 200), '');
});
