import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  boxHtml,
  highlightSelector,
} from '../../../../src/optionsjourney/web/static/js/demo_tour.js';

const view = (over = {}) => ({
  step: 3,
  total: 7,
  title: 'The stop',
  text: 'Price runs into the stop.',
  link: '/automation/runs/4',
  link_label: 'Open the Run page',
  highlight: null,
  finished: false,
  skipped: false,
  day_over: false,
  ...over,
});

test('the box shows the step number, title, text, link and both buttons', () => {
  const html = boxHtml(view());
  assert.match(html, /Step 3 of 7/);
  assert.match(html, /The stop/);
  assert.match(html, /Price runs into the stop\./);
  assert.match(html, /href="\/automation\/runs\/4"/);
  assert.match(html, /Open the Run page/);
  assert.match(html, /data-tour-next/);
  assert.match(html, /data-tour-skip/);
});

test('the box escapes the text', () => {
  const html = boxHtml(view({ text: 'a <b>bold</b> & "quoted"' }));
  assert.ok(!html.includes('<b>'));
  assert.match(html, /&lt;b&gt;bold&lt;\/b&gt; &amp; &quot;quoted&quot;/);
});

test('the finished box has no step number and no Next', () => {
  const html = boxHtml(view({ step: 8, finished: true, title: "That's the tour" }));
  assert.ok(!html.includes('Step '));
  assert.ok(!html.includes('data-tour-next'));
  assert.match(html, /data-tour-skip/);
});

test('a skipped tour shows nothing', () => {
  assert.equal(boxHtml(view({ skipped: true })), '');
});

test('no link, no link markup', () => {
  assert.ok(!boxHtml(view({ link: null })).includes('<a '));
});

test('step 2 is highlighted on the Automation page', () => {
  const sel = '[data-bot-name="x"] .arm';
  assert.equal(highlightSelector(view({ step: 2, link: '/automation', highlight: sel }), '/automation'), sel);
});

test('steps 1 and 2 are highlighted on any page', () => {
  const sel = 'nav a';
  assert.equal(highlightSelector(view({ step: 1, link: '/automation', highlight: sel }), '/journal'), sel);
  assert.equal(highlightSelector(view({ step: 2, link: '/automation', highlight: sel }), '/automation/1'), sel);
});

test('step 4 is not highlighted on the Charts page', () => {
  assert.equal(
    highlightSelector(view({ step: 4, link: '/trade', highlight: '#trade-ticket' }), '/charts'),
    null
  );
});

test('step 4 is highlighted on its own page', () => {
  assert.equal(
    highlightSelector(view({ step: 4, link: '/trade', highlight: '#trade-ticket' }), '/trade'),
    '#trade-ticket'
  );
});

test('no highlight when the step has none, or the tour is skipped or finished', () => {
  assert.equal(highlightSelector(view(), '/automation'), null);
  assert.equal(highlightSelector(view({ step: 1, highlight: 'a', skipped: true }), '/'), null);
  assert.equal(highlightSelector(view({ step: 8, highlight: 'a', finished: true }), '/'), null);
});

test('only this site\'s paths and https links become anchors', () => {
  for (const link of ['javascript:alert(1)', '//evil.example', 'http://x.example', 'data:text/html,x']) {
    assert.ok(!boxHtml(view({ link })).includes('<a '), link);
  }
  for (const link of ['/automation', 'https://optionsjourney.com/install']) {
    assert.match(boxHtml(view({ link })), new RegExp(`<a href="${link}"`));
  }
});
