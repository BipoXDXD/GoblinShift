import "./styles/tokens.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { z } from "zod";

import { App } from "./app/App";

// A CSP da SPA (3.6) não libera eval: o Zod valida sem compilar schemas com new Function.
z.config({ jitless: true });

const container = document.getElementById("root");
if (container === null) {
  throw new Error("index.html não tem o elemento #root");
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
