import type { Metadata } from "next";
import { notFound } from "next/navigation";

import EnTete from "@/components/entete";
import PiedDePage from "@/components/pied-de-page";
import { baliseLangue, dictionnaire, dirLangue, estLangue, type Langue } from "@/dico";

// Polices auto-hébergées via @fontsource (paquets npm versionnés) : aucune
// requête vers fonts.googleapis.com au build ni à l'exécution — next/font/google
// exigeait une connexion à Google à chaque build (échec hors proxy, conteneur compris).
import "@fontsource-variable/inter";
import "@fontsource/ibm-plex-sans-arabic/400.css";
import "@fontsource/ibm-plex-sans-arabic/500.css";
import "@fontsource/ibm-plex-sans-arabic/600.css";
import "@fontsource/ibm-plex-sans-arabic/700.css";

import "../globals.css";

export function generateStaticParams() {
  return [{ lang: "fr" }, { lang: "ar" }];
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ lang: string }>;
}): Promise<Metadata> {
  const { lang } = await params;
  if (!estLangue(lang)) return {};
  const dico = dictionnaire(lang);
  return {
    title: dico.meta.titre,
    description: dico.meta.description,
  };
}

export default async function RacineLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!estLangue(lang)) notFound();
  const langue: Langue = lang;
  const dico = dictionnaire(langue);

  return (
    // dir natif : toute la mise en page se reflète en RTL sans variante
    // dédiée (utilitaires logiques ms/me/ps/pe/text-start partout).
    <html lang={baliseLangue(langue)} dir={dirLangue(langue)}>
      {/* Coque à hauteur d'écran : la page ne défile pas — seuls le fil de
          discussion et la liste des discussions défilent en interne. */}
      <body className="flex h-dvh flex-col overflow-hidden bg-white text-neutral-800 antialiased">
        <a
          href="#contenu"
          className="sr-only focus:not-sr-only focus:absolute focus:start-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-maroc-700 focus:px-3 focus:py-2 focus:text-white"
        >
          {dico.a11y.allerContenu}
        </a>
        <EnTete dico={dico} />
        <main id="contenu" className="flex min-h-0 flex-1 flex-col">
          {children}
        </main>
        <PiedDePage dico={dico} />
      </body>
    </html>
  );
}
