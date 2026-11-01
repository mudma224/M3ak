import SelecteurLangue from "@/components/selecteur-langue";
import type { Dictionnaire } from "@/dico";

export default function EnTete({ dico }: { dico: Dictionnaire }) {
  return (
    <header className="shrink-0 border-b border-neutral-200 bg-white">
      {/* Pleine largeur : la marque s'ancre au bord logique du début et le
          sélecteur de langue au bord de fin — marge intérieure progressive
          (mobile-first), jamais collée au bord sur grand écran. */}
      <div className="flex w-full items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-10">
        <div className="flex items-baseline gap-3">
          <span className="text-xl font-bold tracking-tight text-maroc-700">
            {dico.marque.nom}
          </span>
          <span className="hidden text-sm text-neutral-500 sm:inline">
            {dico.marque.slogan}
          </span>
        </div>
        <SelecteurLangue cible={dico.langue.vers} libelle={dico.langue.autre} />
      </div>
    </header>
  );
}
