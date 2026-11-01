"use client";

import { useEffect, useRef } from "react";

import type { Dictionnaire } from "@/dico";
import type { MessageChat } from "@/lib/chat/reducteur";

import MessageQuestion from "./message-question";
import MessageReponse from "./message-reponse";

interface Props {
  dico: Dictionnaire;
  messages: MessageChat[];
  surConsulter?: (document: { documentId: string; titre: string }) => void;
  surConsulterDemarche?: (document: { documentId: string; titre: string }) => void;
}

export default function Fil({
  dico,
  messages,
  surConsulter,
  surConsulterDemarche,
}: Props) {
  const finRef = useRef<HTMLLIElement | null>(null);
  // Les fragments recréent le dernier message : le fil suit la fin à chaque
  // ajout comme à chaque fragment.
  const dernier = messages.at(-1);
  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, dernier]);

  return (
    <ol aria-label={dico.fil.label} className="flex flex-col gap-5 py-6">
      {messages.map((message, index) => (
        <li key={index}>
          {message.role === "question" ? (
            <MessageQuestion dico={dico} message={message} />
          ) : (
            <MessageReponse
              dico={dico}
              message={message}
              surConsulter={surConsulter}
              surConsulterDemarche={surConsulterDemarche}
            />
          )}
        </li>
      ))}
      <li ref={finRef} aria-hidden="true" className="h-0 scroll-mb-28" />
    </ol>
  );
}
