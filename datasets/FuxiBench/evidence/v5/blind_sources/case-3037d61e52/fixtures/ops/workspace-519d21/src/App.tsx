import { useState } from "react";

import { Button } from "./components/Button";

type StockRow = {
  sku: string;
  name: string;
  quantity: number;
};

const initialRows: StockRow[] = [
  { sku: "SKU-1042", name: "Steel bracket", quantity: 128 },
  { sku: "SKU-2087", name: "Rubber gasket", quantity: 0 },
];

function App() {
  const [rows] = useState<StockRow[]>(initialRows);
  const [filter, setFilter] = useState("");

  console.log("rendering stock table with", rows.length, "rows");

  const visible = rows.filter((row) =>
    row.name.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <main>
      <h1>Inventory dashboard</h1>
      <label>
        Filter
        <input value={filter} onChange={(event) => setFilter(event.target.value)} />
      </label>
      <table>
        <thead>
          <tr>
            <th>SKU</th>
            <th>Name</th>
            <th>Quantity</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={row.sku}>
              <td>{row.sku}</td>
              <td>{row.name}</td>
              <td>{row.quantity}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Button label="Refresh" onClick={() => setFilter("")} />
      <img src="/warehouse-placeholder.svg" width="320" height="160" />
    </main>
  );
}

export default App;
