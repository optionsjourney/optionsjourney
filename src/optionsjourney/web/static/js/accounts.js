// The Accounts page (#74) opens with the balances last read; this asks the
// brokers for fresh ones once and swaps them in.
(function () {
    "use strict";

    const page = document.getElementById("accounts");
    if (!page) return;

    async function refresh() {
        try {
            const response = await fetch(page.dataset.liveUrl, { headers: { Accept: "text/html" } });
            if (!response.ok) throw new Error(response.status);
            const fresh = document.createElement("template");
            fresh.innerHTML = await response.text();
            // A tip the person is reading would be torn down by the swap: wait until they let go.
            // A shown tip, as tip.js's TIP_SHOWN (this file is not a module, so it can't import it).
            const shown = ".tip--open, .tip:hover, .tip--hover, .tip:has(:focus-visible)";
            const engaged = () => {
                try { return page.parentElement.querySelector(shown); }
                catch { return page.parentElement.querySelector(".tip--open, .tip:hover, .tip--hover"); }  // no :has()
            };
            while (engaged()) await new Promise((resolve) => setTimeout(resolve, 500));
            for (const id of ["accounts-total", "accounts-cards"]) {
                const replacement = fresh.content.getElementById(id);
                const current = document.getElementById(id);
                if (replacement && current) current.replaceWith(replacement);
            }
        } catch (error) {
            const status = document.querySelector("#accounts-cards > p.muted.small");
            if (status) status.textContent = "Couldn't get fresh balances. Reload the page to try again.";
        }
    }

    refresh();
})();
