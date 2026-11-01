/** Le moteur du chat : un magasin externe minimal (useSyncExternalStore).
 *
 * Pourquoi hors React : l'arbre [lang] est REMONTÉ par Next au changement
 * de langue (/fr ↔ /ar) — un état détenu par un contexte de composant
 * serait perdu et les flux en cours s'évanouiraient. Un magasin au niveau
 * du module survit à tout remontage : chaque flux continue d'alimenter SA
 * discussion, quelle que soit la navigation (langue, discussions, recharg
 * ement partiel).
 *
 * Sans comptes, l'index des discussions vit dans le navigateur
 * (localStorage) : le service n'expose jamais la liste des sessions.
 */

import { useSyncExternalStore } from "react";

import {
  creerSession,
  envoyerQuestionFlux,
  ErreurAPI,
  recupererHistorique,
  supprimerSession,
} from "@/lib/api/conversation";
import {
  ETAT_INITIAL,
  messagesDepuisHistorique,
  reducteurChat,
  type ActionChat,
  type EtatChat,
  type SessionResumee,
} from "@/lib/chat/reducteur";

export const CLE_SESSION = "m3ak-session";
export const CLE_SESSIONS = "m3ak-sessions";

/** Ce que voient les composants : l'état du chat et le brouillon du composeur. */
export interface MoteurChat {
  etat: EtatChat;
  brouillon: string;
}

let etat: EtatChat = ETAT_INITIAL;
let brouillon = "";

/** Capture immuable — useSyncExternalStore exige une référence stable
 * entre deux notifications (l'égalité référentielle évite les rendus nuls). */
let capture: MoteurChat = { etat, brouillon };
const ecouteurs = new Set<() => void>();

function publier() {
  capture = { etat, brouillon };
  for (const ecouteur of ecouteurs) ecouteur();
}

function dispatch(action: ActionChat) {
  etat = reducteurChat(etat, action);
  persistre();
  publier();
}

export function souscrire(ecouteur: () => void): () => void {
  ecouteurs.add(ecouteur);
  return () => {
    ecouteurs.delete(ecouteur);
  };
}

export function moteurActuel(): MoteurChat {
  return capture;
}

/** Instantané servi pendant le rendu serveur (aucun stockage local là-bas). */
const CAPTURE_INITIALE: MoteurChat = { etat: ETAT_INITIAL, brouillon: "" };

export function moteurServeur(): MoteurChat {
  return CAPTURE_INITIALE;
}

/** Le hook unique des composants — même API que l'ancien contexte. */
export function useMoteurChat(): MoteurChat {
  return useSyncExternalStore(souscrire, moteurActuel, moteurServeur);
}

// ── Brouillon ──────────────────────────────────────────────────────────────

export function definirBrouillon(valeur: string) {
  brouillon = valeur;
  publier();
}

// ── Actions ────────────────────────────────────────────────────────────────

export async function envoyer() {
  const question = brouillon.trim();
  const filActif = etat.fils[etat.sessionId ?? ""] ?? [];
  const dernierFil = filActif[filActif.length - 1];
  const dejaEnCours =
    dernierFil !== undefined && dernierFil.role === "reponse" && dernierFil.enCours;
  if (!question || dejaEnCours) return;

  let sessionId = etat.sessionId;
  if (!sessionId) {
    try {
      const session = await creerSession();
      sessionId = session.session_id;
    } catch {
      dispatch({ type: "TRANSPORT_ERREUR" });
      return;
    }
  }
  dispatch({ type: "SESSION_OUVERTE", sessionId });
  dispatch({
    type: "QUESTION_ENVOYEE",
    sessionId,
    texte: question,
    horodatage: new Date().toISOString(),
  });
  brouillon = "";
  publier();
  try {
    await envoyerQuestionFlux(sessionId, question, {
      statut: (etape) => dispatch({ type: "ETAPE", sessionId, etape }),
      sources: (sources) => dispatch({ type: "SOURCES", sessionId, sources }),
      fragment: (texte) => dispatch({ type: "FRAGMENT", sessionId, texte }),
      fin: (reponse) => dispatch({ type: "REPONSE_FINALE", sessionId, reponse }),
      erreur: (message) => dispatch({ type: "FLUX_ERREUR", sessionId, message }),
    });
  } catch {
    dispatch({ type: "FLUX_ERREUR", sessionId, message: "transport" });
  }
}

export function nouvelleDiscussion() {
  dispatch({ type: "NOUVELLE_DISCUSSION" });
  brouillon = "";
  publier();
}

export async function ouvrirDiscussion(sessionId: string) {
  if (sessionId === etat.sessionId) return;
  try {
    const historique = await recupererHistorique(sessionId);
    dispatch({
      type: "HISTORIQUE_RESTAURE",
      sessionId: historique.session_id,
      messages: messagesDepuisHistorique(historique.messages),
    });
  } catch (erreur) {
    if (erreur instanceof ErreurAPI && erreur.statut === 404) {
      dispatch({ type: "DISCUSSION_SUPPRIMEE", sessionId });
    }
  }
}

export async function supprimerDiscussion(sessionId: string) {
  try {
    await supprimerSession(sessionId);
  } catch (erreur) {
    // 404 : déjà supprimée côté service — l'oubli local reste juste.
    if (!(erreur instanceof ErreurAPI && erreur.statut === 404)) return;
  }
  dispatch({ type: "DISCUSSION_SUPPRIMEE", sessionId });
}

// ── Restauration et reflet du stockage local ───────────────────────────────

let restauree = false;

/** À appeler au montage (côté navigateur uniquement) : recharge l'index des
 * discussions et la conversation active. Idempotent — le remontage du
 * composant au changement de langue ne rejoue ni la restauration ni le
 * chargement, donc ne casse jamais un flux en cours. */
export function restaurerSiNecessaire() {
  if (restauree || typeof window === "undefined") return;
  restauree = true;
  dispatch({ type: "SESSIONS_RESTAUREES", sessions: lireSessions() });
  const sessionId = window.localStorage.getItem(CLE_SESSION);
  if (sessionId) void ouvrirDiscussion(sessionId);
}

function lireSessions(): SessionResumee[] {
  try {
    const brut = window.localStorage.getItem(CLE_SESSIONS);
    if (!brut) return [];
    const valeur: unknown = JSON.parse(brut);
    if (!Array.isArray(valeur)) return [];
    return valeur.filter(
      (entree): entree is SessionResumee =>
        typeof entree === "object" &&
        entree !== null &&
        typeof (entree as SessionResumee).id === "string",
    );
  } catch {
    return [];
  }
}

/** L'état est la source de vérité ; le stockage local n'en est que le reflet. */
function persistre() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CLE_SESSIONS, JSON.stringify(etat.sessions));
  if (etat.sessionId) window.localStorage.setItem(CLE_SESSION, etat.sessionId);
  else window.localStorage.removeItem(CLE_SESSION);
}
