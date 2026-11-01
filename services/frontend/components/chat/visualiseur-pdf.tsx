"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";

import type { Dictionnaire } from "@/dico";

// Le worker est copié dans public/ par predev/prebuild (scripts/) —
// l'unique façon fiable de le servir quelle que soit la chaîne de build.
pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";

interface Props {
  documentId: string;
  titre: string;
  dico: Dictionnaire;
  onFerme: () => void;
}

/** Consultation d'un texte officiel : les pages sont rendues en CANVAS
 * (pdf.js) — le fichier n'est jamais exposé comme lien téléchargeable, et
 * le PDF n'atteint le navigateur que par le proxy serveur (jeton interne
 * resté côté serveur, limitation de débit). Fermeture : bouton ou Échap. */
export default function VisualiseurPdf({ documentId, titre, dico, onFerme }: Props) {
  const conteneurRef = useRef<HTMLDivElement | null>(null);
  const [etat, setEtat] = useState<"chargement" | "pret" | "erreur">("chargement");

  useEffect(() => {
    function surTouche(evenement: KeyboardEvent) {
      if (evenement.key === "Escape") onFerme();
    }
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [onFerme]);

  useEffect(() => {
    let annule = false;
    (async () => {
      try {
        const pdf = await pdfjs.getDocument(
          `/api/sources/pdf/${encodeURIComponent(documentId)}`,
        ).promise;
        const conteneur = conteneurRef.current;
        if (!conteneur) return;
        for (let numero = 1; numero <= pdf.numPages; numero++) {
          const page = await pdf.getPage(numero);
          if (annule) return;
          const viewport = page.getViewport({ scale: 1.6 });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className =
            "mx-auto block h-auto w-full max-w-3xl rounded-md border border-neutral-200 bg-white shadow-sm";
          await page.render({
            canvasContext: canvas.getContext("2d")!,
            viewport,
          }).promise;
          if (annule) return;
          conteneur.appendChild(canvas);
        }
        setEtat("pret");
      } catch {
        if (!annule) setEtat("erreur");
      }
    })();
    return () => {
      annule = true;
    };
  }, [documentId]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-neutral-900/80"
      role="dialog"
      aria-modal="true"
      aria-label={titre}
    >
      <div className="flex items-center justify-between gap-4 bg-white px-4 py-3 shadow-md">
        <p className="truncate text-sm font-medium text-neutral-800">{titre}</p>
        <button
          type="button"
          onClick={onFerme}
          aria-label={dico.sources.fermer}
          className="rounded-md p-2 text-neutral-500 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-maroc-600"
        >
          <svg viewBox="0 0 20 20" className="size-4" fill="none" aria-hidden="true">
            <path
              d="M5 5l10 10M15 5L5 15"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {etat === "erreur" ? (
          <p
            role="alert"
            className="mx-auto mt-10 max-w-md rounded-xl bg-red-50 px-4 py-3 text-center text-sm text-red-800"
          >
            {dico.sources.indisponibleDoc}
          </p>
        ) : (
          <div ref={conteneurRef} className="flex flex-col gap-4">
            {etat === "chargement" && (
              <p className="text-center text-sm text-neutral-300" aria-live="polite">
                {dico.sources.chargement}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
