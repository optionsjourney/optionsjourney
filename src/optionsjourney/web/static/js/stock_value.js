// The Stocks tab's "Portfolio value" chart (#286). Working it out reads every broker's
// positions and each ticker's closes, so the page loads first and this asks
// /api/stocks/value for the points, puts them in the chart's JSON script and draws it
// with line_chart.js. With no points it shows the note instead; with no points and no
// note (no shares and no stock trades) the whole card is hidden.

/** What to do with the API's answer: hide the card, draw the points, and the note. */
export function plan(data) {
    const note = data.note || "";
    if (!data.points.length) return { hide: !note, draw: false, note };
    return { hide: false, draw: true, note };
}

const figure = typeof document === "undefined" ? null : document.getElementById("stocks-value");
if (figure) {
    const card = figure.closest("section");
    const source = card.querySelector('script[type="application/json"]');
    const note = card.querySelector("[data-note]");

    const say = (text) => {
        note.textContent = text;
        note.hidden = !text;
    };

    figure.hidden = true;
    fetch(figure.dataset.url, { headers: { Accept: "application/json" } })
        .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
        .then((data) => {
            const todo = plan(data);
            if (todo.hide) {
                card.hidden = true;
                return;
            }
            say(todo.note);
            if (!todo.draw) return;
            source.textContent = JSON.stringify(data.points);
            figure.dataset.lineChart = source.id;
            figure.hidden = false;
            window.LineChart.setUp(figure);
        })
        .catch(() => say("Your stock chart couldn't load. It will try again when you reload."));
}
