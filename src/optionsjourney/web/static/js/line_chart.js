// Line charts over time, drawn as SVG: one or more stacked charts on a shared
// date axis, with a crosshair and tooltip that follow the pointer (or the arrow
// keys) across all of them. Used by "P/L over time" and "Account values".
//
// The page describes each chart in HTML:
//   <figure class="chart" data-line-chart="<id of a JSON script>"
//           data-tooltip='[["Label", "key", "signed|money|drawdown|percent"], ...]'>
//     <svg class="chart__plot" data-series="key" data-mark="end|min"
//          data-color="line|loss" data-flows="flow"></svg>   (one per chart)
// The JSON is a list of points, each with "day" (YYYY-MM-DD) and the keys.
// With data-x="trade" on the figure (the bot page's P/L graph, #208) the points sit
// evenly, one per trade, instead of by date: several trades can close on one day. A
// point may then carry a "label" for its tooltip (the $0 "Start" has no day), and a
// key that is null (the start has no trade P/L) is left out of the tooltip.
// The same numbers are always in a table on the page too, so nothing here is
// the only way to read them.
(function () {
    "use strict";

    document.querySelectorAll("figure[data-line-chart]").forEach(setUp);

    function setUp(figure) {
    const source = document.getElementById(figure.dataset.lineChart);
    if (!source) return;
    const rows = JSON.parse(figure.dataset.tooltip || "[]");

    const byTrade = figure.dataset.x === "trade";
    const points = JSON.parse(source.textContent).map((p, i) => {
        const time = p.day ? Date.parse(p.day + "T12:00:00") : NaN;
        return { ...p, time, at: byTrade ? i : time }; // `at`: where it sits along the x axis
    });
    if (!points.length) return;

    const SVG = "http://www.w3.org/2000/svg";
    const MARGIN = { top: 12, right: 64, bottom: 6, left: 56 };
    const AXIS_HEIGHT = 22; // month labels under the last chart
    const plots = Array.from(figure.querySelectorAll(".chart__plot"));
    const tooltip = figure.querySelector(".chart__tooltip");
    const css = getComputedStyle(document.documentElement);
    const color = (name) => css.getPropertyValue(name).trim();

    const money = (value, signed) => {
        const text = "$" + Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
        return (value < 0 ? "-" : signed && value > 0 ? "+" : "") + text;
    };
    const compact = (value) => {
        const size = Math.abs(value);
        const text = size >= 1000 ? (size / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "K" : String(Math.round(size));
        return (value < 0 ? "-" : "") + "$" + text;
    };
    const longDate = (p) => new Date(p.time).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });

    function el(name, attrs, parent) {
        const node = document.createElementNS(SVG, name);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        if (parent) parent.appendChild(node);
        return node;
    }

    // Round tick values: 1, 2 or 5 times a power of ten, about `count` of them.
    function ticks(low, high, count) {
        const span = high - low || 1;
        const raw = span / count;
        const power = Math.pow(10, Math.floor(Math.log10(raw)));
        const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
        const values = [];
        for (let v = Math.floor(low / step) * step; v < Math.ceil(high / step) * step + step / 2; v += step) values.push(Math.round(v * 100) / 100);
        return { values, low: Math.min(low, values[0]), high: Math.max(high, values[values.length - 1]) };
    }

    let charts = [];
    let current = null;

    function draw() {
        charts = [];
        const first = points[0].at;
        const last = points[points.length - 1].at;
        plots.forEach((svg, index) => {
            svg.replaceChildren();
            const series = svg.dataset.series;
            const isLast = index === plots.length - 1;
            const width = svg.clientWidth || 600;
            const height = (svg.classList.contains("chart__plot--short") ? 110 : 200) + (isLast ? AXIS_HEIGHT : 0);
            svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
            svg.setAttribute("height", height);

            const inner = { left: MARGIN.left, right: width - MARGIN.right, top: MARGIN.top, bottom: height - MARGIN.bottom - (isLast ? AXIS_HEIGHT : 0) };
            const values = points.map((p) => p[series]);
            // Lines that never go below zero (an account's value) don't need zero on the axis
            const floor = svg.dataset.zero === "no" ? Math.min(...values) : Math.min(0, ...values);
            const scale = ticks(floor, Math.max(0, ...values), svg.classList.contains("chart__plot--short") ? 3 : 4);
            const x = (at) => (last === first ? (inner.left + inner.right) / 2 : inner.left + ((at - first) / (last - first)) * (inner.right - inner.left));
            const y = (value) => inner.bottom - ((value - scale.low) / (scale.high - scale.low || 1)) * (inner.bottom - inner.top);
            const lineColor = svg.dataset.color === "loss" ? color("--chart-loss") : color("--chart-line");
            const markMin = svg.dataset.mark === "min";

            // Grid and y-axis labels: hairlines, recessive
            for (const value of scale.values) {
                el("line", { x1: inner.left, x2: inner.right, y1: y(value), y2: y(value), stroke: value === 0 ? color("--text-muted") : color("--border"), "stroke-width": 1, "stroke-opacity": value === 0 ? 0.6 : 1 }, svg);
                const label = el("text", { x: inner.left - 8, y: y(value) + 4, "text-anchor": "end", class: "chart__tick" }, svg);
                label.textContent = compact(value);
            }

            // Area wash to the zero line, then the 2px line on top
            const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(p[series]).toFixed(1)}`).join("");
            const zero = y(0).toFixed(1);
            const base = scale.low > 0 ? y(scale.low).toFixed(1) : zero; // fill down to the axis when all values are above zero
            el("path", { d: `${path}L${x(last).toFixed(1)},${base}L${x(first).toFixed(1)},${base}Z`, fill: lineColor, "fill-opacity": svg.dataset.color === "loss" ? 0.18 : 0.1 }, svg);
            el("path", { d: path, fill: "none", stroke: lineColor, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);

            // One direct label: the end of the cumulative line, the bottom of the drawdown
            // Money moved in or out: a small hollow marker on the line
            if (svg.dataset.flows) {
                for (const p of points) {
                    if (!p[svg.dataset.flows]) continue;
                    el("circle", { cx: x(p.at), cy: y(p[series]), r: 4, fill: color("--surface"), stroke: color("--text-muted"), "stroke-width": 2 }, svg);
                }
            }

            const marked = markMin ? points.reduce((a, b) => (b[series] < a[series] ? b : a)) : points[points.length - 1];
            if (!markMin || marked[series] < 0) {
                el("circle", { cx: x(marked.at), cy: y(marked[series]), r: 4, fill: lineColor, stroke: color("--surface"), "stroke-width": 2 }, svg);
                const endLabel = el("text", { x: x(marked.at) + 8, y: y(marked[series]) + 4, class: "chart__value" }, svg);
                endLabel.textContent = money(marked[series], !markMin && svg.dataset.signed !== "no");
                if (x(marked.at) + 8 + 60 > width) {
                    endLabel.setAttribute("x", x(marked.at) - 8);
                    endLabel.setAttribute("text-anchor", "end");
                    endLabel.setAttribute("y", y(marked[series]) + (markMin ? 16 : -8));
                }
            }

            // Along the bottom of the last chart: dates under some of the trades (by trade), else months
            if (isLast && byTrade) {
                const dated = points.filter((p) => p.day);
                const every = Math.max(1, Math.ceil(dated.length / Math.max(1, Math.floor((inner.right - inner.left) / 64))));
                let previous = null;
                dated.forEach((p, i) => {
                    const text = new Date(p.time).toLocaleDateString("en-US", { month: "short", day: "numeric" });
                    if (i % every || text === previous) return;
                    previous = text;
                    const label = el("text", { x: x(p.at), y: inner.bottom + 16, "text-anchor": "middle", class: "chart__tick" }, svg);
                    label.textContent = text;
                });
            } else if (isLast) {
                const start = new Date(first);
                const months = [];
                for (let d = new Date(start.getFullYear(), start.getMonth() + 1, 1); d.getTime() <= last; d.setMonth(d.getMonth() + 1)) months.push(new Date(d));
                const every = Math.max(1, Math.ceil(months.length / Math.max(1, Math.floor((inner.right - inner.left) / 56))));
                months.forEach((d, i) => {
                    if (i % every) return;
                    const label = el("text", { x: x(d.getTime()), y: inner.bottom + 16, "text-anchor": "middle", class: "chart__tick" }, svg);
                    label.textContent = d.getMonth() === 0 ? String(d.getFullYear()) : d.toLocaleDateString("en-US", { month: "short" });
                });
            }

            // Crosshair and focus dot, moved by show()
            const hair = el("line", { y1: inner.top, y2: inner.bottom, stroke: color("--text-muted"), "stroke-width": 1, visibility: "hidden" }, svg);
            const dot = el("circle", { r: 4, fill: lineColor, stroke: color("--surface"), "stroke-width": 2, visibility: "hidden" }, svg);
            charts.push({ svg, series, x, y, inner, hair, dot });
        });
        if (current !== null) show(current);
    }

    function nearest(svg, clientX) {
        const chart = charts.find((c) => c.svg === svg);
        const box = svg.getBoundingClientRect();
        const px = ((clientX - box.left) / box.width) * svg.viewBox.baseVal.width;
        let best = 0;
        points.forEach((p, i) => {
            if (Math.abs(chart.x(p.at) - px) < Math.abs(chart.x(points[best].at) - px)) best = i;
        });
        return best;
    }

    function show(index) {
        current = index;
        const p = points[index];
        for (const c of charts) {
            const cx = c.x(p.at);
            c.hair.setAttribute("x1", cx);
            c.hair.setAttribute("x2", cx);
            c.dot.setAttribute("cx", cx);
            c.dot.setAttribute("cy", c.y(p[c.series]));
            c.hair.setAttribute("visibility", "visible");
            c.dot.setAttribute("visibility", "visible");
        }
        tooltip.replaceChildren();
        const title = document.createElement("div");
        title.className = "chart__tooltip-date";
        title.textContent = p.label || longDate(p);
        tooltip.appendChild(title);
        for (const [label, key, format] of rows) {
            if (format === "flow" && !p[key]) continue;
            if (p[key] === null || p[key] === undefined) continue;
            const value =
                format === "drawdown" ? (p[key] < 0 ? money(p[key]) : "none")
                : format === "money" ? money(p[key], false)
                : format === "percent" ? (p[key] > 0 ? "+" : "") + p[key].toFixed(2) + "%"
                : money(p[key], true);
            const row = document.createElement("div");
            const strong = document.createElement("strong");
            strong.textContent = value;
            const name = document.createElement("span");
            name.className = "muted";
            name.textContent = " " + label;
            row.append(strong, name);
            tooltip.appendChild(row);
        }
        tooltip.hidden = false;
        const box = figure.getBoundingClientRect();
        const first = charts[0];
        const left = (first.x(p.at) / first.svg.viewBox.baseVal.width) * first.svg.getBoundingClientRect().width;
        tooltip.style.left = Math.min(Math.max(left + 12, 0), box.width - tooltip.offsetWidth - 4) + "px";
        if (left + 12 + tooltip.offsetWidth > box.width) tooltip.style.left = Math.max(left - tooltip.offsetWidth - 12, 0) + "px";
    }

    function hide() {
        current = null;
        tooltip.hidden = true;
        for (const c of charts) {
            c.hair.setAttribute("visibility", "hidden");
            c.dot.setAttribute("visibility", "hidden");
        }
    }

    for (const svg of plots) {
        svg.setAttribute("tabindex", "0");
        svg.addEventListener("pointermove", (event) => show(nearest(svg, event.clientX)));
        svg.addEventListener("pointerleave", hide);
        svg.addEventListener("focus", () => show(current ?? points.length - 1));
        svg.addEventListener("blur", hide);
        svg.addEventListener("keydown", (event) => {
            const step = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[event.key];
            if (step === undefined) return;
            event.preventDefault();
            show(Math.min(points.length - 1, Math.max(0, (current ?? points.length - 1) + step)));
        });
    }

    draw();
    new ResizeObserver(() => draw()).observe(figure);
    }
})();
