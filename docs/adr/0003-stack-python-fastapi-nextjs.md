# ADR-0003 — Stack applicative : Python/FastAPI + Next.js, LLM Mistral

- **Statut** : accepté
- **Date** : septembre 2026
- **Décideurs** : porteur du projet

## Contexte

Le porteur est étudiant en filière intelligence artificielle et
cybersécurité. La plateforme combine un pipeline documentaire (PDF, OCR
arabe/français, segmentation), des services d'API, et un produit web
bilingue. Le budget est contraint (service gratuit) : le LLM démarre sur
l'offre gratuite de Mistral, avec un contrôle strict des quotas.

## Décision

1. **Backend et pipeline : Python 3.12 / FastAPI** — un seul langage pour
   les six services et la chaîne documentaire, l'écosystème le plus riche
   pour le RAG, l'OCR et le traitement de l'arabe.
2. **Frontend : Next.js (React)** — interface web responsive, PWA ensuite ;
   le RTL arabe est traité dès la conception des composants (BCP 47 :
   `ar-MA`, `fr-MA`).
3. **LLM : API Mistral (offre gratuite)** derrière une **interface
   d'abstraction** dans le service llm ; le quota est global à la
   plateforme, suivi en fenêtres glissantes (requêtes et jetons) ; à
   l'approche de la limite, les demandes sont mises en file d'attente et
   l'utilisateur informé, plutôt qu'en erreur. Anonymisation des documents
   utilisateurs avant tout envoi externe (loi 09-08, dossier CNDP).
4. **Embeddings** : modèle explicitement multilingue arabe-français
   (famille BGE-M3 ou équivalent) avec reranker multilingue — point critique
   validé en phase 2 sur le jeu d'évaluation doré.
5. **Mobile** : web responsive + canal WhatsApp en V1 (webhook Meta,
   opt-in explicite, fenêtre de service de 24 h, pas de documents
   sensibles sur ce canal), puis PWA ; application native seulement si un
   besoin réel émerge.

## Conséquences

**Positives** : alignement avec les compétences du porteur ; un seul
langage côté serveur ; fournisseur LLM interchangeable sans refonte ;
coûts maîtrisés au démarrage.

**Négatives / acceptées** : l'offre LLM gratuite n'a aucune garantie de
service — l'abstraction et la file d'attente rendent la bascule vers un
offre payante ou un modèle auto-hébergé purement opérationnelle ; la
qualité de l'arabe du couple embeddings/reranker devra être démontrée
avant tout engagement sur les performances annoncées.
