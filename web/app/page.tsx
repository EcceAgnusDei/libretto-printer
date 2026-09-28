"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";

import {
  DEFAULT_PUBLIC_CONFIG,
  fetchPublicConfig,
  type PublicConfig,
} from "../lib/apiConfig";
import { validatePdfLocally, type LocalPdfPreview } from "../lib/pdfLocal";

export default function Home() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [limits, setLimits] = useState<PublicConfig>(DEFAULT_PUBLIC_CONFIG);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<LocalPdfPreview | null>(null);

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

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0 || busy) return;
    setError(null);
    setBusy(true);

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
      setBusy(false);
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

  return (
    <div className="flex min-h-full flex-1 justify-center px-6 py-10">
      <main className="w-full max-w-3xl space-y-8">
        <header className="space-y-2">
          <p className="text-sm tracking-wide text-zinc-500 uppercase">
            Couche 2 — Ingestion
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            libretto-printer
          </h1>
          <p className="text-zinc-600 dark:text-zinc-400">
            Vérification du PDF dans le navigateur. Aucun envoi au serveur tant
            que l&apos;extraction n&apos;est pas lancée.
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
            {busy
              ? "Vérification du PDF…"
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
          </section>
        )}
      </main>
    </div>
  );
}
