// Bot page (#97): the side menu marks the section in view. Its links are
// plain jumps to each section, so the menu works without this too.
(function () {
    const links = Array.from(document.querySelectorAll(".bot-page__link"));
    const sections = links.map((link) => document.getElementById(link.hash.slice(1))).filter(Boolean);
    if (!sections.length || !("IntersectionObserver" in window)) return;

    function mark(id) {
        links.forEach((link) => {
            if (link.hash === "#" + id) link.setAttribute("aria-current", "true");
            else link.removeAttribute("aria-current");
        });
    }

    // A section counts as in view when it crosses a band near the top of the window.
    const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => { if (entry.isIntersecting) mark(entry.target.id); });
    }, { rootMargin: "-15% 0px -75% 0px" });
    sections.forEach((section) => observer.observe(section));
    mark(location.hash.slice(1) || sections[0].id);
})();
