import type { UploadXhr } from "../upload/xhr";

/** XMLHttpRequest falso para os testes de upload: grava o pedido e simula a resposta. */
export class FakeXhr implements UploadXhr {
  method: string | null = null;
  url: string | null = null;
  body: Blob | null = null;
  readonly requestHeaders = new Map<string, string>();
  aborted = false;

  status = 0;
  responseText = "";
  readonly upload: UploadXhr["upload"] = { onprogress: null };
  onload: UploadXhr["onload"] = null;
  onerror: UploadXhr["onerror"] = null;
  onabort: UploadXhr["onabort"] = null;

  #responseHeaders = new Map<string, string>();

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string): void {
    this.requestHeaders.set(name, value);
  }

  send(body: Blob): void {
    this.body = body;
  }

  abort(): void {
    this.aborted = true;
    this.onabort?.(new ProgressEvent("abort"));
  }

  getResponseHeader(name: string): string | null {
    return this.#responseHeaders.get(name.toLowerCase()) ?? null;
  }

  progress(loaded: number, total: number): void {
    this.upload.onprogress?.(
      new ProgressEvent("progress", { loaded, total, lengthComputable: true }),
    );
  }

  respond(status: number, body: unknown, headers: Record<string, string> = {}): void {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    for (const [name, value] of Object.entries(headers)) {
      this.#responseHeaders.set(name.toLowerCase(), value);
    }
    this.onload?.(new ProgressEvent("load"));
  }

  failNetwork(): void {
    this.onerror?.(new ProgressEvent("error"));
  }
}
