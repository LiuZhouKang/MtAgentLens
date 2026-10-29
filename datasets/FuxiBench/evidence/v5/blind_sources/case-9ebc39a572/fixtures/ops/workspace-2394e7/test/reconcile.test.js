import assert from "node:assert/strict";
import test from "node:test";

import { formatTotals, reconcile } from "../src/index.js";

test("groups amounts by currency", () => {
  const totals = reconcile([
    { id: "a", amount: 12.5, currency: "USD" },
    { id: "b", amount: 7.25, currency: "USD" },
    { id: "c", amount: 3, currency: "EUR" },
  ]);

  assert.deepEqual(totals, { EUR: 3, USD: 19.75 });
});

test("defaults to USD", () => {
  assert.deepEqual(reconcile([{ id: "a", amount: 4 }]), { USD: 4 });
});

test("rejects non-numeric amounts", () => {
  assert.throws(
    () => reconcile([{ id: "a", amount: "not-a-number" }]),
    /invalid amount/,
  );
});

test("formats totals in currency order", () => {
  assert.equal(formatTotals({ USD: 19.75, EUR: 3 }), "EUR 3.00\nUSD 19.75");
});
