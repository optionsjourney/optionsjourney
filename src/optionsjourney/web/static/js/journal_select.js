// Ticking trades in the journal (#62, #149, #154, as Tracker2's select mode): click a
// row or its box to tick it; Select all / None on the Open and Closed headers and
// Select day on each closing day. Ticks survive a reload (sessionStorage, per page);
// Esc or Clear unticks all. Risk graph opens the ticked open option trades as
// one position; Alert opens one alert on them together (#300, Tracker2's alertState: open,
// one underlying, at most 10); Close closes the ones a bot or their own rules manage.
// Cards | Table | Compact (#155, #156): in Cards a click on a card opens its legs and
// prices, and in Compact a click on a row opens a line under it with them.
(function () {
    "use strict";

    document.querySelectorAll("[data-set-trade-view]").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.documentElement.dataset.tradeView = btn.dataset.setTradeView;
            closeDetails();
            try { localStorage.setItem("journal_trade_view", btn.dataset.setTradeView); } catch (e) { /* storage blocked */ }
        });
    });
    const cards = () => document.documentElement.dataset.tradeView === "cards";
    const compact = () => document.documentElement.dataset.tradeView === "compact";

    // Compact (#156, Tracker2's journal_compact.js): a click pins a row open with a detail
    // line under it holding copies of its legs, closing legs, entry, exit, premium and fees
    // (#282: entry and exit too).
    const DETAIL = [["col-legs", "Legs"], ["col-closing", "Closing legs"], ["col-entry", "Entry"],
        ["col-exit", "Exit"], ["col-premium", "Premium"], ["col-fees", "Fees"]];
    function toggleDetail(row) {
        const next = row.nextElementSibling;
        if (next && next.classList.contains("compact-detail")) {
            next.remove();
            row.classList.remove("compact-open");
            return;
        }
        const body = document.createElement("div");
        body.className = "compact-detail__body";
        DETAIL.forEach(([cls, label]) => {
            const cell = row.querySelector("td." + cls);
            if (!cell || !cell.textContent.trim()) return;
            const section = document.createElement("div");
            const title = document.createElement("div");
            title.className = "muted small";
            title.textContent = label;
            const copy = cell.cloneNode(true);
            copy.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
            const inner = document.createElement("div");
            inner.append(...copy.childNodes);
            section.append(title, inner);
            body.appendChild(section);
        });
        const tr = document.createElement("tr");
        tr.className = "compact-detail";
        const td = document.createElement("td");
        td.colSpan = row.children.length;
        td.appendChild(body);
        tr.appendChild(td);
        row.after(tr);
        row.classList.add("compact-open");
    }
    function closeDetails() {
        document.querySelectorAll("tr.compact-detail").forEach((tr) => tr.remove());
        document.querySelectorAll("tr.compact-open").forEach((tr) => tr.classList.remove("compact-open"));
    }

    const bar = document.getElementById("select-bar");
    if (!bar) return;
    const count = document.getElementById("select-count");
    const analyze = document.getElementById("select-analyze");
    const alertButton = document.getElementById("select-alert");
    const alertBase = alertButton.dataset.managePanel;
    const closeForm = document.getElementById("select-close");
    const closeButton = document.getElementById("select-close-button");
    const MAX_ALERT = 10;  // user_alerts.MAX_TRADES
    const storeKey = "journal-ticked:" + location.pathname;
    const boxes = () => [...document.querySelectorAll(".select-trade")];
    const ticked = () => boxes().filter((b) => b.checked);
    const picked = () => ticked().filter((b) => b.hasAttribute("data-analyzable")).map((b) => b.value);

    function save() {
        try { sessionStorage.setItem(storeKey, JSON.stringify(ticked().map((b) => b.value))); } catch (e) { /* private window */ }
    }

    function restore() {
        let keys = [];
        try { keys = JSON.parse(sessionStorage.getItem(storeKey) || "[]"); } catch (e) { keys = []; }
        const wanted = new Set(keys);
        boxes().forEach((b) => { if (wanted.has(b.value)) b.checked = true; });
    }

    function refresh() {
        const n = ticked().length;
        const graphable = picked().length;
        bar.hidden = n === 0;
        count.textContent = `${n} trade${n === 1 ? "" : "s"} ticked`;
        analyze.disabled = graphable === 0;
        analyze.title = graphable === 0
            ? "Only open option trades can be graphed; none of the ticked ones are."
            : graphable < n ? `Graphs the ${graphable} open option trade${graphable === 1 ? "" : "s"}; the rest can't be graphed.` : "";
        const why = alertProblem(ticked());
        alertButton.disabled = why !== "";
        alertButton.title = why;
        alertButton.dataset.managePanel = alertBase + "?" + ticked().map((b) => "trade=" + encodeURIComponent(b.value)).join("&");
        const closable = ticked().filter((b) => b.hasAttribute("data-closable")).length;
        closeButton.disabled = closable === 0;
        closeButton.title = closable === 0
            ? "None of the ticked trades is managed by a bot or its own exit rules, so none can be closed here."
            : closable < n ? `Closes the ${closable} trade${closable === 1 ? "" : "s"} a bot or its own rules manage; the rest are skipped.` : "";
        document.querySelectorAll(".trade-row").forEach((row) => {
            const box = row.querySelector(".select-trade");
            row.classList.toggle("trade-row--ticked", !!box && box.checked);
        });
        save();
    }

    // Why the ticked trades can't share one alert, or "" (Tracker2's alertState)
    function alertProblem(list) {
        if (list.length > MAX_ALERT) return `An alert covers at most ${MAX_ALERT} trades.`;
        if (list.some((b) => !b.hasAttribute("data-open"))) return "Only open trades can have an alert: untick the closed ones.";
        const tickers = [...new Set(list.map((b) => b.dataset.ticker))].sort();
        if (tickers.length > 1) return `The ticked trades are on ${tickers.join(", ")}: an alert covers one underlying.`;
        return "";
    }

    function setAll(list, on) {
        list.forEach((b) => { b.checked = on; });
        refresh();
    }

    document.addEventListener("change", (e) => {
        if (e.target.classList && e.target.classList.contains("select-trade")) refresh();
    });

    // A click anywhere on a row ticks it, except on its links, buttons and fields
    document.addEventListener("click", (e) => {
        const row = e.target.closest(".trade-row");
        if (row && !e.target.closest("a, button, input, select, textarea, summary, details > :not(summary), label")) {
            if (window.getSelection && String(window.getSelection())) return; // selecting text
            if (cards()) { row.classList.toggle("card-open"); return; }
            if (compact()) { toggleDetail(row); return; }
            const box = row.querySelector(".select-trade");
            if (box) { box.checked = !box.checked; refresh(); }
            return;
        }
        const pick = e.target.closest("[data-select-group]");
        if (pick) {
            const group = pick.dataset.selectGroup;
            const rows = group.startsWith("day-")
                ? document.querySelectorAll(`.trade-row[data-day="${group}"]`)
                : document.querySelectorAll(`.trade-row[data-group="${group}"]`);
            setAll([...rows].map((r) => r.querySelector(".select-trade")).filter(Boolean), !pick.hasAttribute("data-select-none"));
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && ticked().length && !document.querySelector("dialog[open], #manage-drawer:not(.hidden)")) setAll(boxes(), false);
    });

    document.getElementById("select-clear").addEventListener("click", () => setAll(boxes(), false));

    closeForm.addEventListener("submit", () => {
        closeForm.querySelectorAll("input[name=trade]").forEach((i) => i.remove());
        ticked().forEach((b) => {
            const i = document.createElement("input");
            i.type = "hidden"; i.name = "trade"; i.value = b.value;
            closeForm.appendChild(i);
        });
    });

    analyze.addEventListener("click", () => {
        const href = "/risk-graph?trade=" + picked().map(encodeURIComponent).join(",");
        if (window.riskDrawer) window.riskDrawer.open(href);
        else window.location.href = href;
    });

    restore();
    refresh();
})();
