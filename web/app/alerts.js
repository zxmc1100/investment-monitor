// Alert badge and notice text. Pure.
// The status-bar badge: amber, red when any active alert points down; null when nothing is active.
export function badge(active, down) {
  if (!active) return null;
  return { text: `▲ ${active} ALERT${active === 1 ? "" : "S"}`, cls: down ? "dn" : "am" };
}

// GET /api/alerts → the badge's inputs.
export const summary = (view) => {
  const act = view?.active ?? [];
  return { active: act.length, down: act.some((e) => e.down) };
};

// One notice for a batch of new alerts.
export function noticeText(entries) {
  if (!entries?.length) return null;
  if (entries.length === 1) return `ALERT ${entries[0].id} · ${entries[0].msg}`;
  return `${entries.length} NEW ALERTS · ${entries[0].msg} · ALRT`;
}
