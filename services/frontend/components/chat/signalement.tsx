"use client";

import { useState } from "react";

import type { Dictionnaire } from "@/dico";
import { signalerReponse } from "@/lib/api/conversation";

type EtatEnvoi = "repos" | "envoi" | "succes" | "echec";

/** Signalement d'une réponse — motifs rapides (le contrat V1 porte un
 * motif libre ; l'interface propose des raisons prêtes à l'envoi). */
export default function Signalement({
  dico,
  messageId,
}: {
  dico: Dictionnaire;
  messageId: string;
}) {
  const [ouvert, setOuvert] = useState(false);
  const [etat, setEtat] = useState<EtatEnvoi>("repos");

  async function envoyer(motif: string) {
    setEtat("envoi");
    try {
      await signalerReponse(messageId, motif);
      setEtat("succes");
    } catch {
      setEtat("echec");
    }
  }

  if (etat === "succes") {
    return <p className="text-xs text-neutral-500">{dico.signalement.succes}</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {!ouvert ? (
        <button
          type="button"
          onClick={() => setOuvert(true)}
          className="self-start text-xs text-neutral-500 underline-offset-2 hover:text-neutral-700 hover:underline focus-visible:outline-2 focus-visible:outline-maroc-600"
        >
          {dico.signalement.bouton}
        </button>
      ) : (
        <div className="rounded-xl border border-neutral-200 px-3 py-2">
          <p className="text-xs font-medium text-neutral-600">
            {dico.signalement.titre}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(dico.signalement.raisons).map(([cle, libelle]) => (
              <button
                key={cle}
                type="button"
                disabled={etat === "envoi"}
                onClick={() => envoyer(libelle)}
                className="rounded-full border border-neutral-300 px-3 py-1 text-xs text-neutral-700 transition hover:border-maroc-600 hover:text-maroc-700 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-maroc-600"
              >
                {libelle}
              </button>
            ))}
          </div>
          {etat === "echec" && (
            <p className="mt-2 text-xs text-red-700">{dico.signalement.echec}</p>
          )}
        </div>
      )}
    </div>
  );
}
