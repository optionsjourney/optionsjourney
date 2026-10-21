// Live open P/L on the journal (#24): asks /api/open-pl every few seconds and
// fills in the Open P/L box and each open trade's row. The numbers are
// worked out on the server from the brokers' live prices. Each row's open P/L
// and P/L Day show a % of the trade's premium beside them (#213).

const EVERY = 5000;

export const money = (n) => (n < 0 ? "-" : n > 0 ? "+" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// "+$30.00 (+15.0%)": a P/L and its % of the premium (none without a premium);
// `suffix` follows ("+$30.00 (+15.0%) Fri"). Empty when there's no P/L yet.
export function plText(pl, premium, suffix = "") {
    if (pl === undefined || pl === null) return "";
    const base = Math.abs(Number(premium) || 0);
    const percent = base ? ` (${pl >= 0 ? "+" : ""}${(pl / base * 100).toFixed(1)}%)` : "";
    return `${money(pl)}${percent}${suffix}`;
}

const tone = (el, n) => {
    el.classList.toggle("positive", n > 0);
    el.classList.toggle("negative", n < 0);
};

// The premium of the row a cell sits in
const premiumOf = (cell) => (cell.closest("[data-premium]") || cell.closest("tr")?.querySelector("[data-premium]"))?.dataset.premium || 0;

export function install(doc, win) {
    const box = doc.getElementById("open-pl");
    if (!box) return;
    const value = doc.getElementById("open-pl-value");
    const note = doc.getElementById("open-pl-note");
    const dot = box.querySelector(".live-dot");

    async function refresh() {
        try {
            const response = await win.fetch(box.dataset.url, { headers: { Accept: "application/json" } });
            if (!response.ok) throw new Error(response.status);
            const data = await response.json();
            const streaming = Object.values(data.streams || {}).includes("streaming");
            dot.hidden = !streaming;
            if (data.total === null) {
                value.textContent = data.open ? "—" : "$0.00";
                note.textContent = data.open ? (streaming ? "waiting for prices" : "no live prices: connect a broker") : "no open trades";
            } else {
                value.textContent = money(data.total);
                tone(value, data.total);
                note.textContent = data.priced < data.open ? `${data.priced} of ${data.open} open trades priced` : `${data.open} open trade${data.open === 1 ? "" : "s"}`;
            }
            box.classList.toggle("overview__box--gain", data.total > 0);
            box.classList.toggle("overview__box--loss", data.total < 0);
            // P/L Day (#51): today's change, per trade and in total
            const dayBox = doc.getElementById("pl-day");
            if (dayBox) {
                const dayValue = doc.getElementById("pl-day-value");
                const dayNote = doc.getElementById("pl-day-note");
                const day = data.pl_day_total;
                dayValue.textContent = day === null ? "—" : money(day);
                tone(dayValue, day || 0);
                dayBox.classList.toggle("overview__box--gain", day > 0);
                dayBox.classList.toggle("overview__box--loss", day < 0);
                const when = data.pl_day_label === "today" ? "" : `${data.pl_day_label} · `;
                dayNote.textContent = day === null ? "" : when + (data.pl_day_approximate ? "≈ partly from the closes before" : "since the close before");
            }
            doc.querySelectorAll("[data-pl-day-key]").forEach((cell) => {
                const pl = (data.pl_day || {})[cell.dataset.plDayKey];
                const label = data.pl_day_label === "today" ? "" : ` ${data.pl_day_label}`;
                cell.textContent = plText(pl, premiumOf(cell), label);
                tone(cell, pl || 0);
            });
            // Each open leg's live mark (#61)
            doc.querySelectorAll("[data-mark-key]").forEach((cell) => {
                const mark = (data.marks || {})[cell.dataset.markKey];
                cell.textContent = mark === undefined || mark === null ? "" : `@${mark.toFixed(2)}`;
            });
            doc.querySelectorAll("[data-open-pl-key]").forEach((cell) => {
                const pl = data.positions[cell.dataset.openPlKey];
                cell.textContent = plText(pl, premiumOf(cell));
                tone(cell, pl || 0);
            });
        } catch (error) {
            note.textContent = "prices unavailable";
        }
    }

    refresh();
    win.setInterval(() => {
        if (!doc.hidden) refresh();
    }, EVERY);
}

if (typeof document !== "undefined" && typeof window !== "undefined") install(document, window);
