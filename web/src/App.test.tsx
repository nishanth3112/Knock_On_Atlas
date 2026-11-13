import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, parse } from "./App";
import { fakeApi } from "./test-utils";

const ME = { id: 1, email: "ana@example.com", role: "analyst" };
const signedIn = (live: boolean, extra = {}) => ({
  "POST /auth/login": [200, { access_token: "t0k", token_type: "bearer" }] as [number, unknown],
  "GET /auth/me": [200, ME] as [number, unknown],
  "GET /health": [200, { ok: true, live }] as [number, unknown],
  "GET /events": [200, []] as [number, unknown],
  "GET /analyses?status=paused": [200, []] as [number, unknown],
  ...extra,
});

async function signIn() {
  await userEvent.type(screen.getByLabelText("Email"), "ana@example.com");
  await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  location.hash = "";
});

test("routes come from the hash", () => {
  expect(parse("#analysis/abc")).toEqual({ view: "analysis", id: "abc" });
  expect(parse("#map/0000037996?as_of=2025-11-03")).toEqual({ view: "map", cik: "0000037996", asOf: "2025-11-03" });
  expect(parse("#review")).toEqual({ view: "review" });
  expect(parse("#nonsense")).toEqual({ view: "inbox" });
});

test("signing in opens the inbox with the token on every request", async () => {
  const calls = fakeApi(signedIn(true));
  render(<App />);
  await signIn();
  expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
  expect(screen.getByText(/ana@example.com \(analyst\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New analysis" })).toBeInTheDocument();
  expect(calls[0].body).toEqual({ email: "ana@example.com", password: "correct horse battery" });
  const fetch = globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } };
  const events = fetch.mock.calls.find(([url]) => url === "/api/events");
  expect((events?.[1].headers as Record<string, string>).Authorization).toBe("Bearer t0k");
});

test("a wrong password says so and stays on the login", async () => {
  fakeApi({ "POST /auth/login": [401, { detail: "wrong email or password" }] });
  render(<App />);
  await signIn();
  expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");
});

test("an expired token returns to the login", async () => {
  fakeApi(signedIn(true, { "GET /events": [401, { detail: "invalid or expired token" }] }));
  render(<App />);
  await signIn();
  expect(await screen.findByText("Your session ended. Sign in again.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
});

test("with live runs off, reading works and nothing can be started", async () => {
  fakeApi(signedIn(false));
  render(<App />);
  await signIn();
  expect(await screen.findByText("Live runs are off. Published analyses can still be read.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New analysis" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Inbox" })).toBeInTheDocument();
});

test("a refused action shows the API's reason", async () => {
  const event = {
    accession: "0000037996-25-000238", hub_cik: "0000037996", hub_name: "Ford Motor Co",
    accepted_at: "2025-11-03T21:18:00Z", items: ["2.05"], url: "https://sec.gov/a", run: null,
  };
  fakeApi(signedIn(true, {
    "GET /events": [200, [event]],
    "POST /analyses": [429, { detail: "at most 5 new analyses a day" }],
  }));
  render(<App />);
  await signIn();
  await userEvent.click(await screen.findByRole("button", { name: "Analyze" }));
  expect(await screen.findByText("At most 5 new analyses a day.")).toBeInTheDocument();
});
