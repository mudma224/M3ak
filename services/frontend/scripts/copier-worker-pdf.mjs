/** Copie le worker de pdf.js dans public/ — l'unique façon fiable de le
 * servir quelle que soit la chaîne de build (voir runbook). Lancé par les
 * hooks predev/prebuild. */
import { copyFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ici = dirname(fileURLToPath(import.meta.url));
const service = dirname(ici);

const requireResolu = createRequire(join(service, "package.json"));
const source = requireResolu.resolve("pdfjs-dist/build/pdf.worker.min.mjs");

const destination = join(service, "public", "pdf.worker.min.mjs");
await mkdir(join(service, "public"), { recursive: true });
await copyFile(source, destination);
console.log("pdf.js : worker copié dans public/");
