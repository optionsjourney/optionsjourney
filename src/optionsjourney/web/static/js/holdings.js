// The Stocks tab (#58): asks /api/stocks every few seconds and refreshes each
// row's price, market value, P/L and today's P/L, and the three cards above
// (#286). The numbers are worked out on the server.
(function () {
    "use strict";

    // The cards carry the URL on both tabs; the table is only on Holdings
    const summary = document.getElementById("stocks-summary");
    if (!summary || !summary.dataset.url) return;
    const table = document.getElementById("holdings");
    const EVERY = 5000;
    const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const price = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    const tone = (el, n) => {
        el.classList.toggle("positive", n > 0);
        el.classList.toggle("negative", n < 0);
    };
    const percent = (n) => (n === null ? "—" : (n > 0 ? "+" : "") + n.toFixed(2) + "%");

    // A card's figure, and the percent under it (Market value's line stays as is)
    function card(id, value, pct, signed) {
        const box = document.getElementById(id);
        if (!box || value === null) return;
        const figure = box.querySelector("[data-value]");
        figure.textContent = money(value);
        if (signed) tone(figure, value);
        if (pct !== undefined) box.querySelector("[data-line]").textContent = percent(pct);
    }

    async function refresh() {
        try {
            const response = await fetch(summary.dataset.url, { headers: { Accept: "application/json" } });
            if (!response.ok) return;
            const data = await response.json();
            for (const row of table ? data.rows : []) {
                const tr = table.querySelector(`tr[data-ticker="${CSS.escape(row.ticker)}"]`);
                if (!tr) continue;
                const cell = (name) => tr.querySelector(`[data-cell="${name}"]`);
                if (row.price !== null) cell("price").textContent = price(row.price);
                if (row.market_value !== null) cell("value").textContent = money(row.market_value);
                if (row.pl !== null) {
                    const sign = row.pl_percent > 0 ? "+" : "";
                    cell("pl").innerHTML = `${money(row.pl)}<div class="small">${sign}${row.pl_percent.toFixed(2)}%</div>`;
                    tone(cell("pl"), row.pl);
                }
                if (row.today_pl !== null) {
                    cell("today").textContent = money(row.today_pl);
                    tone(cell("today"), row.today_pl);
                }
            }
            const sum = data.summary;
            card("stocks-total-pl", sum.total_pl, sum.total_pl_percent, true);
            card("stocks-market-value", sum.market_value, undefined, false);
            card("stocks-day-pl", sum.day_pl, sum.day_pl_percent, true);
        } catch (error) {
            // keep the last prices shown
        }
    }

    refresh();
    setInterval(() => {
        if (!document.hidden) refresh();
    }, EVERY);
})();
