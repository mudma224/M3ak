/** Client de l'API conversation — typé sur le contrat m3ak-contracts v0.7.0.
 *
 * Isolé de l'interface : composants et tests consomment uniquement ces
 * fonctions et types. Les noms de champs suivent le filaire (snake_case,
 * miroir des schémas Pydantic) pour la traçabilité avec le contrat.
 *
 * Tout passe par le proxy BFF même origine (/api/conversation → service
 * conversation) : pas de CORS, une seule origine exposée au navigateur.
 */

import { blocsSSE } from "./sse";

export const CHEMIN_API = "/api/conversation";

export type LangueEchange = "ar" | "fr";

export type FamilleSource = "segment" | "fiche" | "passage";

/** Miroir du contrat SourceCitee — la famille porte la hiérarchie des
 * sources à l'affichage. */
export interface SourceCitee {
  unite_id: string;
  famille: FamilleSource;
  document_id: string;
  titre: string;
  source: string;
  collection: string;
  langue: string;
  score: number;
}

/** Miroir du contrat ReponseQuestion — réponse vérifiée ou refus. */
export interface ReponseQuestion {
  session_id: string;
  message_id: string;
  reponse: string;
  langue_question: LangueEchange;
  refus: boolean;
  motif_refus: string | null;
  sources: SourceCitee[];
  citations_verifiees: boolean;
  citations_rejetees: number;
  avertissements: string[];
  repli_interlingue: boolean;
  texte_partiel: boolean;
  sans_reponse: boolean;
  score_meilleur: number | null;
  fournisseur: string | null;
  modele: string | null;
  latence_generation_ms: number;
  duree_ms: number;
}

export interface ReponseSession {
  session_id: string;
  cree_le: string;
}

export interface MessageHistorique {
  role: "user" | "assistant";
  contenu: string;
  langue: LangueEchange | null;
  cree_le: string;
  refus: boolean;
  signale: boolean;
  sources: SourceCitee[];
  motif_refus: string | null;
  avertissements: string[];
  repli_interlingue: boolean;
  texte_partiel: boolean;
  sans_reponse: boolean;
}

export interface ReponseHistorique {
  session_id: string;
  messages: MessageHistorique[];
}

/** Miroir du contrat EvenementConversation (flux SSE). */
export type EvenementConversation =
  | { type: "statut"; statut: string }
  | { type: "sources"; sources: SourceCitee[] }
  | { type: "fragment"; texte: string }
  | { type: "fin"; reponse: ReponseQuestion }
  | { type: "erreur"; erreur: string };

/** Erreur d'appel — statut HTTP conservé pour le traitement (404 session
 * inconnue, 429 quota, 502 fournisseur…). */
export class ErreurAPI extends Error {
  constructor(
    public readonly statut: number,
    message: string,
  ) {
    super(message);
    this.name = "ErreurAPI";
  }
}

async function corpsOuErreur(reponse: Response): Promise<Response> {
  if (reponse.ok) return reponse;
  const charge: unknown = await reponse.json().catch(() => null);
  const detail =
    typeof charge === "object" && charge !== null && "detail" in charge
      ? String((charge as { detail: unknown }).detail)
      : `HTTP ${reponse.status}`;
  throw new ErreurAPI(reponse.status, detail);
}

/** Ouvre une session anonyme (sans compte). */
export async function creerSession(): Promise<ReponseSession> {
  const reponse = await fetch(`${CHEMIN_API}/sessions`, { method: "POST" });
  return (await (await corpsOuErreur(reponse)).json()) as ReponseSession;
}

/** Historique d'une session (404 si inconnue — session expirée côté service). */
export async function recupererHistorique(
  sessionId: string,
): Promise<ReponseHistorique> {
  const reponse = await fetch(
    `${CHEMIN_API}/sessions/${encodeURIComponent(sessionId)}`,
  );
  return (await (await corpsOuErreur(reponse)).json()) as ReponseHistorique;
}

/** Supprime définitivement la session et ses messages (204). */
export async function supprimerSession(sessionId: string): Promise<void> {
  const reponse = await fetch(
    `${CHEMIN_API}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  await corpsOuErreur(reponse);
}

/** Signale une réponse (contrôle qualité humain). */
export async function signalerReponse(
  messageId: string,
  motif: string,
): Promise<void> {
  const reponse = await fetch(
    `${CHEMIN_API}/messages/${encodeURIComponent(messageId)}/signaler`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ motif }),
    },
  );
  await corpsOuErreur(reponse);
}

/** Récepteur des événements du flux — tous optionnels, l'ordre du contrat
 * est : statut(recherche) → statut(generation) → sources → fragment* → fin. */
export interface EcouteurFlux {
  statut?(etape: string): void;
  sources?(sources: SourceCitee[]): void;
  fragment?(texte: string): void;
  fin?(reponse: ReponseQuestion): void;
  erreur?(message: string): void;
}

/** Pose une question en mode flux SSE et répartit les événements sur
 * l'écouteur. Lève (ErreurAPI ou TypeError réseau) avant l'ouverture du
 * flux ; les incidents en cours de flux arrivent par `erreur`. */
export async function envoyerQuestionFlux(
  sessionId: string,
  question: string,
  ecouteur: EcouteurFlux,
): Promise<void> {
  const reponse = await fetch(
    `${CHEMIN_API}/sessions/${encodeURIComponent(sessionId)}/messages/flux`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    },
  );
  const ok = await corpsOuErreur(reponse);
  if (!ok.body) throw new ErreurAPI(0, "flux vide");
  for await (const bloc of blocsSSE(ok.body)) {
    let charge: unknown;
    try {
      charge = JSON.parse(bloc.donnees);
    } catch {
      console.warn("événement SSE non JSON ignoré");
      continue;
    }
    const evenement = charge as EvenementConversation;
    switch (evenement.type) {
      case "statut":
        ecouteur.statut?.(evenement.statut ?? "");
        break;
      case "sources":
        ecouteur.sources?.(evenement.sources ?? []);
        break;
      case "fragment":
        ecouteur.fragment?.(evenement.texte ?? "");
        break;
      case "fin":
        if (evenement.reponse) ecouteur.fin?.(evenement.reponse);
        break;
      case "erreur":
        ecouteur.erreur?.(evenement.erreur ?? "");
        break;
      default:
        // Événement d'une version future du contrat : ignoré proprement.
        console.warn("type d'événement inconnu ignoré");
    }
  }
}
