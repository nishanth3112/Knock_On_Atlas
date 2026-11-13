import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fakeApi, renderWithClient } from "../test-utils";
import { Inbox } from "./Inbox";

const triage = (label: string) => ({ label, method: "rule", probability: null, reason: `${label} reason` });
const EVENTS = [
  {
    accession: "0000037996-25-000238", hub_cik: "0000037996", hub_name: "Ford Motor Co",
    accepted_at: "2025-11-03T21:18:00Z", items: ["2.05", "7.01"], url: "https://sec.gov/a",
    run: { id: "run-1", status: "paused", triage: triage("MATERIAL_NEGATIVE") },
  },
  {
    accession: "0000104169-25-000100", hub_cik: "0000104169", hub_name: "Walmart Inc.",
    accepted_at: "2025-10-01T12:00:00Z", items: ["5.02"], url: "https://sec.gov/b",
    run: { id: "run-2", status: "ended", triage: triage("ROUTINE") },
  },
  {
    accession: "0000027419-25-000050", hub_cik: "0000027419", hub_name: "Target Corp",
    accepted_at: "2025-09-01T12:00:00Z", items: ["8.01"], url: "https://sec.gov/c", run: null,
  },
];

test("lists filings with triage and run, and filters by triage", async () => {
  fakeApi({ "GET /events": [200, EVENTS] });
  renderWithClient(<Inbox live toast={() => {}} />);
  const ford = (await screen.findByText("Ford Motor Co")).closest("tr") as HTMLElement;
  expect(within(ford).getByText("Material, negative")).toBeInTheDocument();
  expect(within(ford).getByText("Waiting for review")).toBeInTheDocument();
  expect(within(ford).getByText("2.05")).toHaveAttribute("title", "Exit or disposal costs");
  expect(within(ford).getByText("Nov 3, 2025, 4:18 pm")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Routine" }));
  expect(screen.queryByText("Ford Motor Co")).not.toBeInTheDocument();
  expect(screen.getByText("Walmart Inc.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Monitor" }));
  expect(screen.getByText(/No filings match this filter/)).toBeInTheDocument();
});

test("analyze starts a run for the filing's hub and opens it", async () => {
  const calls = fakeApi({
    "GET /events": [200, EVENTS],
    "POST /analyses": [202, { id: "run-3", status: "queued" }],
  });
  const toast = vi.fn();
  renderWithClient(<Inbox live toast={toast} />);
  await userEvent.click(await screen.findByRole("button", { name: "Analyze" }));
  await vi.waitFor(() => expect(location.hash).toBe("#analysis/run-3"));
  expect(calls.find((c) => c.route === "POST /analyses")?.body).toEqual({
    hub_cik: "0000027419",
    accession: "0000027419-25-000050",
  });
  expect(toast).toHaveBeenCalledWith("Analysis queued.");
});

test("with live runs off there is nothing to start", async () => {
  fakeApi({ "GET /events": [200, EVENTS] });
  renderWithClient(<Inbox live={false} toast={() => {}} />);
  expect(await screen.findByText("Not analyzed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Analyze" })).not.toBeInTheDocument();
});
