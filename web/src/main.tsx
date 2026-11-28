import "./zodJitless";
import "./styles/index.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { registerServiceWorker } from "./share/registerServiceWorker";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("index.html não tem o elemento #root");
}
createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
registerServiceWorker();
