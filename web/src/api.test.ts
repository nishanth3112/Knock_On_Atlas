import { ApiError, api, setToken } from "./api";

function reply(status: number, body: unknown) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

test("sends the token and converts keys both ways", async () => {
  const fetch = reply(200, { hub_cik: "1", car_0_1: 0.03, run: { triage_label: "x" } });
  setToken("t0k");
  const data = await api.post<Record<string, unknown>>("/analyses", { hubCik: "1" });
  expect(data).toEqual({ hubCik: "1", car01: 0.03, run: { triageLabel: "x" } });
  const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe("/api/analyses");
  expect(init.body).toBe('{"hub_cik":"1"}');
  expect((init.headers as Record<string, string>).Authorization).toBe("Bearer t0k");
  setToken(null);
});

test("an error carries the API's message and status", async () => {
  reply(409, { detail: "the analysis is already reviewed" });
  const failure = api.post("/reviews/1", { decision: "approved" });
  await expect(failure).rejects.toEqual(new ApiError(409, "the analysis is already reviewed"));
  await expect(failure).rejects.toMatchObject({ status: 409 });
});

test("a validation error gets a plain message", async () => {
  reply(422, { detail: [{ loc: ["body"], msg: "bad" }] });
  await expect(api.get("/events")).rejects.toThrow("The request was not accepted.");
});
