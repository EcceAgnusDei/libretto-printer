export type ThinkingLevel = "low" | "medium";

export type PublicConfig = {
  max_upload_bytes: number;
  max_pages: number;
  batch_size: number;
  gemini_thinking_level: ThinkingLevel;
};

export const DEFAULT_PUBLIC_CONFIG: PublicConfig = {
  max_upload_bytes: 50 * 1024 * 1024,
  max_pages: 500,
  batch_size: 5,
  gemini_thinking_level: "low",
};

function parseThinkingLevel(value: unknown): ThinkingLevel {
  if (value === "low" || value === "medium") {
    return value;
  }
  if (value === "high") {
    return "medium";
  }
  return DEFAULT_PUBLIC_CONFIG.gemini_thinking_level;
}

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
    gemini_thinking_level: parseThinkingLevel(data.gemini_thinking_level),
  };
}

export type QuotaUsage = {
  batchesRemaining: number | null;
  batchesLimit: number;
  batchesUsed: number;
  batchSize: number;
  nextResetAt: string | null;
  quotaTimezone: string | null;
};

export type QuotaCheck = QuotaUsage & {
  requiresConfirmation: boolean;
  batchesNeeded: number;
  pageCount: number;
  message: string | null;
};

function mapQuotaUsage(data: {
  batches_remaining?: number | null;
  batches_limit?: number;
  batches_used?: number;
  batch_size?: number;
  next_reset_at?: string | null;
  quota_timezone?: string | null;
}): QuotaUsage {
  return {
    batchesRemaining:
      data.batches_remaining === null || data.batches_remaining === undefined
        ? null
        : Number(data.batches_remaining),
    batchesLimit: Number(data.batches_limit ?? 0),
    batchesUsed: Number(data.batches_used ?? 0),
    batchSize: Number(data.batch_size ?? 5),
    nextResetAt:
      typeof data.next_reset_at === "string" ? data.next_reset_at : null,
    quotaTimezone:
      typeof data.quota_timezone === "string" ? data.quota_timezone : null,
  };
}

export async function fetchQuotaUsage(): Promise<QuotaUsage> {
  const res = await fetch(`${apiBase()}/quota`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  return mapQuotaUsage(
    (await res.json()) as {
      batches_remaining?: number | null;
      batches_limit?: number;
      batches_used?: number;
      batch_size?: number;
      next_reset_at?: string | null;
      quota_timezone?: string | null;
    },
  );
}

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
    next_reset_at?: string | null;
    quota_timezone?: string | null;
    message?: string | null;
  };
  return {
    ...mapQuotaUsage(data),
    requiresConfirmation: Boolean(data.requires_confirmation),
    batchesNeeded: Number(data.batches_needed ?? 0),
    pageCount: Number(data.page_count ?? pageCount),
    message: data.message ?? null,
  };
}
