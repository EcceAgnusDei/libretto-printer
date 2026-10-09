"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import {
  DEFAULT_PUBLIC_CONFIG,
  fetchPublicConfig,
  type PublicConfig,
} from "../lib/apiConfig";
import {
  fetchExtractStatus,
  fetchLibretto,
  triggerExtraction,
  type ExtractStartResult,
  type ExtractStatusResult,
  type IngestProgress,
} from "../lib/extract";
import { validatePdfLocally, type LocalPdfPreview } from "../lib/pdfLocal";

type UiPhase = "idle" | "validating" | "extracting";

export default function Home() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [limits, setLimits] = useState<PublicConfig>(DEFAULT_PUBLIC_CONFIG);
  const [phase, setPhase] = useState<UiPhase>("idle");
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<LocalPdfPreview | null>(null);
  const [job, setJob] = useState<ExtractStartResult | null>(null);
  const [liveStatus, setLiveStatus] = useState<ExtractStatusResult | null>(
    null,
  );
  const [librettoMd, setLibrettoMd] = useState<string | null>(null);
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(
    null,
  );

  const busy = phase !== "idle";
  const extractBusy =
    liveStatus?.status === "started" || liveStatus?.status === "running";

  useEffect(() => {
    let cancelled = false;

    async function loadConfig() {
      try {
        const config = await fetchPublicConfig();
        console.info("[config] API /config →", config);
        if (!cancelled) setLimits(config);
      } catch (err) {
        console.warn(
          "[config] fetch /config échoué, fallback defaults",
          err instanceof Error ? err.message : err,
          DEFAULT_PUBLIC_CONFIG,
        );
        if (!cancelled) setLimits(DEFAULT_PUBLIC_CONFIG);
      }
    }

    void loadConfig();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function blockBrowserOpen(event: DragEvent) {
      event.preventDefault();
    }
    window.addEventListener("dragover", blockBrowserOpen);
    window.addEventListener("drop", blockBrowserOpen);
    return () => {
      window.removeEventListener("dragover", blockBrowserOpen);
      window.removeEventListener("drop", blockBrowserOpen);
    };
  }, []);

  useEffect(() => {
    if (!job) return;
    const terminal =
      liveStatus?.status === "done" || liveStatus?.status === "error";
    if (terminal) return;

    let cancelled = false;

    async function tick() {
      try {
        const status = await fetchExtractStatus(job!.sessionId);
        if (cancelled) return;
        setLiveStatus(status);
        if (status.status === "done") {
          const lib = await fetchLibretto(job!.sessionId);
          if (!cancelled) setLibrettoMd(lib.markdown);
        }
        if (status.status === "error") {
          setError(status.error ?? "Extraction échouée");
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Erreur de suivi extraction",
          );
        }
      }
    }

    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [job, liveStatus?.status]);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0 || busy) return;
    setError(null);
    setJob(null);
    setLiveStatus(null);
    setLibrettoMd(null);
    setIngestProgress(null);
    setPhase("validating");

    try {
      const files = Array.from(fileList);
      if (files.length !== 1) {
        throw new Error("Veuillez envoyer un seul fichier PDF");
      }

      const preview = await validatePdfLocally(files[0], limits);
      setLoaded(preview);
    } catch (err) {
      setLoaded(null);
      setError(err instanceof Error ? err.message : "Erreur de vérification");
    } finally {
      setPhase("idle");
    }
  }

  async function onExtract() {
    if (!loaded || busy) return;
    setError(null);
    setJob(null);
    setLiveStatus(null);
    setLibrettoMd(null);
    setIngestProgress(null);
    setPhase("extracting");

    try {
      const result = await triggerExtraction(loaded.file, setIngestProgress);
      setJob(result);
      setLiveStatus({
        sessionId: result.sessionId,
        status: result.status,
        sourceName: result.sourceName,
        pdfPageCount: result.pdfPageCount,
        extractBatchSize: result.extractBatchSize,
        totalBatches: result.totalBatches,
        pagesDone: result.pagesDone,
        currentBatch: result.currentBatch,
        error: null,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec du déclenchement");
    } finally {
      setPhase("idle");
      setIngestProgress(null);
    }
  }

  function onDragEnter(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!busy) setDragOver(true);
  }

  function onDragLeave(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget === event.target) {
      setDragOver(false);
    }
  }

  function onDragOver(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
  }

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    setDragOver(false);
    if (busy) return;
    void handleFiles(event.dataTransfer.files);
  }

  function extractingLabel(): string {
    if (!ingestProgress) return "Envoi et rendu…";
    if (ingestProgress.phase === "upload") return "Upload du PDF…";
    if (ingestProgress.phase === "render") {
      const { renderedCount, pdfPageCount } = ingestProgress;
      return `Rendu des pages… ${renderedCount}/${pdfPageCount}`;
    }
    return "Démarrage de l'extraction…";
  }

  const status = liveStatus ?? null;
  const pagesDone = status?.pagesDone ?? job?.pagesDone ?? 0;
  const pdfPageCount = status?.pdfPageCount ?? job?.pdfPageCount ?? 0;
  const currentBatch = status?.currentBatch ?? job?.currentBatch ?? 0;
  const totalBatches = status?.totalBatches ?? job?.totalBatches ?? 0;

  return (
    <div className="flex min-h-full flex-1 justify-center px-6 py-10">
      <main className="w-full max-w-3xl space-y-8">
        <header className="space-y-2">
          <p className="text-sm tracking-wide text-zinc-500 uppercase">
            Extraction libretto — passe unique
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            libretto-printer
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400">
            Upload PDF, rendu des pages, puis extraction Gemini en une passe
            (lots de 5, contexte section Markdown).
          </p>
        </header>

        <div
          role="button"
          tabIndex={0}
          onClick={() => {
            if (!busy) fileInputRef.current?.click();
          }}
          onKeyDown={(event) => {
            if (!busy && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              fileInputRef.current?.click();
            }
          }}
          onDragEnter={onDragEnter}
          onDragLeave={onDragLeave}
          onDragOver={onDragOver}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center transition ${
            dragOver
              ? "border-zinc-900 bg-zinc-100 dark:border-zinc-100 dark:bg-zinc-900"
              : "border-zinc-300 hover:border-zinc-500 dark:border-zinc-700 dark:hover:border-zinc-400"
          } ${busy ? "pointer-events-none opacity-60" : ""}`}
        >
          <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
            {phase === "validating"
              ? "Vérification du PDF…"
              : phase === "extracting"
                ? extractingLabel()
                : "Déposez un PDF, ou cliquez pour choisir"}
          </span>
          <span className="text-xs text-zinc-500">PDF uniquement</span>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept="application/pdf,.pdf"
            disabled={busy}
            onChange={(e) => {
              void handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {error && (
          <p className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
            {error}
          </p>
        )}

        {loaded && (
          <section className="flex flex-col items-start gap-4 sm:flex-row sm:items-end">
            <div className="relative h-52 w-40 overflow-hidden rounded-lg border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950">
              <Image
                src={loaded.thumbnailUrl}
                alt={`Miniature de ${loaded.sourceName}`}
                fill
                unoptimized
                className="object-contain"
                sizes="160px"
              />
            </div>
            <div className="space-y-3">
              <div className="space-y-1">
                <h2 className="flex items-center gap-1.5 text-lg font-bold text-emerald-600 dark:text-emerald-400">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    className="size-5 shrink-0"
                    aria-hidden="true"
                  >
                    <path
                      fillRule="evenodd"
                      d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
                      clipRule="evenodd"
                    />
                  </svg>
                  PDF valide
                </h2>
                <p className="text-sm text-zinc-600 dark:text-zinc-400">
                  {loaded.sourceName}
                </p>
                <p className="text-sm text-zinc-500">
                  {loaded.pageCount} page{loaded.pageCount > 1 ? "s" : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void onExtract()}
                disabled={busy || extractBusy}
                className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                {phase === "extracting"
                  ? extractingLabel()
                  : extractBusy
                    ? "Extraction en cours…"
                    : "Extraire le libretto"}
              </button>
            </div>
          </section>
        )}

        {job && status && (
          <section className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
            <p className="font-medium">
              {status.status === "done"
                ? "Extraction terminée"
                : status.status === "error"
                  ? "Extraction en erreur"
                  : "Extraction en cours"}
            </p>
            <p className="mt-1 font-mono text-xs break-all">
              session: {job.sessionId}
            </p>
            <p className="mt-1">
              status: <span className="font-mono">{status.status}</span> · lot{" "}
              {currentBatch}/{totalBatches} · pages {pagesDone}/{pdfPageCount}
            </p>
          </section>
        )}

        {librettoMd !== null && (
          <section className="space-y-2">
            <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
              libretto.md
            </h2>
            <pre className="max-h-[28rem] overflow-auto rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-xs leading-relaxed whitespace-pre-wrap text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-200">
              {librettoMd || "(vide)"}
            </pre>
          </section>
        )}
      </main>
    </div>
  );
}
