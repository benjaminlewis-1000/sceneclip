import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Local (non-docker) dev convenience: `npm run dev` proxies API calls to a
// Django dev server on :8000 so the browser only ever talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/accounts": "http://localhost:8000",
      "/admin": "http://localhost:8000",
    },
  },
});
