// Tracker2's pop-out helper (its risk_graph.js, unchanged): the chart pop-out buttons
// call window._popoutUrl. Picture-in-Picture in the Arc browser, else a popup window.
function _popoutUrl(url, windowName, title) {
    if (_isArcBrowser() && 'documentPictureInPicture' in window) {
        _openPip(url, title).catch(err => {
            console.warn('Picture-in-Picture pop-out failed; opening a tab instead', err);
            window.open(url, windowName);
        });
        return;
    }
    const w = window.open(url, windowName, 'popup,width=1280,height=860');
    if (w) w.focus();
}

// Arc sets its theme palette as --arc-palette-* custom properties on every page's root element.
function _isArcBrowser() {
    try {
        const root = getComputedStyle(document.documentElement);
        return ['--arc-palette-title', '--arc-palette-background'].some(v => root.getPropertyValue(v).trim() !== '');
    } catch (err) {
        return false;
    }
}

// The pop-out page runs whole inside an iframe filling the PiP window: its own scripts, live
// quotes and resize handling, exactly as in a normal pop-out window.
async function _openPip(url, title) {
    const pip = await window.documentPictureInPicture.requestWindow({ width: 1200, height: 800 });
    pip.document.title = title;
    pip.document.body.style.cssText = 'margin:0;background:#111827;overflow:hidden;';
    const frame = pip.document.createElement('iframe');
    frame.src = new URL(url, location.href).href;
    frame.style.cssText = 'border:0;display:block;width:100vw;height:100vh;';
    pip.document.body.appendChild(frame);
}
