// The Stocks tab (#58): asks /api/stocks every few seconds and refreshes each
// row's price, P/L and today's P/L. The numbers are worked out on the server.
(function () {
    "use strict";

    const table = document.getElementById("holdings");
    if (!table) return;
    const EVERY = 5000;
    const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const price = (n) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    const tone = (el, n) => {
        el.classList.toggle("positive", n > 0);
        el.classList.toggle("negative", n < 0);
    };

    async function refresh() {
        try {
            const response = await fetch(table.dataset.url, { headers: { Accept: "application/json" } });
            if (!response.ok) return;
            const data = await response.json();
            for (const row of data.rows) {
                const tr = table.querySelector(`tr[data-ticker="${CSS.escape(row.ticker)}"]`);
                if (!tr) continue;
                const cell = (name) => tr.querySelector(`[data-cell="${name}"]`);
                if (row.price !== null) cell("price").textContent = price(row.price);
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
        } catch (error) {
            // keep the last prices shown
        }
    }

    refresh();
    setInterval(() => {
        if (!document.hidden) refresh();
    }, EVERY);
})();
