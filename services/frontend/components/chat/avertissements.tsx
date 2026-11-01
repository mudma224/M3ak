"use client";

import type { Dictionnaire } from "@/dico";

/** Avertissements contextuels (droits, argent, liberté en jeu) — portés
 * par le service, affichés en bandeau ambre, jamais fondus dans la réponse. */
export default function Avertissements({
  dico,
  textes,
}: {
  dico: Dictionnaire;
  textes: string[];
}) {
  return (
    <aside className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3">
      <p className="text-sm font-semibold text-amber-900">
        {dico.avertissements.titre}
      </p>
      <ul className="mt-1 list-disc ps-5 text-sm text-amber-900">
        {textes.map((texte) => (
          <li key={texte} dir="auto" className="text-start">
            {texte}
          </li>
        ))}
      </ul>
    </aside>
  );
}
