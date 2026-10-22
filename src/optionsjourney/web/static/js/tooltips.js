// Tooltips (#73). A tip shows on hover, keyboard focus or a tap on its ⓘ (tip--open). This
// puts each shown tip on the screen: fixed, beside its ⓘ or button, inside the screen's edges,
// so no scroll box or drawer cuts it off (tip.js placeTip does the sums). A click elsewhere or
// Escape closes an open tip; a scroll or resize moves a shown one with its anchor.
import { placeTip, TIP_SHOWN } from './tip.js';

const CLOSE_DELAY = 250;  // ms a hovered tip stays after the pointer leaves, to reach its link
const shown = new Set();
const timers = new WeakMap();

function isShown(tip) {
    try {
        return tip.matches(TIP_SHOWN);
    } catch {
        return tip.matches('.tip--open, .tip:hover, .tip--hover');
    }
}

function textOf(tip) {
    return tip.querySelector(':scope > .tip__text');
}

function anchorOf(tip) {
    return tip.querySelector(':scope > :not(.tip__text)') || tip;
}

function alignOf(tip) {
    if (tip.classList.contains('tip--end')) return 'end';
    return tip.classList.contains('tip--start') ? 'start' : 'center';
}

function place(tip) {
    const text = textOf(tip);
    if (!text || !tip.isConnected || !isShown(tip)) {
        shown.delete(tip);
        return;
    }
    shown.add(tip);
    // Measure it hidden at 0,0 first, so it never flashes in the wrong place. A transformed
    // ancestor makes "fixed" relative to itself: where 0,0 lands is the offset to take off.
    text.classList.add('tip__text--placed');
    text.style.visibility = 'hidden';
    text.style.left = '0px';
    text.style.top = '0px';
    const box = text.getBoundingClientRect();
    const root = document.documentElement;
    const spot = placeTip(anchorOf(tip).getBoundingClientRect(), { width: box.width, height: box.height },
        { width: root.clientWidth, height: root.clientHeight }, { align: alignOf(tip) });
    text.style.left = `${spot.left - box.left}px`;
    text.style.top = `${spot.top - box.top}px`;
    text.classList.toggle('tip__text--below', spot.below);
    text.style.visibility = '';
}

function unhover(tip) {
    clearTimeout(timers.get(tip));
    tip.classList.remove('tip--hover');
}

function closeAll(except) {
    document.querySelectorAll('.tip--open, .tip--hover').forEach((tip) => {
        if (tip === except) return;
        tip.classList.remove('tip--open');
        unhover(tip);
    });
}

function tipAt(target) {
    return target && target.closest ? target.closest('.tip') : null;
}

// Hover: tip--hover shows it (as :hover does) and stays a moment after the pointer leaves,
// so the pointer can cross the gap to the tip and click its Learn more.
document.addEventListener('mouseover', (event) => {
    const tip = tipAt(event.target);
    if (!tip) return;
    clearTimeout(timers.get(tip));
    if (tip.classList.contains('tip--hover')) return;
    document.querySelectorAll('.tip--hover').forEach((other) => { if (other !== tip) unhover(other); });
    tip.classList.add('tip--hover');
    place(tip);
});
document.addEventListener('mouseout', (event) => {
    const tip = tipAt(event.target);
    if (!tip || tip.contains(event.relatedTarget)) return;
    clearTimeout(timers.get(tip));
    timers.set(tip, setTimeout(() => unhover(tip), CLOSE_DELAY));
});

// Keyboard: a tip shows while its ⓘ or button has keyboard focus (the CSS); place it then.
document.addEventListener('focusin', (event) => {
    const tip = tipAt(event.target);
    if (tip) place(tip);
});

// Tap or click on an ⓘ opens or closes its tip; a click anywhere else closes open ones.
// A tip inside a checkbox's <label> would tick the box when its text is clicked (#233):
// a click on the text, other than on its Learn more, does nothing.
document.addEventListener('click', (event) => {
    const target = event.target.closest ? event.target : event.target.parentElement;
    if (target && target.closest('.tip__text') && !target.closest('a')) {
        event.preventDefault();
        return;
    }
    const icon = target ? target.closest('.tip__icon') : null;
    const tip = icon ? icon.closest('.tip') : null;
    closeAll(tipAt(target));
    if (!tip) return;
    if (tip.classList.toggle('tip--open')) place(tip);
    else unhover(tip);
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeAll(null);
});

// A scroll (of the page or any box inside it) or a resize moves the anchor: move the tip
// with it, or close it once its anchor has left the screen.
let pending = 0;
function follow() {
    pending = 0;
    const root = document.documentElement;
    for (const tip of [...shown]) {
        const a = anchorOf(tip).getBoundingClientRect();
        if (a.bottom < 0 || a.top > root.clientHeight || a.right < 0 || a.left > root.clientWidth) {
            tip.classList.remove('tip--open');
            unhover(tip);
        }
        place(tip);
    }
}
function soon() {
    if (shown.size && !pending) pending = requestAnimationFrame(follow);
}
document.addEventListener('scroll', soon, { capture: true, passive: true });
window.addEventListener('resize', soon);
