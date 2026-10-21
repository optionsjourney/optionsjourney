// Tracker-owned: the option chain's earnings marker (chain_panel.js). Like Tasty's
// expiration list, the badge sits on the first expiration whose options are still alive
// for the report's move: any expiry after the report date, or the report day itself when
// it comes before the open. An after-close report on expiration day misses that expiry.
// TastyTrade often leaves time_of_day empty; an unknown time on expiration day counts
// (the conservative read for a short premium seller).

export function earningsExpiration(expirations, earnings) {
  if (!earnings || !earnings.date) return null;
  const amc = earnings.time_of_day === 'AMC';
  return expirations.find(e => e > earnings.date || (e === earnings.date && !amc)) || null;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const TIMES = { BTO: 'before the open', BMO: 'before the open', AMC: 'after the close' };

// "Earnings Oct 29 after the close (est.)" -- the badge's tooltip and the header note.
export function earningsLabel(earnings) {
  if (!earnings || !earnings.date) return '';
  const [, m, d] = earnings.date.split('-').map(Number);
  const when = TIMES[earnings.time_of_day];
  return `Earnings ${MONTHS[m - 1]} ${d}${when ? ` ${when}` : ''}${earnings.estimated ? ' (est.)' : ''}`;
}
