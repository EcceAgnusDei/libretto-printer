"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import {
  DEFAULT_PUBLIC_CONFIG,
  fetchPublicConfig,
  fetchQuotaForPages,
  type PublicConfig,
  type QuotaCheck,
} from "../lib/apiConfig";
import {
  deleteSession,
  fetchExtractStatus,
  fetchLibretto,
  resumeExtraction,
  statusToStartResult,
  triggerExtraction,
  type ExtractStartResult,
  type ExtractStatusResult,
} from "../lib/extract";
import { validatePdfLocally, type LocalPdfPreview } from "../lib/pdfLocal";
import {
  loadStoredSessions,
  mergeTrackedById,
  readSessionIdFromUrl,
  removeStoredSession,
  saveStoredSessions,
  shouldApplyRestoredFocus,
  upsertStoredSession,
  writeSessionIdToUrl,
  type StoredSession,
} from "../lib/sessionStore";

type UiPhase = "idle" | "validating" | "extracting";

function rememberFromStatus(status: ExtractStatusResult): void {
  upsertStoredSession({
    sessionId: status.sessionId,
    sourceName: status.sourceName,
    pdfPageCount: status.pdfPageCount ?? 0,
    updatedAt: new Date().toISOString(),
  });
}

function rememberFromStart(result: ExtractStartResult): void {
  upsertStoredSession({
    sessionId: result.sessionId,
    sourceName: result.sourceName,
    pdfPageCount: result.pdfPageCount,
    updatedAt: new Date().toISOString(),
  });
}

function startToStatus(result: ExtractStartResult): ExtractStatusResult {
  return {
    sessionId: result.sessionId,
    status: result.status,
    sourceName: result.sourceName,
    pdfPageCount: result.pdfPageCount,
    batchSize: result.batchSize,
    totalBatches: result.totalBatches,
    pagesDone: result.pagesDone,
    currentBatch: result.currentBatch,
    error: null,
    wait: result.wait,
    stale: false,
  };
}

function isDailyWaitStatus(status: ExtractStatusResult): boolean {
  return (
    status.status === "paused" ||
    status.wait?.reason === "daily" ||
    status.error === "dépassement des quota d'utilisation, réessayez demain"
  );
}

function statusHeadline(status: ExtractStatusResult): string {
  if (status.status === "done") return "Extraction terminée";
  if (isDailyWaitStatus(status)) {
    return "Quota journalier atteint — l'extraction reprendra demain";
  }
  if (status.stale) {
    return "Extraction interrompue — reprise automatique";
  }
  if (status.wait?.reason === "queue") {
    return status.wait.message || "mise en file d'attente";
  }
  if (status.wait?.reason === "rate_limit") {
    return "Extraction ralentie — forte affluence";
  }
  if (status.status === "error") return "Extraction en erreur";
  return "Extraction en cours";
}

function statusToneClass(status: ExtractStatusResult): string {
  if (isDailyWaitStatus(status) || status.stale || status.status === "error") {
    return "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-200";
  }
  if (
    status.wait?.reason === "rate_limit" ||
    status.wait?.reason === "queue"
  ) {
    return "border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100";
  }
  return "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200";
}

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
  const [tracked, setTracked] = useState<ExtractStatusResult[]>([]);
  const [librettoMd, setLibrettoMd] = useState<string | null>(null);
  const [quotaConfirm, setQuotaConfirm] = useState<QuotaCheck | null>(null);
  const [restoring, setRestoring] = useState(true);
  const userStartedDuringRestoreRef = useRef(false);
  const librettoSyncRef = useRef<{
    sessionId: string;
    pagesDone: number;
  } | null>(null);

  const busy = phase !== "idle" || restoring;
  const extractBusy =
    liveStatus?.status === "running" && liveStatus.stale !== true;

  async function syncLibretto(
    sessionId: string,
    pagesDone: number | null,
    options?: { silent?: boolean; cancelled?: () => boolean },
  ): Promise<void> {
    try {
      const lib = await fetchLibretto(sessionId);
      if (options?.cancelled?.()) return;
      setLibrettoMd(lib.markdown);
      librettoSyncRef.current = {
        sessionId,
        pagesDone: pagesDone ?? 0,
      };
    } catch (err) {
      if (options?.silent) return;
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le libretto",
      );
    }
  }

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
    let cancelled = false;
    userStartedDuringRestoreRef.current = false;

    async function restoreSessions() {
      const stored = loadStoredSessions();
      const fromUrl = readSessionIdFromUrl();
      const ids = [
        ...new Set([
          ...(fromUrl ? [fromUrl] : []),
          ...stored.map((entry) => entry.sessionId),
        ]),
      ];

      if (ids.length === 0) {
        if (!cancelled) setRestoring(false);
        return;
      }

      const results: ExtractStatusResult[] = [];
      const keep: StoredSession[] = [];

      await Promise.all(
        ids.map(async (sessionId) => {
          try {
            const status = await fetchExtractStatus(sessionId);
            if (cancelled) return;
            results.push(status);
            const previous = stored.find(
              (entry) => entry.sessionId === sessionId,
            );
            keep.push({
              sessionId,
              sourceName:
                status.sourceName || previous?.sourceName || sessionId,
              pdfPageCount: status.pdfPageCount ?? previous?.pdfPageCount ?? 0,
              updatedAt: new Date().toISOString(),
            });
          } catch {
            /* 404 ou session invalide → ignorée */
          }
        }),
      );

      if (cancelled) return;

      const applyFocus = shouldApplyRestoredFocus(
        userStartedDuringRestoreRef.current,
      );

      if (applyFocus) {
        saveStoredSessions(keep);
        setTracked(results);

        const preferred =
          results.find((item) => item.sessionId === fromUrl) ??
          results.find((item) => item.status === "running" || item.stale) ??
          results.find((item) => item.status === "paused") ??
          null;

        if (preferred) {
          setJob(statusToStartResult(preferred));
          setLiveStatus(preferred);
          writeSessionIdToUrl(preferred.sessionId);
          await syncLibretto(preferred.sessionId, preferred.pagesDone, {
            silent: true,
            cancelled: () =>
              cancelled ||
              !shouldApplyRestoredFocus(userStartedDuringRestoreRef.current),
          });
        }
      } else {
        for (const entry of keep) {
          upsertStoredSession(entry);
        }
        setTracked((prev) => mergeTrackedById(results, prev));
      }

      setRestoring(false);
    }

    void restoreSessions();
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

  const pollSessionIds = [
    ...new Set([
      ...tracked
        .filter(
          (item) =>
            item.status === "running" ||
            item.status === "paused" ||
            item.stale,
        )
        .map((item) => item.sessionId),
      ...(job &&
      liveStatus &&
      (liveStatus.status === "running" ||
        liveStatus.status === "paused" ||
        liveStatus.stale)
        ? [job.sessionId]
        : []),
    ]),
  ]
    .sort()
    .join(",");

  useEffect(() => {
    if (!pollSessionIds) return;
    const ids = pollSessionIds.split(",");
    let cancelled = false;

    async function tick() {
      const updates = await Promise.all(
        ids.map(async (sessionId) => {
          try {
            return await fetchExtractStatus(sessionId);
          } catch {
            return null;
          }
        }),
      );
      if (cancelled) return;

      const byId = new Map<string, ExtractStatusResult>();
      for (const update of updates) {
        if (update) byId.set(update.sessionId, update);
      }

      setTracked((prev) =>
        prev.map((item) => byId.get(item.sessionId) ?? item),
      );

      const selectedId = job?.sessionId;
      if (!selectedId) return;
      const selected = byId.get(selectedId);
      if (!selected) return;

      setLiveStatus(selected);
      setJob(statusToStartResult(selected));
      rememberFromStatus(selected);

      const pagesDone = selected.pagesDone ?? 0;
      const sync = librettoSyncRef.current;
      if (sync?.sessionId !== selectedId || sync.pagesDone !== pagesDone) {
        await syncLibretto(selectedId, pagesDone, {
          silent: true,
          cancelled: () => cancelled,
        });
      }
      if (selected.status === "error") {
        const isDaily =
          selected.wait?.reason === "daily" ||
          selected.error ===
            "dépassement des quota d'utilisation, réessayez demain";
        if (!isDaily) {
          setError(selected.error ?? "Extraction échouée");
        }
      }
    }

    void tick();
    const id = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [pollSessionIds, job?.sessionId]);

  function applyJobSelection(status: ExtractStatusResult): void {
    setError(null);
    setJob(statusToStartResult(status));
    setLiveStatus(status);
    writeSessionIdToUrl(status.sessionId);
    rememberFromStatus(status);
  }

  async function selectTracked(status: ExtractStatusResult): Promise<void> {
    applyJobSelection(status);
    setLibrettoMd(null);
    librettoSyncRef.current = null;
    await syncLibretto(status.sessionId, status.pagesDone);
  }

  function dismissTracked(sessionId: string): void {
    removeStoredSession(sessionId);
    setTracked((prev) => prev.filter((item) => item.sessionId !== sessionId));
    if (job?.sessionId === sessionId) {
      setJob(null);
      setLiveStatus(null);
      setLibrettoMd(null);
      librettoSyncRef.current = null;
      setLoaded(null);
      setQuotaConfirm(null);
      writeSessionIdToUrl(null);
    }
    void deleteSession(sessionId);
  }

  async function retryTracked(status: ExtractStatusResult): Promise<void> {
    if (busy || extractBusy) return;
    setError(null);
    setPhase("extracting");
    try {
      const result = await resumeExtraction(status.sessionId);
      const asStatus = startToStatus(result);
      rememberFromStart(result);
      setTracked((prev) => [
        asStatus,
        ...prev.filter((item) => item.sessionId !== result.sessionId),
      ]);
      setJob(result);
      setLiveStatus(asStatus);
      writeSessionIdToUrl(result.sessionId);
      await syncLibretto(result.sessionId, result.pagesDone, { silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec de la reprise");
    } finally {
      setPhase("idle");
    }
  }

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0 || busy) return;
    setError(null);
    setJob(null);
    setLiveStatus(null);
    setLibrettoMd(null);
    librettoSyncRef.current = null;
    writeSessionIdToUrl(null);
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

  async function runExtraction() {
    if (!loaded || restoring) return;
    userStartedDuringRestoreRef.current = true;
    setError(null);
    setJob(null);
    setLiveStatus(null);
    setLibrettoMd(null);
    librettoSyncRef.current = null;
    setQuotaConfirm(null);
    setPhase("extracting");

    try {
      const result = await triggerExtraction(loaded.file);
      const asStatus = startToStatus(result);
      rememberFromStart(result);
      setTracked((prev) => [
        asStatus,
        ...prev.filter((item) => item.sessionId !== result.sessionId),
      ]);
      setJob(result);
      setLiveStatus(asStatus);
      writeSessionIdToUrl(result.sessionId);
      setLibrettoMd("");
      librettoSyncRef.current = {
        sessionId: result.sessionId,
        pagesDone: result.pagesDone,
      };
      try {
        const config = await fetchPublicConfig();
        setLimits(config);
      } catch {
        /* ignore */
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Échec du déclenchement");
    } finally {
      setPhase("idle");
    }
  }

  async function onExtract() {
    if (!loaded || busy || extractBusy) return;
    setError(null);
    setQuotaConfirm(null);

    try {
      const check = await fetchQuotaForPages(loaded.pageCount);
      setLimits((prev) => ({
        ...prev,
        batch_size: check.batchSize,
      }));
      if (check.requiresConfirmation) {
        setQuotaConfirm(check);
        return;
      }
      await runExtraction();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de vérifier le quota journalier",
      );
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

  const visibleTracked = tracked.filter(
    (item) =>
      item.status === "running" ||
      item.status === "paused" ||
      item.stale ||
      item.status === "done" ||
      item.status === "error",
  );

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
            Upload PDF puis extraction Gemini en une passe (chaque lot est
            rendu puis envoyé dès qu&apos;il est prêt).
          </p>
        </header>

        {restoring && (
          <p className="text-sm text-zinc-500">
            Restauration des extractions enregistrées…
          </p>
        )}

        {visibleTracked.length > 0 && (
          <section className="space-y-3">
            <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
              Extractions enregistrées
            </h2>
            <ul className="space-y-2">
              {visibleTracked.map((item) => {
                const selected = job?.sessionId === item.sessionId;
                const pagesDone = item.pagesDone ?? 0;
                const pdfPageCount = item.pdfPageCount ?? "?";
                return (
                  <li
                    key={item.sessionId}
                    className={`rounded-lg border px-3 py-3 text-sm ${statusToneClass(item)} ${
                      selected ? "ring-2 ring-zinc-900 dark:ring-zinc-100" : ""
                    }`}
                  >
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0 space-y-1">
                        <p className="truncate font-medium">
                          {item.sourceName}
                        </p>
                        <p>{statusHeadline(item)}</p>
                        <p className="text-xs opacity-80">
                          {pagesDone}/{pdfPageCount} pages
                        </p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void selectTracked(item)}
                          disabled={busy}
                          className="rounded-lg border border-current/30 bg-white/60 px-3 py-1.5 text-sm font-medium transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-black/20 dark:hover:bg-black/40"
                        >
                          {selected ? "Sélectionnée" : "Afficher"}
                        </button>
                        {item.status === "error" &&
                          !isDailyWaitStatus(item) && (
                            <button
                              type="button"
                              onClick={() => void retryTracked(item)}
                              disabled={busy || extractBusy}
                              className="rounded-lg border border-current/30 bg-white/60 px-3 py-1.5 text-sm font-medium transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-black/20 dark:hover:bg-black/40"
                            >
                              Réessayer
                            </button>
                          )}
                        <button
                          type="button"
                          onClick={() => dismissTracked(item.sessionId)}
                          disabled={busy}
                          className="rounded-lg px-3 py-1.5 text-sm font-medium underline-offset-4 opacity-80 transition hover:underline hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          Retirer
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

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
                ? "Envoi du PDF…"
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

        {error &&
          error !== "dépassement des quota d'utilisation, réessayez demain" && (
            <p className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
              {error}
            </p>
          )}

        {quotaConfirm && (
          <section className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
            <p className="font-medium">Quota journalier insuffisant</p>
            <p className="mt-2">
              {quotaConfirm.message ??
                `Ce PDF nécessite ${quotaConfirm.batchesNeeded} lot(s), il reste ${quotaConfirm.batchesRemaining} lot(s) aujourd'hui. Continuer ?`}
            </p>
            <p className="mt-2 text-xs opacity-80">
              {quotaConfirm.pageCount} pages → {quotaConfirm.batchesNeeded} lots
              · reste {quotaConfirm.batchesRemaining}/
              {quotaConfirm.batchesLimit}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void runExtraction()}
                disabled={busy || extractBusy}
                className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
              >
                Continuer quand même
              </button>
              <button
                type="button"
                onClick={() => setQuotaConfirm(null)}
                disabled={busy}
                className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-800 transition hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-100 dark:hover:bg-zinc-800"
              >
                Annuler
              </button>
            </div>
          </section>
        )}

        {loaded && phase !== "extracting" && !job && (
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
                Extraire le libretto
              </button>
            </div>
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
