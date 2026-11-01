/** Parseur SSE (text/event-stream) — flux un bloc à la fois.
 *
 * Conforme à la sémantique du format : un événement se termine par une
 * ligne vide ; les données multi-lignes sont jointes par \n ; les
 * commentaires (lignes « : ») et les champs inconnus (id:, retry:) sont
 * ignorés ; les blocs coupés entre deux paquets réseau sont reconstitués.
 */

export interface BlocSSE {
  /** Nom de l'événement (« event: ») — null si absent. */
  evenement: string | null;
  /** Charge utile (« data: », lignes jointes). */
  donnees: string;
}

/** Énumère les blocs SSE d'un flux binaire (corps d'une Response). */
export async function* blocsSSE(
  corps: ReadableStream<Uint8Array>,
): AsyncGenerator<BlocSSE> {
  const decodeur = new TextDecoder("utf-8");
  const lecteur = corps.getReader();
  let tampon = "";
  while (true) {
    const { value: valeur, done: fait } = await lecteur.read();
    if (fait) break;
    tampon += decodeur.decode(valeur, { stream: true });
    // CRLF et CR seuls sont des fins de ligne valides en SSE.
    tampon = tampon.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
    let fin = tampon.indexOf("\n\n");
    while (fin !== -1) {
      const bloc = tampon.slice(0, fin);
      tampon = tampon.slice(fin + 2);
      const analyse = analyserBloc(bloc);
      if (analyse) yield analyse;
      fin = tampon.indexOf("\n\n");
    }
  }
  // Un bloc inachevé en fin de flux est ignoré (sémantique SSE).
}

function analyserBloc(bloc: string): BlocSSE | null {
  let evenement: string | null = null;
  const lignesDonnees: string[] = [];
  for (const ligne of bloc.split("\n")) {
    if (ligne === "" || ligne.startsWith(":")) continue;
    if (ligne.startsWith("event:")) {
      evenement = ligne.slice("event:".length).trim();
    } else if (ligne.startsWith("data:")) {
      lignesDonnees.push(ligne.slice("data:".length).replace(/^ /, ""));
    }
    // id:, retry: et tout autre champ : ignorés.
  }
  if (lignesDonnees.length === 0) return null;
  return { evenement, donnees: lignesDonnees.join("\n") };
}
