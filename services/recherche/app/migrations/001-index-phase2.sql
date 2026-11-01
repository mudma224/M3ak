-- Migration 001 — phase 2, étape 1 : index de recherche hybride.
--
-- À appliquer UNE FOIS sur la base du service recherche (runbook §20) :
--   docker exec -i m3ak-recherche-db psql -U u_recherche -d db_recherche \
--     < services/recherche/app/migrations/001-index-phase2.sql
--
-- Idempotente (IF NOT EXISTS). vector(1024) = BGE-M3 — même valeur que
-- DIMENSIONS_EMBEDDING dans app/db.py : garder les deux en cohérence.
-- Contrainte : ce fichier est appliqué tel quel par psql — ne pas y
-- placer de point-virgule dans les commentaires.
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE segments ADD COLUMN IF NOT EXISTS tsv tsvector;
ALTER TABLE segments ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE fiches ADD COLUMN IF NOT EXISTS tsv tsvector;
ALTER TABLE fiches ADD COLUMN IF NOT EXISTS embedding vector(1024);
ALTER TABLE passages ADD COLUMN IF NOT EXISTS tsv tsvector;
ALTER TABLE passages ADD COLUMN IF NOT EXISTS embedding vector(1024);
