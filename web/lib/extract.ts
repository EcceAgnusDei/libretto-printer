function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export type ExtractJobStatus =
  | "started"
  | "pass1"
  | "pass2"
  | "done"
  | "error";

export type ExtractStartResult = {
  sessionId: string;
  status: ExtractJobStatus;
  sourceName: string;
  pdfPageCount: number;
};

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .join("; ");
    }
  } catch {
    // ignore
  }
  return `HTTP ${res.status}`;
}

async function assertGeminiReady(): Promise<void> {
  const res = await fetch(`${apiBase()}/health`);
  if (!res.ok) {
    throw new Error(`API indisponible (${res.status})`);
  }
  const data = (await res.json()) as { gemini_configured?: boolean };
  if (!data.gemini_configured) {
    throw new Error("GEMINI_API_KEY non configurée côté API");
  }
}

async function createSession(sourceName: string): Promise<string> {
  const body = new FormData();
  body.set("source_name", sourceName);
  const res = await fetch(`${apiBase()}/ingest/sessions`, {
    method: "POST",
    body,
  });
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as { session_id: string };
  return data.session_id;
}

async function uploadPdf(sessionId: string, file: File): Promise<number> {
  const body = new FormData();
  body.set("file", file, file.name);
  const res = await fetch(`${apiBase()}/ingest/sessions/${sessionId}/pdf`, {
    method: "POST",
    body,
  });
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as { pdf_page_count: number };
  return data.pdf_page_count;
}

async function startExtract(sessionId: string): Promise<ExtractStartResult> {
  const res = await fetch(`${apiBase()}/extract/sessions/${sessionId}/start`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as {
    session_id: string;
    status: ExtractJobStatus;
    source_name: string;
    pdf_page_count: number;
  };
  return {
    sessionId: data.session_id,
    status: data.status,
    sourceName: data.source_name,
    pdfPageCount: data.pdf_page_count,
  };
}

/** Best-effort rollback if triggerExtraction fails mid-flight. */
async function deleteSession(sessionId: string): Promise<void> {
  try {
    await fetch(`${apiBase()}/ingest/sessions/${sessionId}`, {
      method: "DELETE",
    });
  } catch {
    // ignore network errors during cleanup
  }
}

export async function triggerExtraction(file: File): Promise<ExtractStartResult> {
  await assertGeminiReady();
  const sessionId = await createSession(file.name);
  try {
    await uploadPdf(sessionId, file);
    return await startExtract(sessionId);
  } catch (err) {
    await deleteSession(sessionId);
    throw err;
  }
}

export async function fetchExtractStatus(sessionId: string): Promise<{
  status: ExtractJobStatus;
  error: string | null;
}> {
  const res = await fetch(`${apiBase()}/extract/sessions/${sessionId}/status`);
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as {
    status: ExtractJobStatus;
    error: string | null;
  };
  return { status: data.status, error: data.error };
}
