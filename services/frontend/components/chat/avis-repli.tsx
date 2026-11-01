"use client";

import type { Dictionnaire } from "@/dico";

/** Avis de repli interlingue (stratégie de langue §5) : la question est dans
 * une langue, les sources retenues sont toutes dans l'autre. L'utilisateur
 * doit savoir pourquoi les citations verbatim qui suivent ne sont pas dans
 * sa langue — l'explication, elle, reste dans sa langue. Registre
 * informatif (bleu), distinct des avertissements contextuels (ambre). */
export default function AvisRepli({
  dico,
  langueSources,
}: {
  dico: Dictionnaire;
  langueSources: "ar" | "fr";
}) {
  const texte =
    langueSources === "ar" ? dico.repli.arabe : dico.repli.francais;
  return (
    <aside className="rounded-2xl border border-sky-300 bg-sky-50 px-4 py-3">
      <p className="text-sm font-semibold text-sky-900">{dico.repli.titre}</p>
      <p className="mt-1 text-sm leading-relaxed text-sky-900">{texte}</p>
    </aside>
  );
}
