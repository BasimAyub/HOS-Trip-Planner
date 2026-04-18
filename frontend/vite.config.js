import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Use "./" so opening `dist/index.html` from disk still loads JS/CSS (absolute "/assets/..." breaks file://).
export default defineConfig({
  plugins: [react()],
  base: "./",
});
