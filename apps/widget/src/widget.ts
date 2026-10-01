// Confluo web chat widget. One script tag on the business's website:
//
//   <script src="https://…/widget.js" data-confluo-key="wk_…" async></script>
//
// Optional: data-confluo-api="https://api.…" (default: the script's own origin),
// data-confluo-lang="nl" (default: the browser language).
//
// Renders in a Shadow DOM (the host page's CSS can't touch it), keeps a random
// session id in localStorage so the conversation survives page loads, and talks to
// the API over a WebSocket: history on connect, acks for sent messages, replies
// pushed as they're written.

type Config = { color: string; title: Record<string, string>; greeting: Record<string, string>; logo_url: string | null };
type Msg = { id: string; from: string; text: string; at?: string | null };

const STRINGS: Record<string, { placeholder: string; send: string; open: string; close: string; title: string; offline: string }> = {
  en: { placeholder: "Type a message…", send: "Send", open: "Open chat", close: "Close chat", title: "Chat with us", offline: "Reconnecting…" },
  nl: { placeholder: "Typ een bericht…", send: "Verstuur", open: "Chat openen", close: "Chat sluiten", title: "Chat met ons", offline: "Opnieuw verbinden…" },
  fr: { placeholder: "Écrivez un message…", send: "Envoyer", open: "Ouvrir le chat", close: "Fermer le chat", title: "Discutez avec nous", offline: "Reconnexion…" },
  de: { placeholder: "Nachricht schreiben…", send: "Senden", open: "Chat öffnen", close: "Chat schließen", title: "Chatten Sie mit uns", offline: "Verbinde neu…" },
  sq: { placeholder: "Shkruani një mesazh…", send: "Dërgo", open: "Hap bisedën", close: "Mbyll bisedën", title: "Bisedoni me ne", offline: "Duke u rilidhur…" },
};

const script = document.currentScript as HTMLScriptElement | null;
const key = script?.dataset.confluoKey ?? "";
const api = (script?.dataset.confluoApi ?? (script ? new URL(script.src).origin : location.origin)).replace(/\/$/, "");
const browserLang = (script?.dataset.confluoLang ?? navigator.language ?? "en").slice(0, 2).toLowerCase();
const lang = browserLang in STRINGS ? browserLang : "en";
const t = STRINGS[lang];

function sessionId(): string {
  const storageKey = `confluo:${key}:session`;
  try {
    const existing = localStorage.getItem(storageKey);
    if (existing && /^[A-Za-z0-9_-]{16,64}$/.test(existing)) return existing;
  } catch {
    /* storage blocked: the session lasts as long as the page */
  }
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  const id = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  try {
    localStorage.setItem(storageKey, id);
  } catch {
    /* ignore */
  }
  return id;
}

function pick(texts: Record<string, string>, fallback: string): string {
  return texts[lang] ?? texts.en ?? Object.values(texts)[0] ?? fallback;
}

const styles = (color: string) => `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .launcher { position: fixed; right: 20px; bottom: 20px; width: 56px; height: 56px; border: 0; border-radius: 50%;
    background: ${color}; color: #fff; cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.2); display: grid; place-items: center; z-index: 2147483000; }
  .launcher svg { width: 26px; height: 26px; }
  .panel { position: fixed; right: 20px; bottom: 88px; width: min(370px, calc(100vw - 24px)); height: min(560px, calc(100vh - 120px));
    background: #fff; color: #111827; border-radius: 16px; box-shadow: 0 12px 40px rgba(0,0,0,.25); display: none; flex-direction: column;
    overflow: hidden; z-index: 2147483000; }
  .panel.open { display: flex; }
  @media (max-width: 480px) { .panel { right: 0; bottom: 0; width: 100vw; height: 100dvh; border-radius: 0; } }
  header { background: ${color}; color: #fff; padding: 14px 16px; display: flex; align-items: center; gap: 10px; }
  header img { width: 28px; height: 28px; border-radius: 6px; object-fit: cover; background: #fff; }
  header strong { flex: 1; font-size: 15px; }
  header button { background: transparent; border: 0; color: #fff; font-size: 22px; line-height: 1; cursor: pointer; }
  .log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; background: #f9fafb; }
  .msg { max-width: 82%; padding: 9px 12px; border-radius: 14px; font-size: 14px; line-height: 1.4; white-space: pre-wrap; word-wrap: break-word; }
  .msg.them { background: #fff; border: 1px solid #e5e7eb; align-self: flex-start; border-bottom-left-radius: 4px; }
  .msg.me { background: ${color}; color: #fff; align-self: flex-end; border-bottom-right-radius: 4px; }
  .msg.pending { opacity: .6; }
  .typing { align-self: flex-start; color: #6b7280; font-size: 20px; letter-spacing: 2px; display: none; }
  .typing.on { display: block; }
  .status { font-size: 12px; color: #b45309; text-align: center; padding: 4px; display: none; }
  .status.on { display: block; }
  form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb; background: #fff; }
  input { flex: 1; border: 1px solid #d1d5db; border-radius: 10px; padding: 10px 12px; font-size: 14px; outline: none; }
  input:focus { border-color: ${color}; }
  form button { border: 0; border-radius: 10px; padding: 0 14px; background: ${color}; color: #fff; font-size: 14px; cursor: pointer; }
`;

const BUBBLE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>`;

async function start(): Promise<void> {
  if (!key) {
    console.warn("[confluo] missing data-confluo-key");
    return;
  }
  let config: Config;
  try {
    const res = await fetch(`${api}/public/crm/web/${encodeURIComponent(key)}/config`);
    if (!res.ok) return; // unknown or paused widget: show nothing
    config = (await res.json()) as Config;
  } catch {
    return;
  }

  const session = sessionId();
  const host = document.createElement("div");
  host.id = "confluo-chat";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>${styles(config.color)}</style>
    <section class="panel" role="dialog" aria-label="${pick(config.title, t.title)}">
      <header>${config.logo_url ? `<img alt="" src="${config.logo_url}">` : ""}<strong></strong>
        <button type="button" class="close" aria-label="${t.close}">×</button></header>
      <div class="status">${t.offline}</div>
      <div class="log" role="log" aria-live="polite"></div>
      <form><input aria-label="${t.placeholder}" placeholder="${t.placeholder}" maxlength="2000" autocomplete="off">
        <button type="submit">${t.send}</button></form>
    </section>
    <button type="button" class="launcher" aria-label="${t.open}">${BUBBLE}</button>`;
  document.body.appendChild(host);

  const panel = root.querySelector(".panel") as HTMLElement;
  const log = root.querySelector(".log") as HTMLElement;
  const input = root.querySelector("input") as HTMLInputElement;
  const status = root.querySelector(".status") as HTMLElement;
  (root.querySelector("header strong") as HTMLElement).textContent = pick(config.title, t.title);
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.textContent = "•••";

  const shown = new Map<string, HTMLElement>();
  let greeted = false;

  function add(msg: Msg, mine: boolean, pending = false): void {
    if (shown.has(msg.id)) return;
    const el = document.createElement("div");
    el.className = `msg ${mine ? "me" : "them"}${pending ? " pending" : ""}`;
    el.textContent = msg.text;
    el.dataset.id = msg.id;
    log.insertBefore(el, typing.parentNode === log ? typing : null);
    shown.set(msg.id, el);
    log.scrollTop = log.scrollHeight;
  }

  function greet(): void {
    const greeting = pick(config.greeting, "");
    if (!greeted && greeting && log.childElementCount <= 1) {
      add({ id: "greeting", from: "ai", text: greeting }, false);
    }
    greeted = true;
  }

  let ws: WebSocket | null = null;
  let retry = 0;
  let typingTimer = 0;
  const outbox: { id: string; text: string }[] = [];

  function setTyping(on: boolean): void {
    typing.classList.toggle("on", on);
    window.clearTimeout(typingTimer);
    if (on) typingTimer = window.setTimeout(() => setTyping(false), 25000);
    log.scrollTop = log.scrollHeight;
  }

  function connect(): void {
    if (ws && ws.readyState <= WebSocket.OPEN) return;
    const url = `${api.replace(/^http/, "ws")}/public/crm/web/${encodeURIComponent(key)}/socket?session=${session}`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      retry = 0;
      status.classList.remove("on");
      for (const m of outbox) ws?.send(JSON.stringify({ type: "message", id: m.id, text: m.text, lang: browserLang }));
    };
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data as string);
      if (data.type === "history") {
        for (const m of data.messages as Msg[]) add(m, m.from === "customer");
        greet();
      } else if (data.type === "ack") {
        shown.get(data.id)?.classList.remove("pending");
        const i = outbox.findIndex((m) => m.id === data.id);
        if (i >= 0) outbox.splice(i, 1);
      } else if (data.type === "message") {
        setTyping(false);
        add(data.message as Msg, false);
      } else if (data.type === "error") {
        setTyping(false);
      }
    };
    ws.onclose = () => {
      ws = null;
      if (!panel.classList.contains("open")) return;
      status.classList.add("on");
      retry = Math.min(retry + 1, 6);
      window.setTimeout(connect, 500 * 2 ** retry);
    };
  }

  log.appendChild(typing);
  root.querySelector(".launcher")?.addEventListener("click", () => {
    const open = panel.classList.toggle("open");
    if (open) {
      connect();
      input.focus();
    }
  });
  root.querySelector(".close")?.addEventListener("click", () => panel.classList.remove("open"));
  root.querySelector("form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    add({ id, from: "customer", text }, true, true);
    outbox.push({ id, text });
    setTyping(true);
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "message", id, text, lang: browserLang }));
    } else {
      connect();
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => void start());
} else {
  void start();
}
