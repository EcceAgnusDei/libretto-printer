export type PublicConfig = {
  max_upload_bytes: number;
  max_pages: number;
  batch_size: number;
};

export const DEFAULT_PUBLIC_CONFIG: PublicConfig = {
  max_upload_bytes: 50 * 1024 * 1024,
  max_pages: 500,
  batch_size: 5,
};

function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

export async function fetchPublicConfig(): Promise<PublicConfig> {
  const res = await fetch(`${apiBase()}/config`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = (await res.json()) as Partial<PublicConfig>;
  return {
    max_upload_bytes:
      typeof data.max_upload_bytes === "number"
        ? data.max_upload_bytes
        : DEFAULT_PUBLIC_CONFIG.max_upload_bytes,
    max_pages:
      typeof data.max_pages === "number"
        ? data.max_pages
        : DEFAULT_PUBLIC_CONFIG.max_pages,
    batch_size:
      typeof data.batch_size === "number"
        ? data.batch_size
        : DEFAULT_PUBLIC_CONFIG.batch_size,
  };
}

export type QuotaCheck = {
  requiresConfirmation: boolean;
  batchesNeeded: number;
  batchesRemaining: number | null;
  batchesLimit: number;
  batchesUsed: number;
  batchSize: number;
  pageCount: number;
  message: string | null;
};

export async function fetchQuotaForPages(
  pageCount: number,
): Promise<QuotaCheck> {
  const res = await fetch(
    `${apiBase()}/quota?page_count=${encodeURIComponent(String(pageCount))}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const data = (await res.json()) as {
    requires_confirmation?: boolean;
    batches_needed?: number;
    batches_remaining?: number | null;
    batches_limit?: number;
    batches_used?: number;
    batch_size?: number;
    page_count?: number;
    message?: string | null;
  };
  return {
    requiresConfirmation: Boolean(data.requires_confirmation),
    batchesNeeded: Number(data.batches_needed ?? 0),
    batchesRemaining:
      data.batches_remaining === null || data.batches_remaining === undefined
        ? null
        : Number(data.batches_remaining),
    batchesLimit: Number(data.batches_limit ?? 0),
    batchesUsed: Number(data.batches_used ?? 0),
    batchSize: Number(data.batch_size ?? 5),
    pageCount: Number(data.page_count ?? pageCount),
    message: data.message ?? null,
  };
}
