"use client";

import { useEffect, useState } from "react";

type HealthResponse = {
  status: string;
  service: string;
  gemini_configured: boolean;
};

type ApiState =
  | { kind: "loading" }
  | { kind: "connected"; data: HealthResponse }
  | { kind: "error"; message: string };

export default function Home() {
  const [api, setApi] = useState<ApiState>({ kind: "loading" });
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  useEffect(() => {
    let cancelled = false;

    async function checkHealth() {
      try {
        const res = await fetch(`${apiUrl}/health`);
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as HealthResponse;
        if (!cancelled) {
          setApi({ kind: "connected", data });
        }
      } catch (err) {
        if (!cancelled) {
          setApi({
            kind: "error",
            message: err instanceof Error ? err.message : "Unknown error",
          });
        }
      }
    }

    checkHealth();
    return () => {
      cancelled = true;
    };
  }, [apiUrl]);

  return (
    <div className="flex min-h-full flex-1 items-center justify-center px-6">
      <main className="w-full max-w-md space-y-6 text-center">
        <div className="space-y-2">
          <p className="text-sm tracking-wide text-zinc-500 uppercase">
            Couche 1
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            libretto-printer
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400">
            Next.js + FastAPI scaffold
          </p>
        </div>

        <div className="rounded-lg border border-zinc-200 px-4 py-5 dark:border-zinc-800">
          <p className="mb-2 text-sm text-zinc-500">API status</p>
          {api.kind === "loading" && (
            <p className="text-zinc-700 dark:text-zinc-300">Checking…</p>
          )}
          {api.kind === "connected" && (
            <div className="space-y-1">
              <p className="font-medium text-emerald-700 dark:text-emerald-400">
                Connected
              </p>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                {api.data.service} · {api.data.status}
                {api.data.gemini_configured ? " · Gemini ready" : " · Gemini not configured"}
              </p>
            </div>
          )}
          {api.kind === "error" && (
            <div className="space-y-1">
              <p className="font-medium text-rose-700 dark:text-rose-400">
                Error
              </p>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                Cannot reach {apiUrl}/health ({api.message})
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
