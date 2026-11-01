"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";

import type { Dictionnaire } from "@/dico";
import { formaterDuree } from "@/lib/chat/duree";
import type {
  EtapeFlux,
  MessageReponse as MessageReponseType,
} from "@/lib/chat/reducteur";

import Avertissements from "./avertissements";
import AvisRepli from "./avis-repli";
import Signalement from "./signalement";
import Sources from "./sources";

interface Props {
  dico: Dictionnaire;
  message: MessageReponseType;
  surConsulter?: (document: { documentId: string; titre: string }) => void;
  surConsulterDemarche?: (document: { documentId: string; titre: string }) => void;
}

// ── Rendu Markdown avec renvois [n] cliquables ─────────────────────────────
// Le modèle écrit en Markdown (titres, listes, gras) : rendu par
// react-markdown ; un petit plugin rehype transforme les renvois [n] des
// nœuds texte en boutons (surcharge `button` ci-dessous), qui ouvrent et
// mettent en évidence la source correspondante.

interface NoeudHast {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: NoeudHast[];
}

const MARQUEUR_RENVOI = /\[\d{1,2}\]/;

function transformerTexte(noeud: NoeudHast): void {
  if (!noeud.children) return;
  const remplaces: NoeudHast[] = [];
  for (const enfant of noeud.children) {
    if (
      enfant.type === "text" &&
      enfant.value &&
      MARQUEUR_RENVOI.test(enfant.value)
    ) {
      for (const morceau of enfant.value.split(/(\[\d{1,2}\])/)) {
        const renvoi = morceau.match(/^\[(\d{1,2})\]$/);
        remplaces.push(
          renvoi
            ? {
                type: "element",
                tagName: "button",
                properties: {
                  type: "button",
                  className: ["renvoi-source"],
                  dataRenvoi: Number(renvoi[1]),
                },
                children: [{ type: "text", value: morceau }],
              }
            : { type: "text", value: morceau },
        );
      }
    } else {
      transformerTexte(enfant);
      remplaces.push(enfant);
    }
  }
  noeud.children = remplaces;
}

// Le typage « unified » attend ses propres nœuds : interop ciblée.
const remplacementRenvois = [
  () => (arbre: NoeudHast) => transformerTexte(arbre),
] as unknown as PluggableList;

// Les boutons renvoi n'ont pas de onClick propre : le clic est délégué au
// conteneur (un seul gestionnaire, aucune fermeture construite au rendu).
const COMPOSANTS_MARKDOWN: Components = {
  p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
  ul: ({ children }) => (
    <ul className="flex list-disc flex-col gap-1 ps-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="flex list-decimal flex-col gap-1 ps-5">{children}</ol>
  ),
  li: ({ children }) => <li className="text-start">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold text-neutral-900">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => (
    <h3 className="mt-2 text-base font-semibold text-neutral-900">{children}</h3>
  ),
  h2: ({ children }) => (
    <h3 className="mt-2 text-base font-semibold text-neutral-900">{children}</h3>
  ),
  h3: ({ children }) => (
    <h4 className="mt-2 text-sm font-semibold text-neutral-900">{children}</h4>
  ),
  h4: ({ children }) => (
    <h4 className="mt-2 text-sm font-semibold text-neutral-900">{children}</h4>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-s-2 border-neutral-300 ps-3 text-neutral-600">
      {children}
    </blockquote>
  ),
  button: ({ children, ...props }) => {
    const numero = Number((props as Record<string, unknown>)["data-renvoi"]);
    if (!Number.isFinite(numero)) {
      return <button type="button" {...props}>{children}</button>;
    }
    return (
      <button
        type="button"
        data-renvoi={numero}
        className="mx-0.5 align-super text-xs font-semibold text-maroc-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-maroc-600"
      >
        {children}
      </button>
    );
  },
};

/** Les refus liés à la COUVERTURE du corpus (aucune source, score
 * insuffisant) montrent ce que la plateforme couvre déjà — l'utilisateur
 * ne doit pas consumer des questions sur des domaines absents du corpus.
 * Les couleurs reprennent celles des natures de sources. */
function entreesCouverture(dico: Dictionnaire) {
  const couverture = dico.refus.couverture;
  return [
    {
      couleur: "bg-maroc-600",
      label: couverture.textes.label,
      domaines: couverture.textes.domaines,
    },
    {
      couleur: "bg-sky-500",
      label: couverture.procedures.label,
      domaines: couverture.procedures.domaines,
    },
    {
      couleur: "bg-violet-500",
      label: couverture.jurisprudence.label,
      domaines: couverture.jurisprudence.domaines,
    },
  ];
}

// Les motifs d'échec FINAL de génération closent en réponse partielle quand
// des phrases vérifiées ont été diffusées — le service le marque par le
// contrat (`texte_partiel`) ; l'affichage ne déduit JAMAIS ce cas du contenu,
// car un refus sec porte lui aussi du texte (le message de refus standard).

/** Une réponse de l'assistant : chronomètre tant que le flux coule, texte
 * VÉRIFIÉ rendu en Markdown avec renvois [n] cliquables, refus digne,
 * avertissements contextuels, sources numérotées comme les renvois,
 * durée et signalement. dir="auto" : la réponse française peut porter des
 * citations arabes et inversement. */
export default function MessageReponse({
  dico,
  message,
  surConsulter,
  surConsulterDemarche,
}: Props) {
  const panneauSourcesRef = useRef<HTMLDetailsElement | null>(null);
  const [copie, setCopie] = useState(false);

  /** Copie le texte BRUT (source Markdown, renvois [n] compris). */
  async function copier() {
    try {
      await navigator.clipboard.writeText(message.texte);
      setCopie(true);
      window.setTimeout(() => setCopie(false), 2000);
    } catch {
      // Presse-papiers indisponible (contexte non sécurisé) : sans effet.
    }
  }

  function allerALaSource(numero: number) {
    const panneau = panneauSourcesRef.current;
    if (!panneau) return;
    panneau.open = true;
    panneau
      .querySelector(`[data-source="${numero}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /** Délégation du clic pour les boutons renvoi [n] produits par Markdown. */
  function surClicTexte(evenement: React.MouseEvent<HTMLDivElement>) {
    const cible = (evenement.target as HTMLElement).closest(
      "button[data-renvoi]",
    );
    if (!cible) return;
    const numero = Number(cible.getAttribute("data-renvoi"));
    if (Number.isFinite(numero)) allerALaSource(numero);
  }

  const libelleMotif =
    message.motif && message.motif in dico.refus.motifs
      ? dico.refus.motifs[message.motif as keyof typeof dico.refus.motifs]
      : null;
  const montrerCouverture =
    message.refus &&
    (message.motif === "aucune_source" ||
      message.motif === "score_insuffisant" ||
      // Sources trouvées mais tangentes : le rappel du périmètre aide aussi.
      message.motif === "insuffisant_modele");

  const langueSources = message.sources[0]?.langue;

  /** Réponse partielle : des phrases VÉRIFIÉES ont été diffusées puis le
   * contrôle final a échoué (échappatoire du modèle, citation invérifiable,
   * troncature) — le texte vérifié reste servi (§7.1), encadré d'une notice
   * informative au lieu d'être remplacé par une alerte (demande du
   * porteur). Le contrat marque ce cas (`textePartiel`) : un refus sec
   * porte lui aussi du texte, on ne peut pas s'en servir comme indice. */
  const reponsePartielle =
    message.refus && message.textePartiel && message.texte.trim().length > 0;
  const noticePartielle =
    message.motif === "insuffisant_modele"
      ? dico.refus.partiel
      : dico.refus.partielIncomplet;

  function corpsMarkdown(texte: string) {
    return (
      <div
        dir="auto"
        onClick={surClicTexte}
        className="text-start text-sm leading-relaxed text-neutral-800 [&>*:first-child]:mt-0"
      >
        <ReactMarkdown
          rehypePlugins={remplacementRenvois}
          components={COMPOSANTS_MARKDOWN}
        >
          {texte}
        </ReactMarkdown>
      </div>
    );
  }

  return (
    <article className="flex flex-col gap-3" aria-busy={message.enCours}>
      {message.enCours && <StatutFlux dico={dico} etape={message.etape} />}

      {reponsePartielle ? (
        <>
          {corpsMarkdown(message.texte)}
          <div className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3">
            <p className="text-sm font-semibold text-amber-900">
              {noticePartielle.titre}
            </p>
            <p className="mt-1 text-sm leading-relaxed text-amber-900">
              {noticePartielle.texte}
            </p>
          </div>
        </>
      ) : message.refus && message.sansReponse ? (
        // Réponse d'orientation (SANS_REPONSE vérifiée) : la description
        // des passages est un CONTENU — elle s'affiche normalement, sans
        // style d'erreur ; le texte du modèle dit lui-même qu'aucun
        // passage ne répond. La note neutre cadre la lecture.
        <>
          {corpsMarkdown(message.texte)}
          <p className="flex items-start gap-2 text-xs leading-relaxed text-neutral-500">
            <span
              className="mt-1.5 size-1.5 shrink-0 rounded-full bg-neutral-400"
              aria-hidden="true"
            />
            <span>{dico.refus.sansReponse.note}</span>
          </p>
        </>
      ) : message.refus ? (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3">
          <p className="text-sm font-semibold text-amber-900">{dico.refus.titre}</p>
          {message.texte && (
            <p dir="auto" className="mt-1 whitespace-pre-wrap text-start text-sm text-amber-900">
              {message.texte}
            </p>
          )}
          {libelleMotif && <p className="mt-2 text-xs text-amber-800">{libelleMotif}</p>}
          {montrerCouverture && (
            <div className="mt-3 border-t border-amber-200 pt-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
                {dico.refus.couverture.titre}
              </p>
              <ul className="mt-2 flex flex-col gap-2">
                {entreesCouverture(dico).map((entree) => (
                  <li
                    key={entree.label}
                    className="flex gap-2 text-xs leading-relaxed text-amber-900"
                  >
                    <span
                      className={`mt-1 size-2 shrink-0 rounded-full ${entree.couleur}`}
                      aria-hidden="true"
                    />
                    <span>
                      <span className="font-semibold">{entree.label} : </span>
                      {entree.domaines.join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        message.texte && corpsMarkdown(message.texte)
      )}

      {message.erreur && (
        <p
          role="alert"
          className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {dico.erreurs[message.erreur]}
        </p>
      )}

      {!message.refus &&
        message.repliInterlingue &&
        (langueSources === "ar" || langueSources === "fr") && (
          <AvisRepli dico={dico} langueSources={langueSources} />
        )}

      {message.avertissements.length > 0 && (
        <Avertissements dico={dico} textes={message.avertissements} />
      )}

      {message.sources.length > 0 && (
        <Sources
          ref={panneauSourcesRef}
          dico={dico}
          sources={message.sources}
          // Sources émises puis jugées non répondantes SANS texte produit :
          // le titre le dit, pour ne pas suggérer que la réponse existe sous
          // prétexte que le score affiché est élevé. Dans une réponse
          // partielle, ces sources sont celles des extraits cités.
          sansReponse={
            message.refus &&
            message.motif === "insuffisant_modele" &&
            !reponsePartielle
          }
          surConsulter={surConsulter}
          surConsulterDemarche={surConsulterDemarche}
        />
      )}

      {!message.enCours && (message.texte || message.dureeMs !== null || message.id) && (
        <div className="flex items-center gap-3">
          {message.texte && (
            <button
              type="button"
              onClick={() => void copier()}
              title={dico.actions.copier}
              aria-label={dico.actions.copier}
              className="rounded-md p-1 text-neutral-400 transition hover:bg-neutral-100 hover:text-maroc-700 focus-visible:outline-2 focus-visible:outline-maroc-600"
            >
              {copie ? (
                <span className="text-xs font-medium text-maroc-700">
                  {dico.actions.copie}
                </span>
              ) : (
                <svg viewBox="0 0 20 20" className="size-4" fill="none" aria-hidden="true">
                  <rect
                    x="7"
                    y="7"
                    width="9"
                    height="11"
                    rx="1.5"
                    stroke="currentColor"
                    strokeWidth="1.4"
                  />
                  <path
                    d="M13 4.5H5.5A1.5 1.5 0 0 0 4 6v9"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                  />
                </svg>
              )}
            </button>
          )}
          {message.dureeMs !== null && message.dureeMs > 0 && (
            <p className="text-xs text-neutral-400">
              {formaterDuree(message.dureeMs, dico)}
            </p>
          )}
          {message.id && <Signalement dico={dico} messageId={message.id} />}
        </div>
      )}
    </article>
  );
}

/** Chronomètre « thinking » : l'attente est visible seconde par seconde,
 * depuis la recherche jusqu'au dernier fragment vérifié. */
function StatutFlux({ dico, etape }: { dico: Dictionnaire; etape: EtapeFlux | null }) {
  const [secondes, setSecondes] = useState(0);
  useEffect(() => {
    const horloge = setInterval(() => setSecondes((s) => s + 1), 1000);
    return () => clearInterval(horloge);
  }, []);
  const libelle =
    etape === "generation"
      ? dico.statuts.generation
      : etape === "recherche"
        ? dico.statuts.recherche
        : dico.fil.enCours;
  return (
    <p className="flex items-center gap-2 text-sm text-neutral-500" aria-live="polite">
      <span
        className="size-2 animate-pulse rounded-full bg-maroc-600"
        aria-hidden="true"
      />
      {libelle}
      <span className="tabular-nums text-neutral-400">
        · {formaterDuree(secondes * 1000, dico)}
      </span>
    </p>
  );
}
