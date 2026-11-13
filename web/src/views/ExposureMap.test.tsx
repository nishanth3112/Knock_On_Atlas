import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi, renderWithClient } from "../test-utils";
import { ExposureMap } from "./ExposureMap";

const edge = (id: number, pct: number, accession: string) => ({
  id, pct, member_label: "Ford Member", source_accession: accession, confidence: 0.97,
  period_end: "2025-12-31", valid_from: "2026-02-01", valid_to: null,
  reviewed_at: null, extraction_method: "alias_exact",
});
const GRAPH = {
  hub: { cik: "0000037996", name: "Ford Motor Co", tickers: ["F"] },
  as_of: "2026-03-01",
  suppliers: [
    { supplier_cik: "0000903129", via_cik: "0000037996", weight: 22.0, depth: 1, edge_ids: [1], name: "Gentherm Inc", tickers: ["THRM"], edge: edge(1, 22.0, "0000903129-26-000001") },
    { supplier_cik: "0000099999", via_cik: "0000903129", weight: 2.2, depth: 2, edge_ids: [1, 2], name: "Sable Logistics", tickers: [], edge: edge(2, 10.0, "0000099999-26-000001") },
  ],
};

test("draws each supplier and shows a link's source when picked", async () => {
  fakeApi({ "GET /companies/0000037996/exposure?as_of=2026-03-01": [200, GRAPH] });
  renderWithClient(<ExposureMap cik="0000037996" asOf="2026-03-01" />);
  expect(await screen.findByRole("heading", { name: "Exposure map: Ford Motor Co" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Gentherm Inc, 22.0% of revenue" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Sable Logistics, 2.20% indirect" }));
  expect(screen.getByRole("heading", { name: "Sable Logistics sells to Gentherm Inc" })).toBeInTheDocument();
  expect(screen.getByText("0000099999-26-000001")).toBeInTheDocument();
  expect(screen.getByText("2.20% (22.0% × 10.0%), a heuristic")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /period ending Dec 31, 2025/ })).toHaveAttribute(
    "href", "https://www.sec.gov/Archives/edgar/data/99999/000009999926000001/",
  );

  // One hop hides second-hop suppliers and goes back to the customer's summary.
  await userEvent.click(screen.getByRole("button", { name: "1 hop" }));
  expect(screen.queryByRole("button", { name: /Sable Logistics/ })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Ford Motor Co" })).toBeInTheDocument();
});

test("a customer that isn't a hub shows the API's message", async () => {
  fakeApi({ "GET /companies/0000012345/exposure": [404, { detail: "not a hub customer" }] });
  renderWithClient(<ExposureMap cik="0000012345" asOf={null} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("not a hub customer");
});
