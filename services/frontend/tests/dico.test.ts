import { describe, expect, it } from "vitest";

import { LANGUES } from "@/dico";
import ar from "@/dico/ar.json";
import fr from "@/dico/fr.json";

function cles(objet: unknown, prefixe = ""): string[] {
  if (typeof objet !== "object" || objet === null) return [prefixe];
  return Object.entries(objet as Record<string, unknown>).flatMap(([cle, valeur]) =>
    typeof valeur === "object" && valeur !== null
      ? cles(valeur, `${prefixe}${cle}.`)
      : [`${prefixe}${cle}`],
  );
}

describe("dictionnaires bilingues", () => {
  it("fr et ar portent exactement les mêmes clés", () => {
    expect(cles(ar).sort()).toEqual(cles(fr).sort());
  });

  it("couvre les deux langues actées", () => {
    expect([...LANGUES].sort()).toEqual(["ar", "fr"]);
  });

  it("les motifs de refus connus du contrat ont tous un libellé", () => {
    const motifsAttendus = [
      "aucune_source",
      "score_insuffisant",
      "generation_non_verifiable",
      "insuffisant_modele",
      "generation_tronquee",
    ];
    for (const motif of motifsAttendus) {
      expect(fr.refus.motifs).toHaveProperty(motif);
      expect(ar.refus.motifs).toHaveProperty(motif);
    }
  });

  it("les familles de sources actées ont toutes un badge", () => {
    for (const famille of ["segment", "fiche", "passage"]) {
      expect(fr.sources.badge).toHaveProperty(famille);
      expect(ar.sources.badge).toHaveProperty(famille);
    }
  });

  it("le titre des passages non répondants existe dans les deux langues", () => {
    // Sources émises puis jugées non répondantes (insuffisant_modele) : le
    // panneau ne doit pas suggérer que la réponse existe.
    for (const dico of [fr, ar]) {
      expect(dico.sources.titreNonRepondant.length).toBeGreaterThan(0);
      expect(dico.sources.titreNonRepondant).not.toBe(dico.sources.titre);
    }
  });

  it("l'avis de repli interlingue couvre les deux directions", () => {
    // Question fr → sources ar et question ar → sources fr : chaque
    // direction a son texte explicite (stratégie de langue §5).
    for (const dico of [fr, ar]) {
      expect(dico.repli.titre.length).toBeGreaterThan(0);
      expect(dico.repli.arabe.length).toBeGreaterThan(0);
      expect(dico.repli.francais.length).toBeGreaterThan(0);
    }
  });

  it("la notice de réponse partielle existe dans les deux langues", () => {
    // Phrases vérifiées diffusées puis échec final : le texte reste servi,
    // encadré d'une notice — pas d'alerte de sortie. Deux notices selon le
    // motif : sources insuffisantes, ou vérification finale/troncature.
    for (const dico of [fr, ar]) {
      expect(dico.refus.partiel.titre.length).toBeGreaterThan(0);
      expect(dico.refus.partiel.texte.length).toBeGreaterThan(0);
      expect(dico.refus.partielIncomplet.titre.length).toBeGreaterThan(0);
      expect(dico.refus.partielIncomplet.texte.length).toBeGreaterThan(0);
      // Réponse d'orientation (SANS_REPONSE vérifiée) : note neutre, sans
      // style d'erreur — le texte du modèle dit lui-même l'absence de réponse.
      expect(dico.refus.sansReponse.note.length).toBeGreaterThan(0);
    }
  });

  it("la couverture annoncée porte les trois natures de sources, non vides", () => {
    for (const dico of [fr, ar]) {
      const natures = [
        dico.refus.couverture.textes,
        dico.refus.couverture.procedures,
        dico.refus.couverture.jurisprudence,
      ];
      for (const nature of natures) {
        expect(nature.label.length).toBeGreaterThan(0);
        expect(nature.domaines.length).toBeGreaterThan(0);
        for (const domaine of nature.domaines) {
          expect(domaine.length).toBeGreaterThan(0);
        }
      }
    }
  });
});
