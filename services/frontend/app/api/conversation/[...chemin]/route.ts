/** Proxy BFF — même origine vers le service conversation.
 *
 * Le navigateur ne parle qu'à cette origine (/api/conversation/**) : pas de
 * CORS, une seule surface exposée. Le service reste joignable en direct via
 * Traefik (conversation.m3ak.localhost) pour les clients futurs.
 *
 * Passage par node:http plutôt que fetch : fetch (undici) ignore l'en-tête
 * Host — en développement, le service est joignable uniquement via Traefik
 * qui route sur le Host (conversation.m3ak.localhost). node:http donne le
 * contrôle de l'en-tête et un flux de réponse naturel (indispensable au SSE).
 *
 * Sécurité : allowlist stricte des routes et méthodes transmises — ce
 * proxy n'est pas un relais ouvert.
 */

import { request as requeteHttp } from "node:http";
import { request as requeteHttps } from "node:https";
import type { IncomingMessage } from "node:http";
import { Readable } from "node:stream";

import type { NextRequest } from "next/server";

const URL_SERVICE = process.env.URL_CONVERSATION ?? "http://127.0.0.1";
/** En dev, Traefik route sur le Host — à définir si l'URL de service n'est
 * pas le nom routé (conteneur : inutile, http://conversation:8003 suffit). */
const ENTETE_HOST = process.env.ENTETE_HOST_CONVERSATION ?? "";

/** Routes publiques du service conversation transmissibles au navigateur. */
const ROUTES_AUTORISEES: Array<{ methode: string; motif: RegExp }> = [
  { methode: "POST", motif: /^\/sessions$/ },
  { methode: "GET", motif: /^\/sessions\/[^/]+$/ },
  { methode: "DELETE", motif: /^\/sessions\/[^/]+$/ },
  { methode: "POST", motif: /^\/sessions\/[^/]+\/messages$/ },
  { methode: "POST", motif: /^\/sessions\/[^/]+\/messages\/flux$/ },
  { methode: "POST", motif: /^\/messages\/[^/]+\/signaler$/ },
];

interface ReponseService {
  statut: number;
  typeContenu: string | null;
  corps: ReadableStream<Uint8Array>;
}

function appelerService(
  cible: URL,
  methode: string,
  corps: string | undefined,
): Promise<ReponseService> {
  return new Promise((resoudre, rejeter) => {
    const emettre = cible.protocol === "https:" ? requeteHttps : requeteHttp;
    const entetes: Record<string, string> = { accept: "*/*" };
    if (ENTETE_HOST) entetes.host = ENTETE_HOST;
    if (corps !== undefined) entetes["content-type"] = "application/json";
    const requete = emettre(
      cible,
      { method: methode, headers: entetes },
      (reponse: IncomingMessage) => {
        resoudre({
          statut: reponse.statusCode ?? 502,
          typeContenu: reponse.headers["content-type"] ?? null,
          corps: Readable.toWeb(reponse) as ReadableStream<Uint8Array>,
        });
      },
    );
    requete.on("error", rejeter);
    if (corps !== undefined) requete.write(corps);
    requete.end();
  });
}

async function aiguiller(
  requete: NextRequest,
  contexte: { params: Promise<{ chemin?: string[] }> },
): Promise<Response> {
  const { chemin } = await contexte.params;
  const cheminRelatif = `/${(chemin ?? []).join("/")}`;
  const autorisee = ROUTES_AUTORISEES.some(
    (route) => route.methode === requete.method && route.motif.test(cheminRelatif),
  );
  if (!autorisee) {
    return Response.json({ detail: "route inconnue" }, { status: 404 });
  }
  const recherche = new URL(requete.url).search;
  const corps =
    requete.method === "POST" ? await requete.text() : undefined;
  try {
    const reponse = await appelerService(
      new URL(`${URL_SERVICE}${cheminRelatif}${recherche}`),
      requete.method,
      corps,
    );
    const entetes = new Headers();
    if (reponse.typeContenu) entetes.set("content-type", reponse.typeContenu);
    // no-transform : aucun intermédiaire ne doit retamponner le SSE.
    entetes.set("cache-control", "no-cache, no-transform");
    entetes.set("x-accel-buffering", "no");
    return new Response(reponse.corps, {
      status: reponse.statut,
      headers: entetes,
    });
  } catch {
    return Response.json(
      { detail: "service conversation injoignable" },
      { status: 502 },
    );
  }
}

export {
  aiguiller as DELETE,
  aiguiller as GET,
  aiguiller as POST,
};
