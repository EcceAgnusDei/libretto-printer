import { getDocument, GlobalWorkerOptions, version } from "pdfjs-dist";

import {
  DEFAULT_PUBLIC_CONFIG,
  type PublicConfig,
} from "./apiConfig";

const THUMB_MAX_EDGE = 512;

let workerReady = false;

function ensureWorker() {
  if (workerReady) return;
  GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${version}/build/pdf.worker.min.mjs`;
  workerReady = true;
}

export function formatMaxSize(bytes: number): string {
  const mo = bytes / (1024 * 1024);
  if (mo >= 1) {
    const rounded = Number.isInteger(mo)
      ? String(mo)
      : mo.toFixed(1).replace(/\.0$/, "");
    return `${rounded} Mo`;
  }
  const ko = bytes / 1024;
  const rounded = Number.isInteger(ko)
    ? String(ko)
    : ko.toFixed(1).replace(/\.0$/, "");
  return `${rounded} Ko`;
}

export type LocalPdfPreview = {
  file: File;
  sourceName: string;
  pageCount: number;
  thumbnailUrl: string;
};

export async function validatePdfLocally(
  file: File,
  limits: PublicConfig = DEFAULT_PUBLIC_CONFIG,
): Promise<LocalPdfPreview> {
  const isPdf =
    file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) {
    throw new Error("Seul le format PDF est accepté pour le moment");
  }
  if (file.size <= 0) {
    throw new Error("Fichier vide");
  }
  if (file.size > limits.max_upload_bytes) {
    throw new Error(
      `Fichier trop volumineux (max ${formatMaxSize(limits.max_upload_bytes)})`,
    );
  }

  ensureWorker();

  const data = new Uint8Array(await file.arrayBuffer());
  const loadingTask = getDocument({ data });
  let pdf;
  try {
    pdf = await loadingTask.promise;
  } catch {
    await loadingTask.destroy();
    throw new Error("PDF illisible");
  }

  try {
    const pageCount = pdf.numPages;
    if (pageCount < 1) {
      throw new Error("PDF sans pages");
    }
    if (pageCount > limits.max_pages) {
      throw new Error(`PDF trop long (max ${limits.max_pages} pages)`);
    }

    const page = await pdf.getPage(1);
    const baseViewport = page.getViewport({ scale: 1 });
    const longest = Math.max(baseViewport.width, baseViewport.height);
    const scale = Math.min(2, THUMB_MAX_EDGE / longest);
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(viewport.width));
    canvas.height = Math.max(1, Math.floor(viewport.height));

    await page.render({ canvas, viewport }).promise;
    const thumbnailUrl = canvas.toDataURL("image/png");

    return {
      file,
      sourceName: file.name,
      pageCount,
      thumbnailUrl,
    };
  } finally {
    await loadingTask.destroy();
  }
}
