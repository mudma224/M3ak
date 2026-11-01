/** Durée lisible et compacte — chronomètre du flux en attente et durée
 * finale d'une réponse. Les chiffres restent occidentaux (usage marocain). */

import type { Dictionnaire } from "@/dico";

export function formaterDuree(dureeMs: number, dico: Dictionnaire): string {
  const secondes = Math.max(0, Math.round(dureeMs / 1000));
  if (secondes < 60) return `${secondes} ${dico.temps.seconde}`;
  const minutes = Math.floor(secondes / 60);
  const reste = String(secondes % 60).padStart(2, "0");
  return `${minutes} ${dico.temps.minute} ${reste} ${dico.temps.seconde}`;
}
