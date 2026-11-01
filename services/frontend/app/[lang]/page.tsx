import Chat from "@/components/chat/chat";
import { dictionnaire, estLangue, LANGUE_DEFAUT, type Langue } from "@/dico";

export default async function Page({
  params,
}: {
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  // Le layout valide déjà la langue ; le défaut n'arrive qu'au typage.
  const langue: Langue = estLangue(lang) ? lang : LANGUE_DEFAUT;
  return <Chat dico={dictionnaire(langue)} langue={langue} />;
}
