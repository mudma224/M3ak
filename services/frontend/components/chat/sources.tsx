"use client";

import type { Ref } from "react";

import type { Dictionnaire } from "@/dico";
import type { FamilleSource, SourceCitee } from "@/lib/api/conversation";

interface Props {
  ref?: Ref<HTMLDetailsElement>;
  dico: Dictionnaire;
  sources: SourceCitee[];
  /** Vrai quand la réponse a été refusée parce que ces passages, pourtant
   * proches de la question, n'ont pas permis de répondre — le titre
   * l'explicite : un score élevé ne signifie pas que la réponse existe. */
  sansReponse?: boolean;
  /** Consultation du texte intégral (PDF des textes officiels). */
  surConsulter?: (document: { documentId: string; titre: string }) => void;
  /** Checklist de démarche (fiches Idarati : procédures administratives). */
  surConsulterDemarche?: (document: { documentId: string; titre: string }) => void;
}

/** La hiérarchie des sources se voit : un texte officiel (vert), une
 * procédure administrative (bleu) et une décision de justice (violet)
 * ne sont jamais présentés comme équivalents. */
const COULEURS_BADGE: Record<FamilleSource, string> = {
  segment: "bg-maroc-100 text-maroc-800",
  fiche: "bg-sky-100 text-sky-800",
  passage: "bg-violet-100 text-violet-800",
};

export default function Sources({
  ref,
  dico,
  sources,
  sansReponse = false,
  surConsulter,
  surConsulterDemarche,
}: Props) {
  return (
    <details
      ref={ref}
      open
      className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3"
    >
      <summary className="cursor-pointer text-sm font-medium text-neutral-700">
        {sansReponse ? dico.sources.titreNonRepondant : dico.sources.titre} (
        {sources.length})
      </summary>
      <ol className="mt-3 flex flex-col gap-2">
        {sources.map((source, index) => (
          <li
            key={`${source.unite_id}-${index}`}
            data-source={index + 1}
            className="rounded-xl bg-white px-3 py-2 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-neutral-500">
                [{index + 1}]
              </span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${COULEURS_BADGE[source.famille]}`}
              >
                {dico.sources.badge[source.famille]}
              </span>
              <span className="text-xs text-neutral-500">
                {dico.sources.pertinence} : {Math.round(source.score * 100)} %
              </span>
              <span className="text-xs text-neutral-400">· {source.langue}</span>
            </div>
            <p className="mt-1 text-neutral-800">{source.titre}</p>
            {source.famille === "segment" && surConsulter && (
              <button
                type="button"
                onClick={() =>
                  surConsulter({ documentId: source.document_id, titre: source.titre })
                }
                className="mt-1 text-xs font-medium text-maroc-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-maroc-600"
              >
                {dico.sources.consulter}
              </button>
            )}
            {/* Les fiches Idarati (procédures) s'ouvrent en checklist de
                démarche : pièces/conditions/délais/frais étape par étape. */}
            {source.famille === "fiche" && surConsulterDemarche && (
              <button
                type="button"
                onClick={() =>
                  surConsulterDemarche({
                    documentId: source.document_id,
                    titre: source.titre,
                  })
                }
                className="mt-1 block text-xs font-medium text-sky-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-sky-600"
              >
                {dico.demarche.ouvrir}
              </button>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
