// Symbol linking between grid panes: every pane with `linked: true` shows the same symbol
// (each keeps its own timeframe). One link group per layout.

// The symbol the other linked panes show, for a pane that has just been linked; null if none.
export function linkedSymbolFor(panes, id) {
  return (panes || []).find(p => p.id !== id && p.linked)?.symbol ?? null;
}

// `panes` with every other linked pane set to `symbol`. Unlinked panes are left alone.
export function applyLinkedSymbol(panes, sourceId, symbol) {
  return (panes || []).map(p => (p.id !== sourceId && p.linked && p.symbol !== symbol ? { ...p, symbol } : p));
}
