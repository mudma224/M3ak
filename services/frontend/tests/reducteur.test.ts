import { describe, expect, it } from "vitest";

import type { ReponseQuestion, SourceCitee } from "@/lib/api/conversation";
import {
  cleErreurFlux,
  CLE_NOUVELLE,
  ETAT_INITIAL,
  messagesDepuisHistorique,
  reducteurChat,
  tronquerTitre,
} from "@/lib/chat/reducteur";

const SOURCE: SourceCitee = {
  unite_id: "SEG-1",
  famille: "segment",
  document_id: "doc-1",
  titre: "Code de la famille",
  source: "adala",
  collection: "textes_normatifs",
  langue: "ar",
  score: 0.92,
};

const REPONSE: ReponseQuestion = {
  session_id: "sess-1",
  message_id: "m2",
  reponse: "La réponse [1].",
  langue_question: "fr",
  refus: false,
  motif_refus: null,
  sources: [SOURCE],
  citations_verifiees: true,
  citations_rejetees: 0,
  avertissements: ["Un avertissement."],
  repli_interlingue: false,
  texte_partiel: false,
  sans_reponse: false,
  score_meilleur: 0.92,
  fournisseur: "local",
  modele: "gemma",
  latence_generation_ms: 120,
  duree_ms: 200,
};

const HORODATAGE = "2026-10-04T10:00:00Z";

function etatAvecQuestionEnCours(sessionId = "sess-1") {
  const apresSession = reducteurChat(ETAT_INITIAL, {
    type: "SESSION_OUVERTE",
    sessionId,
  });
  return reducteurChat(apresSession, {
    type: "QUESTION_ENVOYEE",
    sessionId,
    texte: "Quelle est la procédure ?",
    horodatage: HORODATAGE,
  });
}

function dernierDe(etat: ReturnType<typeof reducteurChat>, sessionId: string) {
  const fil = etat.fils[sessionId] ?? [];
  return fil[fil.length - 1];
}

describe("réducteur du chat — flux d'une discussion", () => {
  it("déroule le flux complet : étapes, sources, fragments, réponse finale", () => {
    let etat = etatAvecQuestionEnCours();
    const fil = etat.fils["sess-1"];
    expect(fil).toHaveLength(2);

    etat = reducteurChat(etat, { type: "ETAPE", sessionId: "sess-1", etape: "recherche" });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ etape: "recherche" });

    etat = reducteurChat(etat, { type: "ETAPE", sessionId: "sess-1", etape: "generation" });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ etape: "generation" });

    etat = reducteurChat(etat, { type: "SOURCES", sessionId: "sess-1", sources: [SOURCE] });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ sources: [SOURCE] });

    etat = reducteurChat(etat, { type: "FRAGMENT", sessionId: "sess-1", texte: "La réponse " });
    etat = reducteurChat(etat, { type: "FRAGMENT", sessionId: "sess-1", texte: "[1]." });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ texte: "La réponse [1]." });

    etat = reducteurChat(etat, {
      type: "REPONSE_FINALE",
      sessionId: "sess-1",
      reponse: REPONSE,
    });
    const finale = dernierDe(etat, "sess-1");
    expect(finale).toMatchObject({
      role: "reponse",
      id: "m2",
      texte: "La réponse [1].",
      enCours: false,
      refus: false,
      avertissements: ["Un avertissement."],
      dureeMs: 200,
    });
  });

  it("ignore une étape inconnue d'une version future du contrat", () => {
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, { type: "ETAPE", sessionId: "sess-1", etape: "meditation" });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ etape: null });
  });

  it("un flux continue d'alimenter SA discussion même si une autre est affichée", () => {
    // Discussion A en cours de génération…
    let etat = etatAvecQuestionEnCours("sess-1");
    // …l'utilisateur ouvre une autre discussion…
    etat = reducteurChat(etat, {
      type: "HISTORIQUE_RESTAURE",
      sessionId: "sess-2",
      messages: messagesDepuisHistorique([
        { role: "user", contenu: "Ancienne question", refus: false },
      ]),
    });
    // …les fragments du flux de A arrivent quand même.
    etat = reducteurChat(etat, { type: "FRAGMENT", sessionId: "sess-1", texte: "Morceau [1]." });
    expect(etat.sessionId).toBe("sess-2");
    expect(dernierDe(etat, "sess-1")).toMatchObject({ texte: "Morceau [1].", enCours: true });
    // Et la clôture finale atterrit dans la bonne discussion.
    etat = reducteurChat(etat, {
      type: "REPONSE_FINALE",
      sessionId: "sess-1",
      reponse: REPONSE,
    });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ enCours: false, id: "m2" });
  });

  it("clôt la réponse en cours sur une erreur de flux et la mappe (quota)", () => {
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, {
      type: "FLUX_ERREUR",
      sessionId: "sess-1",
      message: "quota de génération atteint — réessayez un peu plus tard",
    });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ enCours: false, erreur: "quota" });
  });

  it("distingue réponse partielle et refus sec par le flag du contrat, jamais par le texte", () => {
    // Réponse partielle : texte vérifié diffusé avant un échec final.
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, {
      type: "REPONSE_FINALE",
      sessionId: "sess-1",
      reponse: { ...REPONSE, refus: true, motif_refus: "insuffisant_modele", texte_partiel: true },
    });
    expect(dernierDe(etat, "sess-1")).toMatchObject({
      refus: true,
      textePartiel: true,
    });

    // Refus sec : le message de refus standard occupe aussi `texte` — il ne
    // doit PAS être marqué partiel (sinon la notice contredit le refus).
    etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, {
      type: "REPONSE_FINALE",
      sessionId: "sess-1",
      reponse: {
        ...REPONSE,
        reponse: "Je n'ai pas trouvé dans le corpus…",
        refus: true,
        motif_refus: "insuffisant_modele",
        texte_partiel: false,
      },
    });
    expect(dernierDe(etat, "sess-1")).toMatchObject({
      refus: true,
      textePartiel: false,
    });
  });

  it("route le flag sans_reponse (description vérifiée des passages consultés)", () => {
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, {
      type: "REPONSE_FINALE",
      sessionId: "sess-1",
      reponse: {
        ...REPONSE,
        reponse: "Les passages portent sur «le délai de trente jours» [1]…",
        refus: true,
        motif_refus: "insuffisant_modele",
        texte_partiel: false,
        sans_reponse: true,
      },
    });
    expect(dernierDe(etat, "sess-1")).toMatchObject({
      refus: true,
      sansReponse: true,
      textePartiel: false,
    });
  });

  it("reconnaît le mot quota sans tenir compte de la casse", () => {
    expect(cleErreurFlux("Quota atteint")).toBe("quota");
    expect(cleErreurFlux("service de recherche indisponible")).toBe("indisponible");
  });

  it("restaure l'historique sans identifiants ni métadonnées internes", () => {
    const etat = reducteurChat(ETAT_INITIAL, {
      type: "HISTORIQUE_RESTAURE",
      sessionId: "s9",
      messages: messagesDepuisHistorique([
        { role: "user", contenu: "Bonjour", refus: false },
        { role: "assistant", contenu: "Réponse [1].", refus: true },
      ]),
    });
    expect(etat.sessionId).toBe("s9");
    expect(etat.fils["s9"]).toEqual([
      { role: "question", texte: "Bonjour" },
      {
        role: "reponse",
        id: null,
        texte: "Réponse [1].",
        enCours: false,
        etape: null,
        refus: true,
        motif: null,
        sources: [],
        avertissements: [],
        repliInterlingue: false,
        textePartiel: false,
        sansReponse: false,
        erreur: null,
        dureeMs: null,
      },
    ]);
  });

  it("restaure sources, motif et marqueurs — la discussion se rend à l'identique après rafraîchissement", () => {
    const etat = reducteurChat(ETAT_INITIAL, {
      type: "HISTORIQUE_RESTAURE",
      sessionId: "s10",
      messages: messagesDepuisHistorique([
        { role: "user", contenu: "Question", refus: false },
        {
          role: "assistant",
          contenu: "Les passages portent sur «le délai» [1]…",
          refus: true,
          sources: [SOURCE],
          motif_refus: "insuffisant_modele",
          avertissements: ["Un avertissement."],
          sans_reponse: true,
        },
      ]),
    });
    expect(dernierDe(etat, "s10")).toEqual({
      role: "reponse",
      id: null,
      texte: "Les passages portent sur «le délai» [1]…",
      enCours: false,
      etape: null,
      refus: true,
      motif: "insuffisant_modele",
      sources: [SOURCE],
      avertissements: ["Un avertissement."],
      repliInterlingue: false,
      textePartiel: false,
      sansReponse: true,
      erreur: null,
      dureeMs: null,
    });
  });

  it("restaurer une discussion ne casse pas un flux en cours", () => {
    let etat = etatAvecQuestionEnCours("sess-1");
    etat = reducteurChat(etat, { type: "FRAGMENT", sessionId: "sess-1", texte: "Partie [1]." });
    // L'utilisateur va voir une autre discussion puis revient : le chargement
    // serveur (qui ne porte que ce qui est persisté) ne doit pas écraser le
    // fil mémoire contenant la réponse en cours.
    etat = reducteurChat(etat, {
      type: "HISTORIQUE_RESTAURE",
      sessionId: "sess-1",
      messages: messagesDepuisHistorique([
        { role: "user", contenu: "Quelle est la procédure ?", refus: false },
      ]),
    });
    expect(etat.fils["sess-1"]).toHaveLength(2);
    expect(dernierDe(etat, "sess-1")).toMatchObject({ texte: "Partie [1].", enCours: true });
    // Et le flux continue de l'alimenter.
    etat = reducteurChat(etat, { type: "FRAGMENT", sessionId: "sess-1", texte: " Suite." });
    expect(dernierDe(etat, "sess-1")).toMatchObject({ texte: "Partie [1]. Suite." });
  });
});

describe("index des discussions (côté navigateur)", () => {
  it("indexe la discussion à la première question, avec titre tronqué", () => {
    const etat = etatAvecQuestionEnCours();
    expect(etat.sessions).toEqual([
      { id: "sess-1", titre: "Quelle est la procédure ?", majLe: HORODATAGE },
    ]);
  });

  it("ramène la discussion active en tête à chaque question, titre conservé", () => {
    let etat = reducteurChat(ETAT_INITIAL, {
      type: "SESSIONS_RESTAUREES",
      sessions: [
        { id: "ancienne", titre: "Ancienne", majLe: "2026-10-01T08:00:00Z" },
        { id: "sess-1", titre: "Première", majLe: "2026-10-03T08:00:00Z" },
      ],
    });
    etat = reducteurChat(etat, { type: "SESSION_OUVERTE", sessionId: "sess-1" });
    etat = reducteurChat(etat, {
      type: "QUESTION_ENVOYEE",
      sessionId: "sess-1",
      texte: "Deuxième question",
      horodatage: HORODATAGE,
    });
    expect(etat.sessions.map((session) => session.id)).toEqual(["sess-1", "ancienne"]);
    expect(etat.sessions[0].titre).toBe("Première");
  });

  it("tronque les titres longs à une frontière de mot", () => {
    expect(tronquerTitre("Court")).toBe("Court");
    const long = "une question très longue qui dépasse largement la taille maximale admise ici";
    const titre = tronquerTitre(long);
    expect(titre.endsWith("…")).toBe(true);
    expect(titre.length).toBeLessThanOrEqual(49);
  });

  it("nouvelle discussion ne touche ni aux fils ni à l'index", () => {
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, { type: "NOUVELLE_DISCUSSION" });
    expect(etat.sessionId).toBeNull();
    expect(etat.fils["sess-1"]).toHaveLength(2); // conservé
    expect(etat.sessions).toHaveLength(1);
    expect(etat.fils[CLE_NOUVELLE]).toBeUndefined();
  });

  it("la suppression retire le fil et l'index ; si active, la vue se vide", () => {
    let etat = etatAvecQuestionEnCours();
    etat = reducteurChat(etat, { type: "DISCUSSION_SUPPRIMEE", sessionId: "sess-autre" });
    expect(etat.fils["sess-1"]).toHaveLength(2); // inactive : intacte

    etat = reducteurChat(etat, { type: "DISCUSSION_SUPPRIMEE", sessionId: "sess-1" });
    expect(etat.sessions).toEqual([]);
    expect(etat.fils["sess-1"]).toBeUndefined();
    expect(etat.sessionId).toBeNull();
  });

  it("charge l'index restauré depuis le navigateur", () => {
    const etat = reducteurChat(ETAT_INITIAL, {
      type: "SESSIONS_RESTAUREES",
      sessions: [{ id: "s1", titre: null, majLe: HORODATAGE }],
    });
    expect(etat.sessions).toEqual([{ id: "s1", titre: null, majLe: HORODATAGE }]);
  });

  it("une erreur de transport hors fil passe par la bannière de la vue", () => {
    const etat = reducteurChat(ETAT_INITIAL, { type: "TRANSPORT_ERREUR" });
    expect(etat.erreur).toBe("reseau");
  });
});
