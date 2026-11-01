/** Proxy de CONSULTATION des PDF de la couche brute (phase 3, étape 3).
 *
 * Le navigateur ne voit jamais le stockage : seul ce proxy détient le
 * jeton interne du service corpus, et l'affichage se fait en canvas
 * (pdf.js) — aucun lien de téléchargement n'expose le fichier. Une
 * limitation de débit en mémoire (par adresse cliente) borne le raclage
 * en attendant la limitation Redis de la phase 5.
 */

import { request as requeteHttp } from "node:http";
import type { IncomingMessage } from "node:http";
import { Readable } from "node:stream";

import type { NextRequest } from "next/server";

const URL_CORPUS = process.env.URL_CORPUS ?? "http://127.0.0.1";
/** En dev, Traefik route par Host (cf. proxy conversation). */
const ENTETE_HOST = process.env.ENTETE_HOST_CORPUS ?? "";
const JETON_CORPUS = process.env.CORPUS_JETON_INTERNE ?? "";

const FENETRE_MS = 60_000;
const MAX_PAR_FENETRE = 30;
const frappes = new Map<string, number[]>();

function autorise(cle: string): boolean {
  const maintenant = Date.now();
  const recentes = (frappes.get(cle) ?? []).filter(
    (horodatage) => maintenant - horodatage < FENETRE_MS,
  );
  if (recentes.length >= MAX_PAR_FENETRE) {
    frappes.set(cle, recentes);
    return false;
  }
  recentes.push(maintenant);
  frappes.set(cle, recentes);
  return true;
}

function appeler(cible: URL, enteteHost: string): Promise<{
  statut: number;
  typeContenu: string | null;
  corps: ReadableStream<Uint8Array>;
}> {
  return new Promise((resoudre, rejeter) => {
    const entetes: Record<string, string> = { accept: "application/pdf,*/*" };
    if (enteteHost) entetes.host = enteteHost;
    if (JETON_CORPUS) entetes["x-internal-token"] = JETON_CORPUS;
    const requete = requeteHttp(cible, { method: "GET", headers: entetes }, (reponse: IncomingMessage) => {
      resoudre({
        statut: reponse.statusCode ?? 502,
        typeContenu: reponse.headers["content-type"] ?? null,
        corps: Readable.toWeb(reponse) as ReadableStream<Uint8Array>,
      });
    });
    requete.on("error", rejeter);
    requete.end();
  });
}

export async function GET(
  requete: NextRequest,
  contexte: { params: Promise<{ document_id: string }> },
): Promise<Response> {
  const { document_id } = await contexte.params;
  // Un identifiant stable ne porte pas de séparateur de chemin.
  if (!document_id || document_id.includes("/") || document_id.length > 200) {
    return Response.json({ detail: "identifiant invalide" }, { status: 400 });
  }
  const adresse =
    requete.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
  if (!autorise(adresse)) {
    return Response.json({ detail: "trop de requêtes" }, { status: 429 });
  }

  const recherche = "";
  try {
    const reponse = await appeler(
      new URL(
        `${URL_CORPUS}/internal/documents/${encodeURIComponent(document_id)}/pdf${recherche}`,
      ),
      ENTETE_HOST,
    );
    const entetes = new Headers();
    if (reponse.typeContenu) entetes.set("content-type", reponse.typeContenu);
    // Consultation uniquement : aucun hint de téléchargement, cache privé.
    entetes.set("cache-control", "private, no-store");
    entetes.set("x-content-type-options", "nosniff");
    return new Response(reponse.corps, { status: reponse.statut, headers: entetes });
  } catch {
    return Response.json(
      { detail: "service corpus injoignable" },
      { status: 502 },
    );
  }
}
