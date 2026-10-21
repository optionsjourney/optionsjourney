// Copied from Tracker2's analyzer/chain_held.js (#210): the option chain's Pos column, what each
// account already holds at a strike (GET /api/trade/held-legs). Each account gets a fixed color
// (by account id, so it never shifts when a nickname changes); a strike held in several accounts
// shows one pill per account. Pure helpers; chain_panel.js renders them.
// OptionsJourney change: a cell matches by expiration, strike and call/put only. Tracker2 also
// matched futures legs by stream symbol, which this app's journal legs don't carry.

// Mid-tone hues that read on both themes; red and green are left to the sell/buy cells.
export const ACCOUNT_COLORS = ['#3b82f6', '#f59e0b', '#a855f7', '#14b8a6', '#ec4899', '#84cc16', '#f97316', '#06b6d4'];

// {id -> color} over the page's accounts plus any account that holds legs but isn't listed.
export function accountColors(accounts, held) {
  const ids = [...new Set([...(accounts || []).map(a => a.id), ...(held || []).map(h => h.account_id)])]
    .sort((a, b) => a - b);
  return new Map(ids.map((id, i) => [id, ACCOUNT_COLORS[i % ACCOUNT_COLORS.length]]));
}

// filter: null = every account, else an account id.
const shown = (h, filter) => filter == null || h.account_id === filter;

// [{account_id, qty}] held at one chain cell.
export function heldAt(held, { expiration, strike, type }, filter = null) {
  return (held || []).filter(h => shown(h, filter) && h.type === type
    && h.expiration === expiration && h.strike === strike);
}

// {expiration -> [account ids]} with anything held (the dots on the expiration strip).
export function heldExpirations(held, filter = null) {
  const out = new Map();
  for (const h of (held || []).filter(x => shown(x, filter))) {
    const ids = out.get(h.expiration) || [];
    if (!ids.includes(h.account_id)) ids.push(h.account_id);
    out.set(h.expiration, ids.sort((a, b) => a - b));
  }
  return out;
}

// The accounts holding anything on this ticker, in color order, with their labels.
export function heldAccounts(held, accounts, colors) {
  const ids = [...new Set((held || []).map(h => h.account_id))].sort((a, b) => a - b);
  return ids.map(id => {
    const a = (accounts || []).find(x => x.id === id);
    return { id, label: a ? a.label : `Account ${id}`, color: colors.get(id) };
  });
}

export const fmtQty = q => (q > 0 ? `+${q}` : String(q));
