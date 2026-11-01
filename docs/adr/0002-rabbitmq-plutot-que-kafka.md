# ADR-0002 — RabbitMQ plutôt que Kafka pour la communication asynchrone

- **Statut** : accepté
- **Date** : septembre 2026
- **Décideurs** : porteur du projet, sur recommandation architecturale

## Contexte

Les flux asynchrones de la plateforme sont : la publication des segments
d'articles par le service corpus vers le service recherche (quelques
dizaines de milliers de messages lors de l'indexation initiale, puis
quelques dizaines par jour via la veille), et à terme les tâches OCR à la
demande. L'hébergement est un hôte unique sous Docker Compose portant déjà
la passerelle, cinq bases PostgreSQL, le cache et le stockage objet. Le
porteur attend de la résilience réelle : accusés de consommation, messages
en échec isolés, inspection avant rejeu.

## Décision

Retenir **RabbitMQ 3.13 (management)** comme courtier de messages, avec la
sémantique suivante : livraison *at-least-once* et consommateurs
idempotents ; files quorum durables ; une dead-letter exchange par file de
travail ; publisher confirms ; contrats de messages versionnés dans
`m3ak-contracts`, abstraits des connecteurs.

**Kafka est explicitement écarté** à ce stade : conçu pour le streaming
distribué à très haut débit avec relecture, son coût opérationnel (JVM,
mode cluster, mémoire) n'apporte aucun bénéfice aux volumes du projet.
**Redis Streams** a aussi été écarté comme courtier principal (pas de DLQ
native, sémantique de file limitée) ; Redis reste présent pour son vrai
rôle : cache et limitation de débit.

## Déclencheurs de réévaluation (migration vers Kafka)

1. Débit soutenu au-delà de quelques milliers de messages par seconde ;
2. besoin de relecture de flux (event sourcing, audit par replay) ;
3. diffusion d'un même flux vers de nombreux consommateurs indépendants.

Grâce aux contrats abstraits, la migration se jouerait dans les connecteurs
des seuls services émetteurs/consommateurs.

## Conséquences

**Positives** : sémantique de file de travail complète (ack, rejet, DLQ,
quorum) pour une empreinte mémoire modeste ; interface d'administration
web précieuse pour le débogage ; client Python mature (aio-pika) ;
courant standard pour ce volume.

**Négatives / acceptées** : un composant d'infrastructure de plus à
exploiter ; pas de relecture native des flux consommés (compensé par la
rejouabilité de la chaîne corpus depuis la couche brute).
