// Tip text for the Trade page's option chain, the ticket's stats strip and its Time in force
// choice. Rendered with tip.js's tipHtml; ids are 'trade-<key>'. Checked against chain_stats.js
// (the strip's maths), ticket_core.js reviewBp (BP Eff., BP After, Fees), chain_held.js (Pos)
// and trade_ticket.js / trading/check.py (GTC overnight).
export const TRADE_TIPS = {
  oi: "The open interest your data feed reports for this strike: how many contracts are open. The column is hidden when the feed gives none for this expiration.",
  pos: "What you already hold at this strike, from your open trades in the Journal. A minus number is contracts sold, a plus number contracts bought. Each account has its own colour.",
  delta: "The delta your data feed gives for this option. In the ticket strip, Delta adds up your legs.",
  spread: "Ask minus bid, in dollars and as a share of the mid price. The cell turns amber over 10% and red over 25%, a warning that you may not fill near the mid.",
  bid: "Click to sell this option at the bid. The leg goes into the ticket as a sell; click it again to take it out.",
  ask: "Click to buy this option at the ask. The leg goes into the ticket as a buy; click it again to take it out.",
  stripDelta: "The ticket's total delta in dollars per $1 move, from each leg's delta times its quantity and the contract size. Sold legs subtract. Shows a dash if any leg has no delta.",
  pop: "Chance the trade finishes above zero at expiration. The app takes the sold legs' implied volatility from their mid prices and works out how likely the price is to end in the profitable range. It is a model, not a promise.",
  p50: "Chance the trade reaches half its max profit at any point before expiration. The app runs 2,000 simulated price paths and re-prices the legs along each one. A model, so expect a few points of difference from other platforms.",
  maxProfit: "The most the trade can make at expiration, worked out from the legs at their mid prices. 'Unlimited' when a long call has no cap. A dash means the ticket spans more than one expiration.",
  maxLoss: "The most the trade can lose at expiration, worked out from the legs at their mid prices. 'Unlimited' when a short call has no cap. A dash means the ticket spans more than one expiration.",
  bpEff: "The buying power this trade uses, as your broker works it out. It fills in after you press Review order and goes back to a dash when you change the ticket. A dash can also mean the broker gave no figure (Schwab doesn't).",
  bpAfter: "The buying power left once the order is placed, from your broker's answer to Review order. A dash means you haven't reviewed this ticket yet, or the broker gave no figure.",
  fees: "Commission plus fees, from your broker's answer to Review order. A dash means you haven't reviewed this ticket yet, or the broker gave no figure.",
  gtc: "How long the order lasts. Day ends at the close. GTC (good till cancelled) stays until it fills or you cancel it.",
  gtcOvernight: "GTC overnight also works during the Cboe index options' overnight hours. It is offered only for index options such as SPX on a TastyTrade account, and only with a limit price.",
};

// Guide sections for "Learn more" (docs/guide/trade.md).
export const TRADE_MORE = {
  pos: '/help/trade#what-you-already-hold',
  bpEff: '/help/trade#review-order',
  bpAfter: '/help/trade#review-order',
  fees: '/help/trade#review-order',
};
