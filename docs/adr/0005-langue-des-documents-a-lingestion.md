# ADR-0005 — Langue des documents à l'ingestion : titre arabe prioritaire, détection gardée par densité

- **Statut** : accepté et **mis en œuvre** (phase 1, étape 2)
- **Date** : septembre 2026
- **Décideurs** : porteur du projet

## Contexte

L'identifiant stable d'un texte normatif est `numéro|langue|version` : la
langue y entre, elle gouverne la déduplication, l'indexation et le repli
interlingue. Or Adala mélange documents arabes et français dans un même
thème — la langue ne peut être déclarée a priori par thème ni par
métadonnée seule.

Le corpus réel (2 881 documents) présente trois familles de cas,
mesurées avec `tools/analyser_quarantaine.py` :

1. **Scans sans couche texte** : l'extraction ne donne que des folios
   (≈ 0 lettre) — indécidable par le contenu.
2. **Scans longs à en-têtes répétés** : 200 pages × un en-tête français
   accumulent des centaines de lettres latines sans que le document soit
   français (densité mesurée ≤ 60 caractères/page, contre ≥ 243 pour du
   vrai texte).
3. **PDF à polices arabes cassées** (sans table ToUnicode — ex. lois de
   finances) : l'extraction produit des codes de glyphes décodés en
   caractères latins parasites — 0 lettre arabe, des milliers de
   pseudo-lettres latines, densité de vrai texte.

S'y ajoute une exigence de conception : aucun endpoint ne doit posséder
de langue par défaut — une valeur par défaut serait une déclaration
implicite de langue, contraire au principe de détection.

## Décision

La langue est décidée par le pipeline (`decider_langue`), dans cet ordre :

1. **Titre arabe → ar**, prime sur tout. Un document français ne porte
   jamais de titre arabe sur Adala ; c'est le seul signal qui survit aux
   polices cassées.
2. **Détection du contenu** si elle est fiable : ≥ 50 lettres ET
   ≥ 100 caractères/page (seuil dans le vide mesuré entre junk de scan
   ≤ 60 et vrai texte ≥ 243).
3. **Annonce de la source** (champ `language` d'Adala), `ar`/`fr`
   uniquement.
4. **« inconnue »** sinon — quarantaine, jamais indexée.

Corollaire : **aucune langue par défaut n'existe nulle part** (endpoint :
champ absent ou vide = aucune annonce).

## Conséquences

**Positives** : les scans et documents à polices cassées sont étiquetés
selon un critère vérifiable (le titre) plutôt que par des métadonnées
parfois inexactes ; « inconnue » honnête pour l'indécidable (l'OCR de la
phase 4 tranchera) ; le repli interlingue repose sur des variantes
linguistiques réelles ; l'absence de valeur par défaut élimine toute
déclaration implicite.

**Négatives / acceptées** : le titre source devient une entrée de la
décision (un texte français portant un titre arabe serait étiqueté « ar »
— cas non observé) ; les seuils de densité sont des heuristiques mesurées
sur le corpus actuel, à réévaluer au passage à l'échelle.

## Notes de mise en œuvre

- `services/corpus/app/pipeline.py` (`decider_langue`), tests de
  régression : glyphes parasites + titre arabe → ar ; garde de densité
  (junk dilué sur 200 pages → indécidable) ; aucun défaut de langue.
- Mesure sur corpus réel : `tools/analyser_quarantaine.py`
  (pages, lettres ar/lat, densité, lignes les plus fréquentes).
- Ré-étiquetage sans re-téléchargement : `tools/resegmenter_langue.py`
  (teste le service avant toute purge).

## Références

- AGENTS.md §5 (architecture de données — langue du document).
- Runbook §14 (collecte Adala) et §15 (boucle d'amélioration).
