export function reconcile(entries) {
  if (!Array.isArray(entries)) {
    throw new TypeError("entries must be an array");
  }

  return entries.reduce(
    (totals, entry) => {
      const amount = Number(entry.amount);
      if (!Number.isFinite(amount)) {
        throw new TypeError(`invalid amount for ${entry.id}`);
      }
      const key = entry.currency ?? "USD";
      totals[key] = (totals[key] ?? 0) + amount;
      return totals;
    },
    {},
  );
}

export function formatTotals(totals) {
  return Object.entries(totals)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([currency, amount]) => `${currency} ${amount.toFixed(2)}`)
    .join("\n");
}
