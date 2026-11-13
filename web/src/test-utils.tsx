import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";

type Reply = [number, unknown] | ((body: unknown) => [number, unknown]);

/** A fake API: "GET /events" or "POST /reviews/1" mapped to [status, snake_case body]. */
export function fakeApi(routes: Record<string, Reply>) {
  const calls: { route: string; body: unknown }[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const route = `${init?.method ?? "GET"} ${url.replace(/^\/api/, "")}`;
    const body: unknown = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ route, body });
    const reply = routes[route];
    if (!reply) return new Response(JSON.stringify({ detail: `no fake for ${route}` }), { status: 500 });
    const [status, data] = typeof reply === "function" ? reply(body) : reply;
    return new Response(JSON.stringify(data), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

export function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
