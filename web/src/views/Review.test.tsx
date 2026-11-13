import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "../api";
import { fakeApi, renderWithClient } from "../test-utils";
import { Review } from "./Review";

const ANALYST: User = { id: 1, email: "ana@example.com", role: "analyst" };
const REVIEWER: User = { id: 2, email: "rev@example.com", role: "reviewer" };
const PAUSED = [
  {
    id: "run-1", status: "paused", input: { hub_cik: "0000037996", accession: "0000037996-25-000238" },
    result: { reasons: ["Gentherm Inc gets 22% from Ford"], usage: { llm_calls: 5, tool_calls: 9, tokens: 900 }, evidence: [], exposures: [] },
    created_at: "2025-11-03T21:20:00Z", completed_at: null, hub_name: "Ford Motor Co", event: null,
  },
];
const EDGES = [
  {
    id: 7, status: "pending", supplier: "Pinecrest Paper", hub: "Ford Motor Co", member_label: "Ford",
    pct: 13.2, total_share: false, relationship_flag: null, extraction_method: "jev_pick",
    confidence: 0.82, period_end: "2025-12-31", src_cik: "0000088888",
    source_accession: "0000088888-26-000001", filing_url: "https://www.sec.gov/x/",
  },
];

test("an analyst sees what waits but cannot decide", async () => {
  const calls = fakeApi({ "GET /analyses?status=paused": [200, PAUSED] });
  renderWithClient(<Review user={ANALYST} live toast={() => {}} />);
  expect(await screen.findByText(/Gentherm Inc gets 22% from Ford/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve and publish" })).toBeDisabled();
  expect(screen.getByText(/signed in as an analyst/)).toBeInTheDocument();
  expect(calls.some((c) => c.route === "GET /edges/review")).toBe(false);
});

test("a reviewer approves a brief with a comment", async () => {
  const calls = fakeApi({
    "GET /analyses?status=paused": [200, PAUSED],
    "GET /edges/review": [200, []],
    "POST /reviews/run-1": [202, { id: "run-1", decision: "approved" }],
  });
  const toast = vi.fn();
  renderWithClient(<Review user={REVIEWER} live toast={toast} />);
  await userEvent.type(await screen.findByLabelText("Comment (saved with the run)"), "22% matches");
  await userEvent.click(screen.getByRole("button", { name: "Approve and publish" }));
  await vi.waitFor(() => expect(toast).toHaveBeenCalledWith("Brief approved. The run resumes from its checkpoint and publishes."));
  expect(calls.find((c) => c.route === "POST /reviews/run-1")?.body).toEqual({ decision: "approved", note: "22% matches" });
  // Still paused until the worker resumes it, but it can't be decided twice.
  expect(await screen.findByText("Decision sent. The run is resuming.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve and publish" })).not.toBeInTheDocument();
});

test("rejecting a brief asks first", async () => {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.open = true; };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.open = false; };
  const calls = fakeApi({
    "GET /analyses?status=paused": [200, PAUSED],
    "GET /edges/review": [200, []],
    "POST /reviews/run-1": [202, { id: "run-1", decision: "rejected" }],
  });
  renderWithClient(<Review user={REVIEWER} live toast={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
  expect(calls.some((c) => c.route === "POST /reviews/run-1")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Reject brief" }));
  await vi.waitFor(() =>
    expect(calls.find((c) => c.route === "POST /reviews/run-1")?.body).toEqual({ decision: "rejected", note: "" }),
  );
});

test("a reviewer decides a new relationship", async () => {
  const calls = fakeApi({
    "GET /analyses?status=paused": [200, []],
    "GET /edges/review": [200, EDGES],
    "POST /edges/7/decision": [200, { id: 7, status: "rejected" }],
  });
  const toast = vi.fn();
  renderWithClient(<Review user={REVIEWER} live={false} toast={toast} />);
  expect(await screen.findByRole("heading", { name: "Pinecrest Paper sells to Ford Motor Co" })).toBeInTheDocument();
  expect(screen.getByText("Share of part of revenue")).toBeInTheDocument();
  // Deciding a relationship costs nothing, so it works with live runs off.
  await userEvent.click(screen.getByRole("button", { name: "Reject" }));
  await vi.waitFor(() => expect(toast).toHaveBeenCalledWith("Relationship rejected. It stays out of the graph."));
  expect(calls.find((c) => c.route === "POST /edges/7/decision")?.body).toEqual({ decision: "rejected" });
});
