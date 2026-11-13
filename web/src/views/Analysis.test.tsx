import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi, renderWithClient } from "../test-utils";
import { Analysis } from "./Analysis";

const RESULT = {
  hub_name: "Ford Motor Co",
  triage: { label: "MATERIAL_NEGATIVE", method: "rule", probability: null, reason: "item 2.05" },
  plan: {
    hub_cik: "0000037996", as_of: "2025-11-03", queries: [],
    suppliers: [
      { cik: "0000903129", via_cik: "0000037996", depth: 1, weight: 22.0, edge_ids: [1] },
      { cik: "0000099999", via_cik: "0000903129", depth: 2, weight: 2.2, edge_ids: [1, 2] },
    ],
  },
  exposures: [
    { supplier_cik: "0000903129", name: "Gentherm Inc", depth: 1, weight: 22.0, note: "" },
    { supplier_cik: "0000099999", name: "Sable Logistics", depth: 2, weight: 2.2, note: "" },
  ],
  evidence: [
    { supplier_cik: "0000903129", chunk_id: 41, quote: "Ford accounted for 22% of net sales.", url: "https://sec.gov/g" },
  ],
  market: [
    { cik: "0000903129", depth: 1, status: "complete", day0: "2025-11-04", car_0_1: -0.064, car_m1_5: null, t_0_1: -3.02, t_m1_5: null },
    { cik: "0000099999", depth: 2, status: "complete", day0: "2025-11-04", car_0_1: 0.012, car_m1_5: null, t_0_1: 0.4, t_m1_5: null },
  ],
  verification: { ok: true, checks: [], failed_suppliers: [] },
  gate: { route: "HUMAN_REVIEW", reasons: ["Gentherm Inc gets 22% from Ford"] },
  usage: { llm_calls: 6, tool_calls: 14, tokens: 18420 },
  review: { decision: "approved", reviewer: "rev@example.com", note: "matches the 10-K" },
  brief: {
    headline: "Ford plans plant closures",
    claims: [{ text: "Gentherm gets 22% of its revenue from Ford.", supplier_ciks: ["0000903129"], citations: [41] }],
    gaps: [],
  },
};

const run = (status: string, result: object | null) => ({
  id: "run-1", status, input: { hub_cik: "0000037996", accession: "0000037996-25-000238" },
  result, created_at: "2025-11-03T21:20:00Z", completed_at: null, hub_name: "Ford Motor Co",
  event: { accepted_at: "2025-11-03T21:18:00Z", items: ["2.05", "7.01"], url: "https://sec.gov/8k" },
});

test("a published run shows its cited brief, suppliers, returns and history", async () => {
  fakeApi({ "GET /analyses/run-1": [200, run("published", RESULT)] });
  renderWithClient(<Analysis id="run-1" />);
  expect(await screen.findByRole("heading", { name: "Ford plans plant closures" })).toBeInTheDocument();
  expect(screen.getByText(/8-K items 2.05 and 7.01, filed Nov 3, 2025/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Exposure map" })).toHaveAttribute("href", "#map/0000037996?as_of=2025-11-03");

  const gentherm = screen.getAllByText("Gentherm Inc")[0].closest("tr") as HTMLElement;
  expect(within(gentherm).getByText("22.0%")).toBeInTheDocument();
  expect(within(gentherm).getByText("−6.4%")).toBeInTheDocument();
  const sable = screen.getAllByText("Sable Logistics")[0].closest("tr") as HTMLElement;
  expect(within(sable).getByText("indirect, a heuristic")).toBeInTheDocument();
  expect(within(sable).getByText("via Gentherm Inc")).toBeInTheDocument();
  expect(screen.getAllByTestId("car-bar")).toHaveLength(2);

  // A citation jumps to its passage in the evidence list.
  await userEvent.click(screen.getAllByRole("button", { name: "Source 1" })[0]);
  expect(document.getElementById("ev-1")).toHaveClass("flash");
  expect(screen.getByText("“Ford accounted for 22% of net sales.”")).toBeInTheDocument();
  expect(screen.getByText("Reviewer approved")).toBeInTheDocument();
  expect(screen.getByText("18,420")).toBeInTheDocument();
});

test("a paused run says the brief comes after review", async () => {
  const { brief: _brief, review: _review, ...paused } = RESULT;
  fakeApi({ "GET /analyses/run-1": [200, run("paused", { ...paused, reasons: paused.gate.reasons })] });
  renderWithClient(<Analysis id="run-1" />);
  expect(await screen.findByText("The brief is written after a reviewer approves.")).toBeInTheDocument();
  expect(screen.getByText("Waiting for review", { selector: ".pill" })).toBeInTheDocument();
  expect(screen.getByText("Runs after a reviewer approves.")).toBeInTheDocument();
});

test("an ended run says why it stopped", async () => {
  const empty = { triage: RESULT.triage, plan: { ...RESULT.plan, suppliers: [] } };
  fakeApi({ "GET /analyses/run-1": [200, run("ended", empty)] });
  renderWithClient(<Analysis id="run-1" />);
  expect(await screen.findByText(/No reviewed suppliers were linked to Ford Motor Co on Nov 3, 2025/)).toBeInTheDocument();
});

test("an unknown run shows the API's error", async () => {
  fakeApi({ "GET /analyses/nope": [404, { detail: "no such analysis" }] });
  renderWithClient(<Analysis id="nope" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no such analysis");
});
