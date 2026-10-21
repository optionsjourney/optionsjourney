// Settings (#73): show one section at a time, picked in the sidebar and kept
// in the address (/settings#backups), so saving or the back button returns to
// it. The page says which section to start on when a save had a problem.
(function () {
    const root = document.querySelector(".settings");
    if (!root) return;
    const panels = root.querySelectorAll(".settings__panel");
    const known = new Set(Array.from(panels, (panel) => panel.id));

    function show(key) {
        if (!known.has(key)) key = root.dataset.active;
        panels.forEach((panel) => { panel.hidden = panel.id !== key; });
        root.querySelectorAll("[data-section]").forEach((link) => {
            if (link.dataset.section === key) link.setAttribute("aria-current", "page");
            else link.removeAttribute("aria-current");
        });
    }

    root.classList.add("settings--one");
    root.querySelectorAll("[data-section]").forEach((link) => {
        link.addEventListener("click", (event) => {
            event.preventDefault();
            history.replaceState(null, "", "#" + link.dataset.section);
            show(link.dataset.section);
        });
    });
    window.addEventListener("hashchange", () => show(location.hash.slice(1)));
    show(location.hash.slice(1));
})();
