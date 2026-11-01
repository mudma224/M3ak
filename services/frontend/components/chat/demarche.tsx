"use client";

import { useEffect, useState } from "react";

import type { Dictionnaire } from "@/dico";
import { construireDemarche, type Demarche } from "@/lib/demarche/construire";

interface Props {
  documentId: string;
  titre: string;
  dico: Dictionnaire;
  onFerme: () => void;
}

/** Checklist de démarche (phase 3, étape 4) : les pièces/conditions/délais/
 * frais de la fiche Idarati restitués en étapes COCHABLES. Les données
 * viennent du proxy (fiche brute + pièces de la couche brute) ; les cases
 * vivent dans le composant — l'app est sans compte, la checklist est un
 * aide-mémoire de session, jamais une donnée service. Fermeture : bouton
 * ou Échap. */
export default function Demarche({ documentId, titre, dico, onFerme }: Props) {
  const [demarche, setDemarche] = useState<Demarche | null>(null);
  const [etat, setEtat] = useState<"chargement" | "pret" | "erreur">("chargement");
  const [cochees, setCochees] = useState<Set<string>>(new Set());

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
        const reponse = await fetch(
          `/api/sources/fiche/${encodeURIComponent(documentId)}`,
          { cache: "no-store" },
        );
        if (!reponse.ok) throw new Error(String(reponse.status));
        const corps = (await reponse.json()) as { fiche?: unknown; pieces?: unknown };
        const construite = construireDemarche(corps.fiche, corps.pieces ?? null);
        if (!construite) throw new Error("fiche illisible");
        if (!annule) {
          setDemarche(construite);
          setEtat("pret");
        }
      } catch {
        if (!annule) setEtat("erreur");
      }
    })();
    return () => {
      annule = true;
    };
  }, [documentId]);

  function basculer(cle: string) {
    setCochees((anciennes) => {
      const nouvelles = new Set(anciennes);
      if (nouvelles.has(cle)) nouvelles.delete(cle);
      else nouvelles.add(cle);
      return nouvelles;
    });
  }

  function caseACocher(cle: string, libelle: string, detail?: string | null) {
    const cochee = cochees.has(cle);
    return (
      <label className="flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2 transition hover:bg-neutral-100 focus-within:bg-neutral-100">
        <input
          type="checkbox"
          checked={cochee}
          onChange={() => basculer(cle)}
          className="mt-0.5 size-4 shrink-0 accent-maroc-700"
        />
        <span className="min-w-0">
          <span
            dir="auto"
            className={`block text-start text-sm ${cochee ? "text-neutral-400 line-through" : "text-neutral-800"}`}
          >
            {libelle}
          </span>
          {detail && (
            <span dir="auto" className="mt-0.5 block text-start text-xs text-neutral-500">
              {detail}
            </span>
          )}
        </span>
      </label>
    );
  }

  function titreSection(numero: number, libelle: string) {
    return (
      <h3 className="mt-5 flex items-baseline gap-2 text-sm font-semibold text-maroc-800">
        <span
          className="flex size-5 shrink-0 items-center justify-center rounded-full bg-maroc-700 text-[11px] font-semibold text-white"
          aria-hidden="true"
        >
          {numero}
        </span>
        {libelle}
      </h3>
    );
  }

  let numeroEtape = 0;
  const etapeSuivante = () => ++numeroEtape;

  // Progression : items cochables = objet + pièces + dépôt + formulaires +
  // frais + retrait (le délai est une information, pas une action).
  const clesAction = demarche
    ? [
        "objet",
        ...demarche.groupesPieces.flatMap((g, gi) =>
          g.pieces.map((_, pi) => `piece:${gi}:${pi}`),
        ),
        "depot",
        ...demarche.formulaires.map((_, i) => `formulaire:${i}`),
        "frais",
        "retrait",
      ]
    : [];

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-neutral-900/50"
      role="dialog"
      aria-modal="true"
      aria-label={`${dico.demarche.titre} — ${titre}`}
    >
      <div className="flex items-center justify-between gap-4 bg-white px-4 py-3 shadow-md">
        <p className="truncate text-sm font-medium text-neutral-800">
          <span className="font-semibold text-maroc-800">{dico.demarche.titre}</span>
          <span className="mx-2 text-neutral-300" aria-hidden="true">
            ·
          </span>
          {titre}
          {clesAction.length > 0 && etat === "pret" && (
            <span className="ms-3 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-normal text-neutral-500 tabular-nums">
              {cochees.size} / {clesAction.length} {dico.demarche.progression}
            </span>
          )}
        </p>
        <button
          type="button"
          onClick={onFerme}
          aria-label={dico.demarche.fermer}
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
          <div className="mx-auto w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
            <p role="alert" className="text-center text-sm text-red-800">
              {dico.demarche.indisponible}
            </p>
          </div>
        ) : etat === "chargement" ? (
          <div className="mx-auto w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
            <p className="text-center text-sm text-neutral-500" aria-live="polite">
              {dico.demarche.chargement}
            </p>
          </div>
        ) : demarche ? (
          <div className="mx-auto w-full max-w-2xl rounded-2xl bg-white p-4 shadow-xl sm:p-6">
            {/* 1. Objet / conditions d'usage de la démarche. */}
            {demarche.objet && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.objet)}
                {caseACocher(
                  "objet",
                  demarche.objet,
                  demarche.regleOrientation ?? undefined,
                )}
              </>
            )}

            {/* 2. Pièces requises, groupées comme la source les présente. */}
            {titreSection(etapeSuivante(), dico.demarche.pieces)}
            {demarche.piecesDisponibles ? (
              demarche.groupesPieces.length > 0 ? (
                demarche.groupesPieces.map((groupe, gi) => (
                  <div key={`${groupe.titre}-${gi}`} className="mt-1">
                    {groupe.titre && (
                      <p dir="auto" className="px-3 pt-2 text-xs font-medium uppercase tracking-wide text-neutral-400">
                        {groupe.titre}
                      </p>
                    )}
                    {groupe.pieces.map((piece, pi) =>
                      caseACocher(`piece:${gi}:${pi}`, piece),
                    )}
                  </div>
                ))
              ) : (
                <p dir="auto" className="px-3 py-2 text-sm text-neutral-500">
                  {dico.demarche.piecesVides}
                </p>
              )
            ) : (
              <p dir="auto" className="px-3 py-2 text-sm text-neutral-500">
                {dico.demarche.piecesIndisponibles}
              </p>
            )}

            {/* 3. Dépôt du dossier : les administrations compétentes. */}
            {(demarche.depot.length > 0 || demarche.administrationCharge) && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.depot)}
                {caseACocher(
                  "depot",
                  [demarche.administrationCharge, ...demarche.depot]
                    .filter((v): v is string => Boolean(v))
                    .join(" · "),
                )}
              </>
            )}

            {/* 4. Formulaires éventuels. */}
            {demarche.formulaires.length > 0 && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.formulaires)}
                {demarche.formulaires.map((formulaire, i) =>
                  caseACocher(`formulaire:${i}`, formulaire),
                )}
              </>
            )}

            {/* 5. Frais. */}
            {demarche.frais && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.frais)}
                {caseACocher("frais", demarche.frais)}
              </>
            )}

            {/* 6. Traitement : information, pas une action à cocher. */}
            {(demarche.delai || demarche.validiteJours) && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.delai)}
                <p dir="auto" className="px-3 py-2 text-sm text-neutral-800">
                  {demarche.delai ?? "—"}
                  {demarche.validiteJours && (
                    <span className="ms-3 text-xs text-neutral-500">
                      {dico.demarche.validite} : {demarche.validiteJours}
                    </span>
                  )}
                </p>
              </>
            )}

            {/* 7. Retrait. */}
            {demarche.retrait.length > 0 && (
              <>
                {titreSection(etapeSuivante(), dico.demarche.retrait)}
                {caseACocher("retrait", demarche.retrait.join(" · "))}
              </>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
