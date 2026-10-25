// "P/L by day / week / month" on the trading metrics page: one bar per period,
// up from zero for a gain and down for a loss. Each bar is its own hover and
// keyboard target with a tooltip. The data comes from the page as JSON
// (#pl-bars-data); the table under the chart has the same story in numbers.
// Over the bars, the cumulative P/L as a line on its own right-hand axis (#301,
// Tracker2's combo chart), with zero at the same height on both axes.
(function () {
    "use strict";

    const figure = document.getElementById("pl-bars");
    const source = document.getElementById("pl-bars-data");
    if (!figure || !source) return;

    const bars = JSON.parse(source.textContent);
    if (!bars.length) return;

    const SVG = "http://www.w3.org/2000/svg";
    const MARGIN = { top: 12, right: 60, bottom: 28, left: 56 };
    const HEIGHT = 220;
    const MAX_BAR = 24; // bars never fill their slot
    const GAP = 2;
    const period = figure.dataset.period;
    const svg = figure.querySelector(".chart__plot");
    const tooltip = figure.querySelector(".chart__tooltip");
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();

    const money = (value) => (value < 0 ? "-" : value > 0 ? "+" : "") + "$" + Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
    const compact = (value) => {
        const size = Math.abs(value);
        const text = size >= 1000 ? (size / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "K" : String(Math.round(size));
        return (value < 0 ? "-" : "") + "$" + text;
    };
    const parse = (iso) => new Date(iso + "T12:00:00");
    const label = (iso) => {
        const d = parse(iso);
        if (period === "month") return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
        const text = d.toLocaleDateString("en-US", { weekday: period === "day" ? "short" : undefined, month: "short", day: "numeric", year: "numeric" });
        return period === "week" ? "Week of " + text : text;
    };

    function el(name, attrs, parent) {
        const node = document.createElementNS(SVG, name);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        if (parent) parent.appendChild(node);
        return node;
    }

    function ticks(low, high, count) {
        const span = high - low || 1;
        const raw = span / count;
        const power = Math.pow(10, Math.floor(Math.log10(raw)));
        const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
        const values = [];
        for (let v = Math.floor(low / step) * step; v < Math.ceil(high / step) * step + step / 2; v += step) values.push(Math.round(v * 100) / 100);
        return { values, low: Math.min(low, values[0]), high: Math.max(high, values[values.length - 1]) };
    }

    // The two axes' ranges stretched so zero sits at the same height on both: zero's
    // place is the average of where each axis would put it on its own.
    function alignZero(a, b) {
        const up = [a.high, b.high], down = [-a.low, -b.low];
        if (!down[0] && !down[1]) return [a, b];
        if (!up[0] && !up[1]) return [a, b];
        const top = (up[0] / (up[0] + down[0] || 1) + up[1] / (up[1] + down[1] || 1)) / 2;
        const fit = (s, i) => {
            const span = Math.max(up[i] / top, down[i] / (1 - top));
            return { values: s.values, low: -(1 - top) * span, high: top * span };
        };
        return [fit(a, 0), fit(b, 1)];
    }

    // A bar with its far end rounded (4px) and square at the zero line
    function barPath(x, width, y0, y1) {
        const r = Math.min(4, width / 2, Math.abs(y1 - y0));
        if (y1 < y0) {
            return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + width - r}Q${x + width},${y1} ${x + width},${y1 + r}V${y0}Z`;
        }
        return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + width - r}Q${x + width},${y1} ${x + width},${y1 - r}V${y0}Z`;
    }

    function showTip(bar, rect) {
        tooltip.replaceChildren();
        const title = document.createElement("div");
        title.className = "chart__tooltip-date";
        title.textContent = label(bar.start);
        tooltip.append(title);
        for (const [amount, text] of [[bar.pl, " realized"], [bar.cumulative, " cumulative"]]) {
            const value = document.createElement("strong");
            value.textContent = money(amount);
            const name = document.createElement("span");
            name.className = "muted";
            name.textContent = text;
            const row = document.createElement("div");
            row.append(value, name);
            tooltip.append(row);
        }
        tooltip.hidden = false;
        const box = figure.getBoundingClientRect();
        const target = rect.getBoundingClientRect();
        let left = target.left - box.left + target.width + 8;
        if (left + tooltip.offsetWidth > box.width) left = target.left - box.left - tooltip.offsetWidth - 8;
        tooltip.style.left = Math.max(0, left) + "px";
    }

    function draw() {
        svg.replaceChildren();
        const width = svg.clientWidth || 600;
        svg.setAttribute("viewBox", `0 0 ${width} ${HEIGHT}`);
        svg.setAttribute("height", HEIGHT);
        const inner = { left: MARGIN.left, right: width - MARGIN.right, top: MARGIN.top, bottom: HEIGHT - MARGIN.bottom };
        const values = bars.map((b) => b.pl);
        const totals = bars.map((b) => b.cumulative);
        const [scale, right] = alignZero(
            ticks(Math.min(0, ...values), Math.max(0, ...values), 4),
            ticks(Math.min(0, ...totals), Math.max(0, ...totals), 4),
        );
        const at = (s) => (value) => inner.bottom - ((value - s.low) / (s.high - s.low || 1)) * (inner.bottom - inner.top);
        const y = at(scale);
        const y2 = at(right);
        for (const value of right.values) {
            if (value < right.low || value > right.high) continue;
            el("text", { x: inner.right + 8, y: y2(value) + 4, "text-anchor": "start", class: "chart__tick", style: `fill: ${color("--chart-cumulative")}` }, svg).textContent = compact(value);
        }

        for (const value of scale.values) {
            if (value < scale.low || value > scale.high) continue;
            el("line", { x1: inner.left, x2: inner.right, y1: y(value), y2: y(value), stroke: value === 0 ? color("--text-muted") : color("--border"), "stroke-width": 1, "stroke-opacity": value === 0 ? 0.6 : 1 }, svg);
            el("text", { x: inner.left - 8, y: y(value) + 4, "text-anchor": "end", class: "chart__tick" }, svg).textContent = compact(value);
        }

        const slot = (inner.right - inner.left) / bars.length;
        const barWidth = Math.max(1, Math.min(MAX_BAR, slot - (slot > 4 ? GAP : 0)));
        const zero = y(0);
        const every = Math.max(1, Math.ceil(bars.length / Math.max(1, Math.floor((inner.right - inner.left) / 64))));
        bars.forEach((bar, i) => {
            const cx = inner.left + slot * (i + 0.5);
            const fill = bar.pl < 0 ? color("--chart-loss") : color("--chart-line");
            const group = el("g", { class: "chart__bar", tabindex: 0, role: "img", "aria-label": `${label(bar.start)}: ${money(bar.pl)}, ${money(bar.cumulative)} cumulative` }, svg);
            // The hit target is the whole slot, not just the painted bar
            el("rect", { x: inner.left + slot * i, y: inner.top, width: slot, height: inner.bottom - inner.top, fill: "transparent" }, group);
            if (bar.pl !== 0) el("path", { d: barPath(cx - barWidth / 2, barWidth, zero, y(bar.pl)), fill }, group);
            const hit = group.firstChild;
            group.addEventListener("pointerenter", () => showTip(bar, hit));
            group.addEventListener("focus", () => showTip(bar, hit));
            group.addEventListener("pointerleave", () => (tooltip.hidden = true));
            group.addEventListener("blur", () => (tooltip.hidden = true));
            if (i % every === 0) {
                const d = parse(bar.start);
                const text = period === "month" ? d.toLocaleDateString("en-US", { month: "short", year: "2-digit" }) : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
                el("text", { x: cx, y: inner.bottom + 18, "text-anchor": "middle", class: "chart__tick" }, svg).textContent = text;
            }
        });
        const points = bars.map((bar, i) => `${inner.left + slot * (i + 0.5)},${y2(bar.cumulative)}`).join(" ");
        el("polyline", { points, fill: "none", stroke: color("--chart-cumulative"), "stroke-width": 2, "stroke-linejoin": "round", "pointer-events": "none" }, svg);
    }

    draw();
    new ResizeObserver(() => draw()).observe(figure);
})();
