// Markup for a tip (the same as the info() macro in templates/_tooltip.html), for
// screens built in JavaScript, and the pure parts of its behaviour: when a tip counts as
// shown, and where an open tip goes on screen. The behaviour itself is js/tooltips.js.

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function tipHtml(id, text, about, { more = null, end = false } = {}) {
  const link = more ? `<a class="tip__more" href="${esc(more)}" target="_blank" rel="noopener">Learn more</a>` : '';
  return `<span class="tip ${end ? 'tip--end' : 'tip--start'}">`
    + `<button type="button" class="tip__icon" aria-label="More about ${esc(about)}" aria-describedby="tip-${esc(id)}">i</button>`
    + `<span class="tip__text" role="tooltip" id="tip-${esc(id)}">${esc(text)}${link}</span></span>`;
}

// A tip is shown while it is open (tapped), hovered (tip--hover is tooltips.js's hover, which
// lasts a moment after the pointer leaves) or holds keyboard focus. The same states as the
// display rule in tooltips.css. Plain focus is not one: an ⓘ clicked closed keeps the focus.
export const TIP_SHOWN = '.tip--open, .tip:hover, .tip--hover, .tip:has(:focus-visible)';
const TIP_SHOWN_OLD = '.tip--open, .tip:hover, .tip--hover';  // for a browser without :has()

// The first shown tip inside root, or null.
export function shownTip(root) {
  if (!root) return null;
  try {
    return root.querySelector(TIP_SHOWN);
  } catch {
    return root.querySelector(TIP_SHOWN_OLD);
  }
}

// True while the user is on a tip inside root. Screens that rebuild their markup on a timer
// or a quote push skip the rebuild then, so the tip isn't torn down.
export function tipEngaged(root) {
  return !!shownTip(root);
}

// Where to put a shown tip's text, in viewport pixels (it is position: fixed). Above the
// anchor when it fits, else below (under the top bar, say); lined up with the anchor's left
// edge (start), right edge (end) or middle (center), then kept `margin` px inside the screen.
// anchor: {left, top, right, bottom}; size: {width, height}; viewport: {width, height}.
export function placeTip(anchor, size, viewport, { align = 'start', gap = 8, margin = 8 } = {}) {
  let left;
  if (align === 'end') left = anchor.right - size.width;
  else if (align === 'center') left = (anchor.left + anchor.right) / 2 - size.width / 2;
  else left = anchor.left;
  left = Math.max(margin, Math.min(left, viewport.width - margin - size.width));

  const above = anchor.top - gap - size.height;
  const below = anchor.bottom + gap;
  let top;
  let isBelow;
  if (above >= margin) {
    [top, isBelow] = [above, false];
  } else if (below + size.height <= viewport.height - margin) {
    [top, isBelow] = [below, true];
  } else {
    // fits neither way: the side with more room, its top kept on screen
    isBelow = viewport.height - anchor.bottom > anchor.top;
    top = isBelow ? below : Math.max(margin, above);
  }
  return { left: Math.round(left), top: Math.round(top), below: isBelow };
}
