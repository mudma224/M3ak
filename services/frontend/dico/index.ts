import ar from "./ar.json";
import fr from "./fr.json";

export const LANGUES = ["fr", "ar"] as const;
export type Langue = (typeof LANGUES)[number];

/** Défaut de dernier recours (préférence navigateur absente) — non produite. */
export const LANGUE_DEFAUT: Langue = "fr";

export const CLE_COOKIE_LANGUE = "m3ak-lang";

export type Dictionnaire = typeof fr;

// La forme du dictionnaire arabe est vérifiée à la compilation : toute clé
// manquante casse le build — le bilinguisme ne peut pas se désynchroniser.
const DICTIONNAIRES: Record<Langue, Dictionnaire> = { fr, ar };

export function dictionnaire(langue: Langue): Dictionnaire {
  return DICTIONNAIRES[langue];
}

export function estLangue(valeur: string): valeur is Langue {
  return (LANGUES as readonly string[]).includes(valeur);
}

/** Attribut dir natif — l'arabe se parcourt de droite à gauche. */
export function dirLangue(langue: Langue): "rtl" | "ltr" {
  return langue === "ar" ? "rtl" : "ltr";
}

/** Balise BCP 47 actée (§4.5) : ar-MA et fr-MA. */
export function baliseLangue(langue: Langue): string {
  return langue === "ar" ? "ar-MA" : "fr-MA";
}
