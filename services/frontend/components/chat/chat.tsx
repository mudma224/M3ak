"use client";

import { useEffect, useState } from "react";

import {
  definirBrouillon,
  envoyer,
  nouvelleDiscussion,
  ouvrirDiscussion,
  restaurerSiNecessaire,
  supprimerDiscussion,
  useMoteurChat,
} from "@/lib/chat/moteur";
import { CLE_NOUVELLE, type MessageChat } from "@/lib/chat/reducteur";
import type { Dictionnaire, Langue } from "@/dico";
import { baliseLangue } from "@/dico";

import BarreLaterale from "./barre-laterale";
import Composeur from "./composeur";
import Fil from "./fil";
import Demarche from "./demarche";
import VisualiseurPdf from "./visualiseur-pdf";

/** Coquille du chat — la mécanique (état, flux, stockage) vit dans le
 * magasin externe `lib/chat/moteur.ts` : elle survit au changement de
 * langue et aux remontages, et continue d'alimenter les discussions en
 * arrière-plan. */
export default function Chat({ dico, langue }: { dico: Dictionnaire; langue: Langue }) {
  const { etat, brouillon } = useMoteurChat();
  const [tiroir, setTiroir] = useState(false);
  const [consulte, setConsulte] = useState<{ documentId: string; titre: string } | null>(
    null,
  );
  const [demarcheConsultee, setDemarcheConsultee] = useState<{
    documentId: string;
    titre: string;
  } | null>(null);

  // Restauration idempotente (index + conversation active) — le garde-fou
  // interne fait que le remontage du composant ne rejoue pas le chargement.
  useEffect(() => {
    restaurerSiNecessaire();
  }, []);

  // Le tiroir mobile se ferme à Échap (dialogue modal léger).
  useEffect(() => {
    if (!tiroir) return;
    function surTouche(evenement: KeyboardEvent) {
      if (evenement.key === "Escape") setTiroir(false);
    }
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [tiroir]);

  const filActif: MessageChat[] = etat.fils[etat.sessionId ?? CLE_NOUVELLE] ?? [];
  const vide = filActif.length === 0;
  const dernierFil = filActif[filActif.length - 1];
  const enCours = dernierFil !== undefined && dernierFil.role === "reponse" && dernierFil.enCours;

  const proprietesBarre = {
    dico,
    sessions: etat.sessions,
    sessionIdActive: etat.sessionId,
    locale: baliseLangue(langue),
    surNouvelle: () => {
      nouvelleDiscussion();
      setTiroir(false);
    },
    surOuvrir: (sessionId: string) => {
      void ouvrirDiscussion(sessionId);
      setTiroir(false);
    },
    surSupprimer: (sessionId: string) => void supprimerDiscussion(sessionId),
  };

  return (
    <div className="flex min-h-0 w-full flex-1 items-stretch">
      <BarreLaterale {...proprietesBarre} className="hidden w-72 shrink-0 lg:flex" />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <button
          type="button"
          onClick={() => setTiroir(true)}
          aria-label={dico.discussions.menu}
          className="mt-2 ms-2 self-start rounded-md p-2 text-neutral-500 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-maroc-600 lg:hidden"
        >
          <svg viewBox="0 0 20 20" className="size-5" fill="none" aria-hidden="true">
            <path
              d="M3 5h14M3 10h14M3 15h14"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </button>

        {/* Le fil défile DANS sa colonne : la barre latérale reste fixe. */}
        <div className="mx-auto flex w-full max-w-3xl min-h-0 flex-1 flex-col px-4 sm:px-6">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {vide ? (
              <section className="pb-6 pt-10 text-center sm:pt-14">
                <h1 className="text-balance text-2xl font-semibold text-neutral-900 sm:text-3xl">
                  {dico.accueil.titre}
                </h1>
                <p className="mx-auto mt-3 max-w-xl text-neutral-600">
                  {dico.accueil.sousTitre}
                </p>
                <ul className="mt-8 flex flex-col gap-2" aria-label={dico.accueil.exemplesTitre}>
                  {dico.accueil.exemples.map((exemple) => (
                    <li key={exemple}>
                      <button
                        type="button"
                        onClick={() => definirBrouillon(exemple)}
                        className="w-full rounded-xl border border-neutral-200 bg-white px-4 py-3 text-start text-sm text-neutral-600 transition hover:border-maroc-600 hover:text-maroc-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maroc-600"
                      >
                        {exemple}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : (
              <Fil
                dico={dico}
                messages={filActif}
                surConsulter={(document) => setConsulte(document)}
                surConsulterDemarche={(document) => setDemarcheConsultee(document)}
              />
            )}

            {etat.erreur && (
              <p
                role="alert"
                className="mb-2 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800"
              >
                {dico.erreurs[etat.erreur]}
              </p>
            )}
          </div>

          <div className="shrink-0 bg-white pb-4 pt-2">
            <Composeur
              dico={dico}
              valeur={brouillon}
              surChangement={definirBrouillon}
              surEnvoi={() => void envoyer()}
              enCours={enCours}
            />
          </div>
        </div>
      </div>

      {tiroir && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-neutral-900/40"
            onClick={() => setTiroir(false)}
            aria-hidden="true"
          />
          <BarreLaterale
            {...proprietesBarre}
            surFermer={() => setTiroir(false)}
            mobile
            className="absolute inset-y-0 start-0 flex w-80 max-w-[85vw] shadow-xl"
          />
        </div>
      )}

      {consulte && (
        <VisualiseurPdf
          documentId={consulte.documentId}
          titre={consulte.titre}
          dico={dico}
          onFerme={() => setConsulte(null)}
        />
      )}

      {demarcheConsultee && (
        <Demarche
          documentId={demarcheConsultee.documentId}
          titre={demarcheConsultee.titre}
          dico={dico}
          onFerme={() => setDemarcheConsultee(null)}
        />
      )}
    </div>
  );
}
