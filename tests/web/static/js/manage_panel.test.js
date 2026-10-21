import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withoutPanel, panelUrl } from '../../../../src/optionsjourney/web/static/js/manage_panel.js';

test('the journal view drops its panel= when the panel closes', () => {
  assert.equal(withoutPanel('?status=open&panel=tastytrade%3A1%3A10'), '?status=open');
  assert.equal(withoutPanel('?panel=x'), '');
  assert.equal(withoutPanel(''), '');
});

test("the panel is told which journal view to come back to", () => {
  assert.equal(
    panelUrl('/journal/trades/k/panel', '/journal', '?status=open&panel=k'),
    '/journal/trades/k/panel?back=' + encodeURIComponent('/journal?status=open'),
  );
});
