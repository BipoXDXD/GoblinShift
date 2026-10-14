import "./styles/tokens.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("index.html não tem o elemento #root");
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
