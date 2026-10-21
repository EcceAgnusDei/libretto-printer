function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export type ExtractJobStatus = "running" | "paused" | "done" | "error";

export type ExtractWaitReason = "rate_limit" | "daily" | "queue";

export type ExtractWaitSource = "server" | "gemini";

export type ExtractWaitInfo = {
  reason: ExtractWaitReason;
  message: string;
  waitSeconds: number | null;
  waitUntil: string | null;
  source: ExtractWaitSource | null;
};

export type ExtractStartResult = {
  sessionId: string;
  status: ExtractJobStatus;
  sourceName: string;
  pdfPageCount: number;
  renderedCount: number;
  batchSize: number;
  totalBatches: number;
  pagesDone: number;
  currentBatch: number;
  wait: ExtractWaitInfo | null;
};

export type ExtractStatusResult = {
  sessionId: string;
  status: ExtractJobStatus;
  sourceName: string;
  pdfPageCount: number | null;
  batchSize: number | null;
  totalBatches: number | null;
  pagesDone: number | null;
  currentBatch: number | null;
  error: string | null;
  wait: ExtractWaitInfo | null;
  stale: boolean;
};

export function statusToStartResult(
  status: ExtractStatusResult,
): ExtractStartResult {
  return {
    sessionId: status.sessionId,
    status: status.status,
    sourceName: status.sourceName,
    pdfPageCount: status.pdfPageCount ?? 0,
    renderedCount: status.pdfPageCount ?? 0,
    batchSize: status.batchSize ?? 5,
    totalBatches: status.totalBatches ?? 0,
    pagesDone: status.pagesDone ?? 0,
    currentBatch: status.currentBatch ?? 0,
    wait: status.wait,
  };
}

function mapWait(
  wait:
    | {
        reason: ExtractWaitReason;
        message: string;
        wait_seconds?: number | null;
        wait_until?: string | null;
        source?: ExtractWaitSource | null;
      }
    | null
    | undefined,
): ExtractWaitInfo | null {
  if (!wait) return null;
  return {
    reason: wait.reason,
    message: wait.message,
    waitSeconds: wait.wait_seconds ?? null,
    waitUntil: wait.wait_until ?? null,
    source: wait.source ?? null,
  };
}

type ExtractApiPayload = {
  session_id: string;
  status: ExtractJobStatus;
  source_name: string;
  pdf_page_count: number;
  batch_size: number;
  total_batches: number;
  pages_done: number;
  current_batch: number;
  wait?: {
    reason: ExtractWaitReason;
    message: string;
    wait_seconds?: number | null;
    wait_until?: string | null;
    source?: ExtractWaitSource | null;
  } | null;
};

function mapStartLike(data: ExtractApiPayload): ExtractStartResult {
  return {
    sessionId: data.session_id,
    status: data.status,
    sourceName: data.source_name,
    pdfPageCount: data.pdf_page_count,
    renderedCount: data.pdf_page_count,
    batchSize: data.batch_size,
    totalBatches: data.total_batches,
    pagesDone: data.pages_done,
    currentBatch: data.current_batch,
    wait: mapWait(data.wait),
  };
}

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
  return mapStartLike((await res.json()) as ExtractApiPayload);
}

/** Annule l'extraction Gemini et supprime la session côté serveur. */
export async function deleteSession(sessionId: string): Promise<void> {
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

export async function fetchExtractStatus(
  sessionId: string,
): Promise<ExtractStatusResult> {
  const res = await fetch(`${apiBase()}/extract/sessions/${sessionId}/status`);
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as {
    session_id: string;
    status: ExtractJobStatus;
    source_name: string;
    pdf_page_count: number | null;
    batch_size: number | null;
    total_batches: number | null;
    pages_done: number | null;
    current_batch: number | null;
    error: string | null;
    stale?: boolean;
    wait?: {
      reason: ExtractWaitReason;
      message: string;
      wait_seconds?: number | null;
      wait_until?: string | null;
      source?: ExtractWaitSource | null;
    } | null;
  };
  return {
    sessionId: data.session_id,
    status: data.status,
    sourceName: data.source_name,
    pdfPageCount: data.pdf_page_count,
    batchSize: data.batch_size,
    totalBatches: data.total_batches,
    pagesDone: data.pages_done,
    currentBatch: data.current_batch,
    error: data.error,
    wait: mapWait(data.wait),
    stale: Boolean(data.stale),
  };
}

export async function fetchLibretto(sessionId: string): Promise<{
  status: ExtractJobStatus;
  markdown: string;
}> {
  const res = await fetch(
    `${apiBase()}/extract/sessions/${sessionId}/libretto`,
  );
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  const data = (await res.json()) as {
    status: ExtractJobStatus;
    markdown: string;
  };
  return { status: data.status, markdown: data.markdown };
}
