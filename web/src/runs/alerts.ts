import { useEffect, useRef } from "react";

import { runPath } from "../app/paths";
import { findRecipe } from "../recipes/registry";
import { TEXT } from "../text";
import type { RunDetail } from "./api";
import { useCompletionTitle, type SignaledStatus } from "./completionSignal";

const T = TEXT.run.completion;

/** Chave no `localStorage` com a escolha do aviso de término: `"on"` ou `"off"`. */
export const ALERTS_KEY = "gs-run-alerts";

type AlertsChoice = "on" | "off";

/** Se o navegador tem notificações; sem elas, o toggle some e só o título da aba avisa. */
export function alertsSupported(): boolean {
  // `typeof`, não `in`: o navegador sem suporte pode ter a propriedade indefinida.
  return typeof window.Notification === "function";
}

function readAlerts(): AlertsChoice {
  try {
    return window.localStorage.getItem(ALERTS_KEY) === "on" ? "on" : "off";
  } catch {
    // Armazenamento bloqueado: o aviso fica desligado, que não incomoda ninguém.
    return "off";
  }
}

function writeAlerts(choice: AlertsChoice): void {
  try {
    window.localStorage.setItem(ALERTS_KEY, choice);
  } catch {
    // Sem armazenamento, a escolha vale só até a próxima carga; o toggle já a mostra.
  }
}

/** Se o aviso está ligado e o navegador ainda deixa avisar (a permissão pode ter sido revogada). */
export function alertsEnabled(): boolean {
  return alertsSupported() && readAlerts() === "on" && Notification.permission === "granted";
}

/**
 * Pede a permissão do navegador e liga o aviso. Só o clique do toggle chama: o diálogo do navegador
 * aparece depois de um pedido explícito, nunca ao abrir a tela (D3).
 */
export async function requestAlerts(): Promise<"on" | "blocked"> {
  let permission: NotificationPermission;
  try {
    permission = await Notification.requestPermission();
  } catch {
    // Navegador que recusa o pedido em vez de responder: para a pessoa, é o mesmo bloqueio.
    return "blocked";
  }
  if (permission !== "granted") {
    return "blocked";
  }
  writeAlerts("on");
  return "on";
}

export function disableAlerts(): void {
  writeAlerts("off");
}

interface CompletionNotice {
  readonly runId: string;
  readonly status: SignaledStatus;
  readonly recipeTitle: string;
  readonly fileCount: number;
}

/**
 * Notificação do término: só com o aviso ligado, a permissão dada e a aba escondida (com a aba
 * visível, a tela já mostra). A `tag` é o id da execução, para duas abas da mesma execução
 * avisarem uma vez. O corpo não leva nome de arquivo, porque aparece na tela bloqueada.
 */
export async function notifyCompletion(notice: CompletionNotice): Promise<void> {
  if (!alertsEnabled() || document.visibilityState !== "hidden") {
    return;
  }
  const title = T.notification.title[notice.status];
  const options: NotificationOptions = {
    body: T.notification.body(notice.recipeTitle, notice.fileCount),
    tag: notice.runId,
    data: { url: runPath(notice.runId) },
  };
  try {
    // No Android, `new Notification` lança TypeError: com service worker, a notificação sai dele.
    const registration =
      "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
    if (registration === undefined) {
      const notification = new Notification(title, options);
      notification.onclick = () => {
        window.focus();
        notification.close();
      };
    } else {
      await registration.showNotification(title, options);
    }
  } catch {
    // O aviso é acessório: o título da aba já sinalizou o término, e um erro aqui não deve virar
    // alerta nem console numa tela que está escondida.
  }
}

/**
 * Título da aba e notificação no término de `run` (Tarefas 7 e 8). A notificação sai uma vez por
 * execução: a tela é montada de novo a cada execução (key do RunView), e a ref segura as leituras
 * seguintes do mesmo término.
 */
export function useCompletionAlert(run: RunDetail | undefined): void {
  const signal = useCompletionTitle(run?.status);
  const notified = useRef(false);
  useEffect(() => {
    if (signal === null || run === undefined || notified.current) {
      return;
    }
    notified.current = true;
    void notifyCompletion({
      runId: run.id,
      status: signal,
      recipeTitle: findRecipe(run.recipe_id)?.title ?? run.recipe_id,
      fileCount: run.items.length,
    });
  }, [signal, run]);
}
