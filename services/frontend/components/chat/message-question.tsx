"use client";

import { definirBrouillon } from "@/lib/chat/moteur";
import type { Dictionnaire } from "@/dico";
import type { MessageQuestion } from "@/lib/chat/reducteur";

export default function MessageQuestion({
  dico,
  message,
}: {
  dico: Dictionnaire;
  message: MessageQuestion;
}) {
  function modifier() {
    // La question remonte au composeur pour être éditée puis renvoyée dans
    // la MÊME discussion (le contrat V1 n'a pas de branchement de fil :
    // l'envoi crée un nouvel échange à la suite).
    definirBrouillon(message.texte);
    document.getElementById("question")?.focus();
  }

  return (
    <div className="group/question flex flex-col items-end gap-1">
      <div className="max-w-[85%] rounded-2xl rounded-ee-md bg-maroc-50 px-4 py-3">
        <span className="sr-only">{dico.fil.vous} : </span>
        <p
          dir="auto"
          className="whitespace-pre-wrap text-start text-sm text-neutral-800"
        >
          {message.texte}
        </p>
      </div>
      <button
        type="button"
        onClick={modifier}
        title={dico.actions.modifier}
        aria-label={dico.actions.modifier}
        className="me-1 rounded-md p-1 text-neutral-400 opacity-0 transition hover:bg-neutral-100 hover:text-maroc-700 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-maroc-600 group-hover/question:opacity-100"
      >
        <svg viewBox="0 0 20 20" className="size-3.5" fill="currentColor" aria-hidden="true">
          <path d="M13.586 3.586a2 2 0 1 1 2.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793 3 14.172V17h2.828l8.379-8.379-2.828-2.828z" />
        </svg>
      </button>
    </div>
  );
}
