import type { Dictionnaire } from "@/dico";

export default function PiedDePage({ dico }: { dico: Dictionnaire }) {
  return (
    <footer className="shrink-0 border-t border-neutral-200 bg-white">
      <div className="mx-auto w-full max-w-5xl px-4 py-4 text-center text-xs leading-relaxed text-neutral-500">
        <p>{dico.decharge}</p>
      </div>
    </footer>
  );
}
