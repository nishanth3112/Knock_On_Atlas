import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, setToken, type User } from "./api";

export function Login({ onSignedIn, notice }: { onSignedIn: (user: User) => void; notice: string | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: async () => {
      const { accessToken } = await api.post<{ accessToken: string }>("/auth/login", { email, password });
      setToken(accessToken);
      return api.get<User>("/auth/me");
    },
    onSuccess: onSignedIn,
  });
  return (
    <main className="login">
      <form
        className="panel panel-pad"
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate();
        }}
      >
        <h1>Knock-On Atlas</h1>
        <p className="sub">Sign in to see which suppliers a customer’s 8-K reaches.</p>
        {notice && <p className="body-text">{notice}</p>}
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {login.isError && (
          <p className="error-text" role="alert">
            {login.error.message === "wrong email or password" ? "Wrong email or password." : login.error.message}
          </p>
        )}
        <div className="dlg-actions">
          <button className="btn btn-primary" type="submit" disabled={login.isPending}>
            Sign in
          </button>
        </div>
      </form>
    </main>
  );
}
