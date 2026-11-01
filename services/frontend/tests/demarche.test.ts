import { describe, expect, it } from "vitest";

import { construireDemarche } from "@/lib/demarche/construire";

const FICHE = {
  id: "e0dbe9d5-0000-0000-0000-000000000000",
  title: "الاعتراف الإداري",
  description: "متابعة الدراسة",
  price: "بالمجان",
  delay: "60 يوما",
  // La source sérialise ses null en "None" — jamais à afficher.
  delayFix: "None",
  validityDurationInDays: "None",
  orientationRule: "None",
  administrationInCharge: { id: "x", title: "جامعة محمد الأول" },
  receivingAdministrations: [{ id: "y", title: "المدرسة الوطنية للتجارة و التسيير" }],
  deliveringAdministrations: [
    { id: "y", title: "المدرسة الوطنية للتجارة و التسيير" },
    { id: "z", title: "كلية الآداب والعلوم الإنسانية" },
  ],
  downloadableForms: [],
};

const PIECES = [
  {
    title: "التصريح بالضياع أو السرقة",
    section: { sort: 0, title: "الوثائق المطلوبة" },
    sort: 0,
  },
  {
    title: "البطاقة الوطنية للتعريف",
    section: { sort: 0, title: "الوثائق المطلوبة" },
    sort: 1,
  },
];

describe("construction de la démarche depuis la fiche brute", () => {
  it("extrait les champs utiles et ignore les null sérialisés", () => {
    const demarche = construireDemarche(FICHE, PIECES)!;
    expect(demarche.objet).toBe("متابعة الدراسة");
    expect(demarche.delai).toBe("60 يوما");
    expect(demarche.frais).toBe("بالمجان");
    // "None" (chaîne) et champs absents → null, jamais affichés.
    expect(demarche.regleOrientation).toBeNull();
    expect(demarche.validiteJours).toBeNull();
    expect(demarche.administrationCharge).toBe("جامعة محمد الأول");
    expect(demarche.depot).toHaveLength(1);
    expect(demarche.retrait).toHaveLength(2);
    expect(demarche.formulaires).toEqual([]);
    expect(demarche.piecesDisponibles).toBe(true);
  });

  it("groupe les pièces par section, dans l'ordre de la source", () => {
    const demarche = construireDemarche(FICHE, PIECES)!;
    expect(demarche.groupesPieces).toEqual([
      {
        titre: "الوثائق المطلوبة",
        pieces: ["التصريح بالضياع أو السرقة", "البطاقة الوطنية للتعريف"],
      },
    ]);
  });

  it("distingue fiche sans pièces ([]) et fiche non enrichie (null)", () => {
    const vide = construireDemarche(FICHE, [])!;
    expect(vide.piecesDisponibles).toBe(true);
    expect(vide.groupesPieces).toEqual([]);

    const nonEnrichie = construireDemarche(FICHE, null)!;
    expect(nonEnrichie.piecesDisponibles).toBe(false);
    expect(nonEnrichie.groupesPieces).toEqual([]);
  });

  it("reste sûr sur des formes inattendues", () => {
    expect(construireDemarche(null, PIECES)).toBeNull();
    expect(construireDemarche("texte", PIECES)).toBeNull();
    const bizarre = construireDemarche(
      { description: 42, receivingAdministrations: "pas-une-liste" },
      ["pas-un-objet", { title: "البطاقة الوطنية" }],
    )!;
    expect(bizarre.objet).toBeNull();
    expect(bizarre.depot).toEqual([]);
    expect(bizarre.groupesPieces).toEqual([
      { titre: "", pieces: ["البطاقة الوطنية"] },
    ]);
  });
});
