import { describe, expect, it } from "vitest";

import { blocsSSE } from "@/lib/api/sse";

function flux(morceaux: string[]): ReadableStream<Uint8Array> {
  const encodeur = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controleur) {
      for (const morceau of morceaux) {
        controleur.enqueue(encodeur.encode(morceau));
      }
      controleur.close();
    },
  });
}

async function recolter(corps: ReadableStream<Uint8Array>) {
  const blocs: Array<{ evenement: string | null; donnees: string }> = [];
  for await (const bloc of blocsSSE(corps)) {
    blocs.push(bloc);
  }
  return blocs;
}

describe("parseur SSE", () => {
  it("analyse plusieurs événements dans un seul paquet", async () => {
    const blocs = await recolter(
      flux([
        'event: statut\ndata: {"type":"statut","statut":"recherche"}\n\n' +
          'event: fragment\ndata: {"type":"fragment","texte":"Bonjour"}\n\n',
      ]),
    );
    expect(blocs).toEqual([
      { evenement: "statut", donnees: '{"type":"statut","statut":"recherche"}' },
      { evenement: "fragment", donnees: '{"type":"fragment","texte":"Bonjour"}' },
    ]);
  });

  it("reconstitue un événement coupé entre deux paquets", async () => {
    const blocs = await recolter(
      flux(['event: fin\nda', 'ta: {"type":"fin"}\n', '\n']),
    );
    expect(blocs).toEqual([{ evenement: "fin", donnees: '{"type":"fin"}' }]);
  });

  it("normalise les fins de ligne CRLF", async () => {
    const blocs = await recolter(
      flux(['event: statut\r\ndata: {"a":1}\r\n\r\n']),
    );
    expect(blocs).toEqual([{ evenement: "statut", donnees: '{"a":1}' }]);
  });

  it("assemble les données multi-lignes", async () => {
    const blocs = await recolter(flux(["data: ligne1\ndata: ligne2\n\n"]));
    expect(blocs).toEqual([{ evenement: null, donnees: "ligne1\nligne2" }]);
  });

  it("ignore les commentaires et les blocs sans données", async () => {
    const blocs = await recolter(
      flux([": ping\n\nid: 12\nevent: statut\n\n"]),
    );
    // Un événement sans « data: » n'a rien à transmettre : ignoré.
    expect(blocs).toEqual([]);
  });

  it("ignore un bloc inachevé en fin de flux (sémantique SSE)", async () => {
    const blocs = await recolter(flux(['data: {"complet":true}\n\ndata: {"tr']));
    expect(blocs).toEqual([{ evenement: null, donnees: '{"complet":true}' }]);
  });
});
