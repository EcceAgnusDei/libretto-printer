export type PublicConfig = {
  max_upload_bytes: number;
  max_pages: number;
  ingest_batch_size: number;
};

export const DEFAULT_PUBLIC_CONFIG: PublicConfig = {
  max_upload_bytes: 50 * 1024 * 1024,
  max_pages: 500,
  ingest_batch_size: 5,
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
    ingest_batch_size:
      typeof data.ingest_batch_size === "number"
        ? data.ingest_batch_size
        : DEFAULT_PUBLIC_CONFIG.ingest_batch_size,
  };
}
