/** Construction de la checklist de démarche depuis la fiche Idarati brute
 * (phase 3, étape 4).
 *
 * La source sert des champs hétérogènes : certains « null » arrivent sous
 * forme de chaîne "None", les listes d'administrations sont des objets
 * {title}, les pièces sont groupées en sections (titre + tri). Ce module
 * transforme ce brut en structure TYPÉE et sûre — l'affichage (étiquettes
 * bilingues, cases à cocher) reste dans le composant. Aucune donnée n'est
 * inventée : une section absente de la fiche est absente de la démarche.
 */

export interface GroupePieces {
  titre: string;
  pieces: string[];
}

export interface Demarche {
  /** Description de la fiche = à quoi sert la démarche / conditions. */
  objet: string | null;
  /** Règle d'orientation complémentaire (champ orientationRule). */
  regleOrientation: string | null;
  /** Délai de traitement annoncé par la source (ex. « 60 يوما »). */
  delai: string | null;
  /** Frais (ex. « بالمجان » = gratuit). */
  frais: string | null;
  /** Durée de validité du document délivré, en jours. */
  validiteJours: string | null;
  /** Administration responsable de la procédure. */
  administrationCharge: string | null;
  /** Administrations où déposer le dossier. */
  depot: string[];
  /** Administrations où retirer le document. */
  retrait: string[];
  /** Formulaires téléchargeables annoncés par la source. */
  formulaires: string[];
  /** Pièces requises, groupées par section de la source. */
  groupesPieces: GroupePieces[];
  /** Vrai si la source n'a servi aucune pièce (fiche non enrichie). */
  piecesDisponibles: boolean;
}

type Inconnu = unknown;

function texte(valeur: Inconnu): string | null {
  // La source sérialise ses null en "None" (chaîne) — à ne jamais afficher.
  if (typeof valeur !== "string") return null;
  const propre = valeur.trim();
  return propre && propre !== "None" ? propre : null;
}

function titresListe(valeur: Inconnu): string[] {
  if (!Array.isArray(valeur)) return [];
  return valeur
    .map((entree) =>
      texte(typeof entree === "object" && entree !== null
        ? (entree as Record<string, Inconnu>)["title"]
        : entree),
    )
    .filter((titre): titre is string => titre !== null);
}

function groupesPieces(documents: Inconnu): GroupePieces[] {
  if (!Array.isArray(documents)) return [];
  // Groupement par section (titre + tri), pièces triées à l'intérieur.
  const groupes = new Map<string, { tri: number; pieces: { tri: number; titre: string }[] }>();
  for (const document of documents) {
    if (typeof document !== "object" || document === null) continue;
    const entree = document as Record<string, Inconnu>;
    const titre = texte(entree["title"]);
    if (!titre) continue;
    const section =
      typeof entree["section"] === "object" && entree["section"] !== null
        ? (entree["section"] as Record<string, Inconnu>)
        : {};
    const titreSection = texte(section["title"]) ?? "";
    const triSection = typeof section["sort"] === "number" ? section["sort"] : 0;
    const triPiece = typeof entree["sort"] === "number" ? entree["sort"] : 0;
    const groupe = groupes.get(titreSection) ?? { tri: triSection, pieces: [] };
    groupe.pieces.push({ tri: triPiece, titre });
    groupes.set(titreSection, groupe);
  }
  return [...groupes.entries()]
    .sort((a, b) => a[1].tri - b[1].tri)
    .map(([titre, { pieces }]) => ({
      titre,
      pieces: pieces.sort((a, b) => a.tri - b.tri).map((p) => p.titre),
    }));
}

export function construireDemarche(
  fiche: Inconnu,
  pieces: Inconnu | null,
): Demarche | null {
  if (typeof fiche !== "object" || fiche === null) return null;
  const champs = fiche as Record<string, Inconnu>;
  const validite = texte(champs["validityDurationInDays"]);
  const groupes = groupesPieces(pieces);
  return {
    objet: texte(champs["description"]),
    regleOrientation: texte(champs["orientationRule"]),
    delai: texte(champs["delay"]),
    frais: texte(champs["price"]),
    validiteJours: validite,
    administrationCharge: texte(
      typeof champs["administrationInCharge"] === "object" &&
        champs["administrationInCharge"] !== null
        ? (champs["administrationInCharge"] as Record<string, Inconnu>)["title"]
        : null,
    ),
    depot: titresListe(champs["receivingAdministrations"]),
    retrait: titresListe(champs["deliveringAdministrations"]),
    formulaires: titresListe(champs["downloadableForms"]),
    groupesPieces: groupes,
    // Pas de pièces : soit la fiche n'en a pas, soit elle n'a pas encore
    // été enrichie — l'affichage le dit, sans faire semblant.
    piecesDisponibles: Array.isArray(pieces),
  };
}
