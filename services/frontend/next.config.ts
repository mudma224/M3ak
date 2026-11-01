import type { NextConfig } from "next";

const configuration: NextConfig = {
  // Sortie standalone : le conteneur exécute server.js sans node_modules.
  output: "standalone",
};

export default configuration;
