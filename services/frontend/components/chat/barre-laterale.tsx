"use client";

import { useEffect, useRef, useState } from "react";

import type { Dictionnaire } from "@/dico";
import type { SessionResumee } from "@/lib/chat/reducteur";

interface Props {
  dico: Dictionnaire;
  sessions: SessionResumee[];
  sessionIdActive: string | null;
  locale: string;
  surNouvelle: () => void;
  surOuvrir: (sessionId: string) => void;
  surSupprimer: (sessionId: string) => void;
  /** Classes d'affichage : « hidden lg:flex » (bureau) ou « flex » (tiroir). */
  className?: string;
  surFermer?: () => void;
  mobile?: boolean;
}

/** Index local des discussions (sans comptes : cet index vit dans le
 * navigateur, le service n'expose jamais la liste des sessions). La barre
 * est FIXE : seule sa liste défile, indépendamment du fil de discussion. */
export default function BarreLaterale({
  dico,
  sessions,
  sessionIdActive,
  locale,
  surNouvelle,
  surOuvrir,
  surSupprimer,
  className = "",
  surFermer,
  mobile = false,
}: Props) {
  const boutonNouvelleRef = useRef<HTMLButtonElement | null>(null);
  const boutonAnnulerRef = useRef<HTMLButtonElement | null>(null);
  const [aSupprimer, setASupprimer] = useState<SessionResumee | null>(null);

  useEffect(() => {
    if (mobile) boutonNouvelleRef.current?.focus();
  }, [mobile]);

  // La boîte de confirmation se ferme à Échap et reçoit le focus.
  useEffect(() => {
    if (!aSupprimer) return;
    function surTouche(evenement: KeyboardEvent) {
      if (evenement.key === "Escape") setASupprimer(null);
    }
    window.addEventListener("keydown", surTouche);
    boutonAnnulerRef.current?.focus();
    return () => window.removeEventListener("keydown", surTouche);
  }, [aSupprimer]);

  const formatDate = new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
  });

  return (
    <>
      <nav
        aria-label={dico.discussions.titre}
        className={`flex-col gap-3 border-e border-neutral-200 bg-neutral-50 p-3 ${className}`}
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          <div className="flex shrink-0 items-center gap-2">
            <button
              ref={boutonNouvelleRef}
              type="button"
              onClick={() => {
                surNouvelle();
                surFermer?.();
              }}
              className="flex-1 rounded-xl bg-maroc-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-maroc-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maroc-600"
            >
              {dico.discussions.nouvelle}
            </button>
            {mobile && surFermer && (
              <button
                type="button"
                onClick={surFermer}
                aria-label={dico.discussions.fermer}
                className="rounded-md p-2 text-neutral-500 hover:bg-neutral-200 focus-visible:outline-2 focus-visible:outline-maroc-600"
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
            )}
          </div>

          {sessions.length > 0 && (
            <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
              {sessions.map((session) => {
                const active = session.id === sessionIdActive;
                return (
                  <li key={session.id} className="group relative">
                    <button
                      type="button"
                      onClick={() => {
                        surOuvrir(session.id);
                        surFermer?.();
                      }}
                      aria-current={active || undefined}
                      className={`w-full rounded-lg px-3 py-2 pe-9 text-start text-sm transition hover:bg-white focus-visible:outline-2 focus-visible:outline-maroc-600 ${
                        active
                          ? "bg-white font-medium text-maroc-800 shadow-sm"
                          : "text-neutral-700"
                      }`}
                    >
                      <span className="block truncate">
                        {session.titre ?? dico.discussions.nouvelle}
                      </span>
                      <span className="block text-xs font-normal text-neutral-400">
                        {formatDate.format(new Date(session.majLe))}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setASupprimer(session)}
                      aria-label={`${dico.discussions.supprimer} — ${session.titre ?? dico.discussions.nouvelle}`}
                      className="absolute inset-y-0 end-1 my-auto h-7 rounded-md px-2 text-xs text-neutral-400 opacity-60 transition hover:bg-neutral-200 hover:text-neutral-600 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-maroc-600 group-hover:opacity-100 lg:opacity-0"
                    >
                      ✕
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </nav>

      {aSupprimer && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="titre-suppression"
        >
          <div
            className="absolute inset-0 bg-neutral-900/50"
            onClick={() => setASupprimer(null)}
            aria-hidden="true"
          />
          <div className="relative w-full max-w-md rounded-2xl bg-white p-5 shadow-xl">
            <p id="titre-suppression" className="text-sm font-semibold text-neutral-900">
              {dico.discussions.supprimerTitre}
            </p>
            <p className="mt-2 truncate text-sm text-neutral-700">
              {aSupprimer.titre ?? dico.discussions.nouvelle}
            </p>
            <p className="mt-1 text-xs text-neutral-500">
              {dico.discussions.supprimerTexte}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                ref={boutonAnnulerRef}
                type="button"
                onClick={() => setASupprimer(null)}
                className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 transition hover:bg-neutral-50 focus-visible:outline-2 focus-visible:outline-maroc-600"
              >
                {dico.discussions.annuler}
              </button>
              <button
                type="button"
                onClick={() => {
                  surSupprimer(aSupprimer.id);
                  setASupprimer(null);
                }}
                className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-red-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600"
              >
                {dico.discussions.supprimer}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
