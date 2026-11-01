"use client";

import type { KeyboardEvent } from "react";

import type { Dictionnaire } from "@/dico";

interface Props {
  dico: Dictionnaire;
  valeur: string;
  surChangement: (valeur: string) => void;
  surEnvoi: () => void;
  enCours: boolean;
}

/** Limite du contrat DemandeQuestion — le service la redouble. */
const TAILLE_MAX = 2000;

export default function Composeur({
  dico,
  valeur,
  surChangement,
  surEnvoi,
  enCours,
}: Props) {
  const envoiPossible = valeur.trim().length > 0 && !enCours;

  function surTouche(evenement: KeyboardEvent<HTMLTextAreaElement>) {
    // Entrée envoie ; Maj+Entrée passe la ligne ; la composition IME
    // (arabe, suggestions clavier) n'est jamais interceptée.
    if (
      evenement.key === "Enter" &&
      !evenement.shiftKey &&
      !evenement.nativeEvent.isComposing
    ) {
      evenement.preventDefault();
      if (envoiPossible) surEnvoi();
    }
  }

  return (
    <form
      onSubmit={(evenement) => {
        evenement.preventDefault();
        if (envoiPossible) surEnvoi();
      }}
    >
      <label htmlFor="question" className="sr-only">
        {dico.composeur.etiquette}
      </label>
      <div className="flex items-end gap-2 rounded-2xl border border-neutral-300 bg-white p-2 shadow-sm focus-within:border-maroc-600">
        <textarea
          id="question"
          rows={2}
          maxLength={TAILLE_MAX}
          value={valeur}
          onChange={(evenement) => surChangement(evenement.target.value)}
          onKeyDown={surTouche}
          placeholder={dico.composeur.placeholder}
          disabled={enCours}
          className="flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-neutral-400 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={!envoiPossible}
          className="shrink-0 rounded-xl bg-maroc-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-maroc-700 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maroc-600"
        >
          {enCours ? dico.composeur.envoiEnCours : dico.composeur.envoyer}
        </button>
      </div>
      <p className="mt-2 text-center text-xs text-neutral-400">
        {dico.composeur.indication}
      </p>
    </form>
  );
}
