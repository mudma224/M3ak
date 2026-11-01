/** Réducteur de l'état du chat — pur et testable sans rendu.
 *
 * Machine à états du flux : question posée → étapes (recherche, generation)
 * → sources → fragments de la réponse VÉRIFIÉE → réponse finale (ou refus,
 * ou erreur). Les événements d'une version future du contrat sont ignorés
 * proprement.
 *
 * Chaque discussion possède son FIL (`fils`, clé = sessionId, clé ""
 * pour la discussion pas encore créée) : les événements sont portés par
 * leur sessionId, donc un flux continue de remplir SA discussion même si
 * l'utilisateur en affiche une autre — le moteur vit dans le layout, il
 * survit aussi au changement de langue.
 *
 * L'index des discussions vit côté navigateur (accès sans compte) : le
 * service n'expose jamais la liste des sessions de personne.
 */

import type { ReponseQuestion, SourceCitee } from "@/lib/api/conversation";

export type EtapeFlux = "recherche" | "generation";

/** Clé i18n d'une erreur d'interface (jamais le texte brut du service). */
export type CleErreur = "reseau" | "quota" | "indisponible";

/** Clé du fil de la discussion pas encore créée côté service. */
export const CLE_NOUVELLE = "";

export interface MessageQuestion {
  role: "question";
  texte: string;
}

export interface MessageReponse {
  role: "reponse";
  /** Identifiant du message côté service — null dans l'historique restauré
   * (le contrat d'historique n'expose pas les identifiants). */
  id: string | null;
  texte: string;
  enCours: boolean;
  etape: EtapeFlux | null;
  refus: boolean;
  motif: string | null;
  sources: SourceCitee[];
  avertissements: string[];
  repliInterlingue: boolean;
  /** Vrai si `texte` porte un contenu vérifié diffusé avant un échec final
   * (réponse partielle) — porté par le contrat (`texte_partiel`) et jamais
   * déduit du contenu : un refus sec porte LUI AUSSI du texte (le message
   * de refus standard). */
  textePartiel: boolean;
  /** Vrai si `texte` est la description VÉRIFIÉE des passages consultés
   * quand aucun ne répond (réponse d'orientation, contrat `sans_reponse`)
   * — affichée comme un contenu normal, sans style d'erreur. */
  sansReponse: boolean;
  /** Incident du flux pour CETTE réponse (quota, indisponible) — null si OK. */
  erreur: CleErreur | null;
  /** Durée totale du pipeline (portée par l'événement fin) — null tant que
   * la réponse n'est pas close, et dans l'historique restauré. */
  dureeMs: number | null;
}

export type MessageChat = MessageQuestion | MessageReponse;

/** Une discussion de l'index local — le titre dérive de la première
 * question (null tant qu'aucune question n'a été posée). */
export interface SessionResumee {
  id: string;
  titre: string | null;
  majLe: string;
}

export interface EtatChat {
  sessions: SessionResumee[];
  /** Discussion affichée — null = nouvelle discussion (clé CLE_NOUVELLE). */
  sessionId: string | null;
  fils: Record<string, MessageChat[]>;
  /** Erreur d'infrastructure hors fil (échec de création de session). */
  erreur: CleErreur | null;
}

export const ETAT_INITIAL: EtatChat = {
  sessions: [],
  sessionId: null,
  fils: {},
  erreur: null,
};

export type ActionChat =
  | { type: "SESSIONS_RESTAUREES"; sessions: SessionResumee[] }
  | { type: "SESSION_OUVERTE"; sessionId: string }
  | { type: "HISTORIQUE_RESTAURE"; sessionId: string; messages: MessageChat[] }
  | { type: "NOUVELLE_DISCUSSION" }
  | { type: "DISCUSSION_SUPPRIMEE"; sessionId: string }
  | {
      type: "QUESTION_ENVOYEE";
      sessionId: string;
      texte: string;
      horodatage: string;
    }
  | { type: "ETAPE"; sessionId: string; etape: string }
  | { type: "SOURCES"; sessionId: string; sources: SourceCitee[] }
  | { type: "FRAGMENT"; sessionId: string; texte: string }
  | { type: "REPONSE_FINALE"; sessionId: string; reponse: ReponseQuestion }
  | { type: "FLUX_ERREUR"; sessionId: string; message: string }
  | { type: "TRANSPORT_ERREUR" }
  | { type: "ERREUR_EFFACEE" };

/** Mappe un message d'incident du flux sur une clé i18n — le texte du
 * service n'est jamais affiché tel quel (interface bilingue). */
export function cleErreurFlux(message: string): CleErreur {
  if (message.toLowerCase().includes("quota")) return "quota";
  return "indisponible";
}

/** Titre de discussion : première question ramenée à une frontière de mot. */
export function tronquerTitre(texte: string, max = 48): string {
  const nettoye = texte.replace(/\s+/g, " ").trim();
  if (nettoye.length <= max) return nettoye;
  const coupe = nettoye.lastIndexOf(" ", max);
  return `${nettoye.slice(0, coupe > 20 ? coupe : max)}…`;
}

function rafraichirIndex(
  sessions: SessionResumee[],
  sessionId: string,
  premiereQuestion: string,
  horodatage: string,
): SessionResumee[] {
  const existante = sessions.find((session) => session.id === sessionId);
  const entree: SessionResumee = {
    id: sessionId,
    titre: existante?.titre ?? tronquerTitre(premiereQuestion),
    majLe: horodatage,
  };
  return [entree, ...sessions.filter((session) => session.id !== sessionId)];
}

function filsDe(etat: EtatChat, sessionId: string): MessageChat[] {
  return etat.fils[sessionId] ?? [];
}

function dernierMessageReponse(messages: MessageChat[]): MessageReponse | null {
  const dernier = messages[messages.length - 1];
  return dernier && dernier.role === "reponse" ? dernier : null;
}

/** Remplace le dernier message (réponse) du fil visé. */
function avecDernierReponse(
  etat: EtatChat,
  sessionId: string,
  transformation: (message: MessageReponse) => MessageReponse,
): EtatChat {
  const messages = filsDe(etat, sessionId);
  const dernier = dernierMessageReponse(messages);
  if (!dernier) return etat;
  return {
    ...etat,
    fils: {
      ...etat.fils,
      [sessionId]: [...messages.slice(0, -1), transformation(dernier)],
    },
  };
}

export function reducteurChat(etat: EtatChat, action: ActionChat): EtatChat {
  switch (action.type) {
    case "SESSIONS_RESTAUREES":
      return { ...etat, sessions: action.sessions };

    case "SESSION_OUVERTE":
      return { ...etat, sessionId: action.sessionId, erreur: null };

    case "HISTORIQUE_RESTAURE": {
      // Une discussion dont une réponse est EN COURS garde son fil mémoire
      // (plus récent que l'historique serveur, qui ne porte que ce qui est
      // déjà persisté) : revenir sur la discussion — ou changer de langue —
      // ne casse jamais le flux qui coule.
      const existant = etat.fils[action.sessionId];
      const reponseEnCours = dernierMessageReponse(existant ?? [])?.enCours;
      return {
        ...etat,
        sessionId: action.sessionId,
        erreur: null,
        fils: reponseEnCours
          ? etat.fils
          : { ...etat.fils, [action.sessionId]: action.messages },
      };
    }

    case "NOUVELLE_DISCUSSION":
      // Le fil affiché est quitté mais conservé ; la session ne sera
      // créée côté service qu'à la première question (sobriété).
      return { ...etat, sessionId: null, erreur: null };

    case "DISCUSSION_SUPPRIMEE": {
      const fils = { ...etat.fils };
      delete fils[action.sessionId];
      return {
        ...etat,
        sessions: etat.sessions.filter(
          (session) => session.id !== action.sessionId,
        ),
        fils,
        sessionId:
          etat.sessionId === action.sessionId ? null : etat.sessionId,
      };
    }

    case "QUESTION_ENVOYEE": {
      const messages = filsDe(etat, action.sessionId);
      return {
        ...etat,
        sessions: rafraichirIndex(
          etat.sessions,
          action.sessionId,
          action.texte,
          action.horodatage,
        ),
        fils: {
          ...etat.fils,
          [action.sessionId]: [
            ...messages,
            { role: "question", texte: action.texte },
            {
              role: "reponse",
              id: null,
              texte: "",
              enCours: true,
              etape: null,
              refus: false,
              motif: null,
              sources: [],
              avertissements: [],
              repliInterlingue: false,
              textePartiel: false,
              sansReponse: false,
              erreur: null,
              dureeMs: null,
            },
          ],
        },
      };
    }

    case "ETAPE": {
      // Une étape inconnue (version future) n'est pas une erreur : ignorée.
      if (action.etape !== "recherche" && action.etape !== "generation") {
        return etat;
      }
      const etape = action.etape as EtapeFlux;
      return avecDernierReponse(etat, action.sessionId, (message) => ({
        ...message,
        etape,
      }));
    }

    case "SOURCES":
      return avecDernierReponse(etat, action.sessionId, (message) => ({
        ...message,
        sources: action.sources,
      }));

    case "FRAGMENT":
      return avecDernierReponse(etat, action.sessionId, (message) => ({
        ...message,
        texte: message.texte + action.texte,
      }));

    case "REPONSE_FINALE": {
      const reponse = action.reponse;
      const messages = filsDe(etat, action.sessionId);
      const dernier = dernierMessageReponse(messages);
      const sources =
        reponse.sources.length > 0 ? reponse.sources : (dernier?.sources ?? []);
      return avecDernierReponse(etat, action.sessionId, () => ({
        role: "reponse",
        id: reponse.message_id,
        texte: reponse.reponse,
        enCours: false,
        etape: null,
        refus: reponse.refus,
        motif: reponse.motif_refus,
        sources,
        avertissements: reponse.avertissements,
        repliInterlingue: reponse.repli_interlingue,
        textePartiel: reponse.texte_partiel,
        sansReponse: reponse.sans_reponse,
        erreur: null,
        dureeMs: reponse.duree_ms,
      }));
    }

    case "FLUX_ERREUR":
      return avecDernierReponse(etat, action.sessionId, (message) => ({
        ...message,
        enCours: false,
        etape: null,
        erreur: cleErreurFlux(action.message),
      }));

    case "TRANSPORT_ERREUR":
      // Hors fil (échec de création de session) : bannière de la vue active.
      return { ...etat, erreur: "reseau" };

    case "ERREUR_EFFACEE":
      return { ...etat, erreur: null };

    default:
      return etat;
  }
}

/** Reconstruit les messages affichables depuis l'historique du service —
 * sources citées, motif de refus et marqueurs compris (contrat 0.9.0) :
 * après un rafraîchissement, la discussion se rend à l'identique du flux
 * (consultation PDF, checklist de démarche, notices). Seuls les
 * identifiants de messages manquent : le signalement reste un geste du
 * message vivant. */
export function messagesDepuisHistorique(
  messages: Array<{
    role: "user" | "assistant";
    contenu: string;
    refus: boolean;
    sources?: SourceCitee[];
    motif_refus?: string | null;
    avertissements?: string[];
    repli_interlingue?: boolean;
    texte_partiel?: boolean;
    sans_reponse?: boolean;
  }>,
): MessageChat[] {
  return messages.map((message): MessageChat =>
    message.role === "user"
      ? { role: "question", texte: message.contenu }
      : {
          role: "reponse",
          id: null,
          texte: message.contenu,
          enCours: false,
          etape: null,
          refus: message.refus,
          motif: message.motif_refus ?? null,
          sources: message.sources ?? [],
          avertissements: message.avertissements ?? [],
          repliInterlingue: message.repli_interlingue ?? false,
          textePartiel: message.texte_partiel ?? false,
          sansReponse: message.sans_reponse ?? false,
          erreur: null,
          dureeMs: null,
        },
  );
}
