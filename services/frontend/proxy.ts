import { NextResponse, type NextRequest } from "next/server";

import { CLE_COOKIE_LANGUE, estLangue, LANGUE_DEFAUT, LANGUES, type Langue } from "@/dico";

/** Langue préférée d'un en-tête Accept-Language (les mieux notées d'abord). */
function languePreferee(entete: string): Langue | null {
  const candidates = entete
    .split(",")
    .map((morceau) => {
      const [etiquette, ...parametres] = morceau.trim().split(";");
      const q = parametres.find((p) => p.trim().startsWith("q="));
      return {
        etiquette: etiquette.trim().toLowerCase(),
        poids: q ? Number.parseFloat(q.split("=")[1]) || 0 : 1,
      };
    })
    .sort((a, b) => b.poids - a.poids);
  for (const { etiquette } of candidates) {
    if (etiquette.startsWith("ar")) return "ar";
    if (etiquette.startsWith("fr")) return "fr";
  }
  return null;
}

// Convention Next 16 : proxy remplace middleware (déprécié).
export default function proxy(requete: NextRequest) {
  const { pathname } = requete.nextUrl;
  // Fichiers statiques (icône, favicon, manifeste…) : servis tels quels —
  // test explicite ici, le matcher de Next ne gère pas les regards négatifs
  // avec jokers de façon fiable.
  const dernierSegment = pathname.split("/").pop() ?? "";
  if (dernierSegment.includes(".")) {
    return NextResponse.next();
  }
  // Une URL déjà localisée passe telle quelle — la langue de l'URL est la
  // source de vérité de la page affichée.
  if (LANGUES.some((l) => pathname === `/${l}` || pathname.startsWith(`/${l}/`))) {
    return NextResponse.next();
  }
  // Sinon : choix mémorisé par le visiteur, puis préférence du navigateur,
  // puis défaut. La plate-forme est bilingue par conception (§7.5) : aucune
  // des deux langues n'est « la langue par défaut » au sens produit.
  const cookie = requete.cookies.get(CLE_COOKIE_LANGUE)?.value;
  const langue = estLangue(cookie ?? "")
    ? cookie!
    : (languePreferee(requete.headers.get("accept-language") ?? "") ?? LANGUE_DEFAUT);
  const url = requete.nextUrl.clone();
  url.pathname = `/${langue}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next|api).*)"],
};
