"use client";

import { usePathname, useRouter } from "next/navigation";

import { CLE_COOKIE_LANGUE } from "@/dico";

/** Bascule d'affichage arabe ↔ français. Le choix est mémorisé dans un
 * cookie pour la prochaine visite ; l'URL porte la langue courante. */
export default function SelecteurLangue({ cible, libelle }: { cible: string; libelle: string }) {
  const pathname = usePathname() ?? "/";
  const router = useRouter();

  function basculer() {
    document.cookie = `${CLE_COOKIE_LANGUE}=${cible};path=/;max-age=31536000;samesite=lax`;
    const segments = pathname.split("/");
    segments[1] = cible;
    router.push(segments.join("/") || `/${cible}`);
  }

  return (
    <button
      type="button"
      onClick={basculer}
      // Le libellé est dans l'autre langue : balises lang/dir propres pour
      // les lecteurs d'écran et le rendu bidirectionnel.
      lang={cible}
      dir={cible === "ar" ? "rtl" : "ltr"}
      className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 transition hover:border-maroc-600 hover:text-maroc-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maroc-600"
    >
      {libelle}
    </button>
  );
}
