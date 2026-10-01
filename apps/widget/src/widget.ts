// Embeddable chat widget. Placeholder until the "Web chat widget (embeddable)" card:
// it only mounts a launcher button so the build and embed path can be tested.
//
// Embed: <script src="https://…/widget.js" data-confluo-tenant="<slug>" async></script>

const script = document.currentScript as HTMLScriptElement | null;
const tenant = script?.dataset.confluoTenant ?? "";

function mount(): void {
  const host = document.createElement("div");
  host.id = "confluo-widget";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      button { position: fixed; right: 20px; bottom: 20px; width: 56px; height: 56px;
        border: 0; border-radius: 50%; background: #111827; color: #fff; font: 600 14px system-ui;
        cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.2); }
    </style>
    <button type="button" aria-label="Open chat">Chat</button>`;
  root.querySelector("button")?.addEventListener("click", () => {
    console.info("[confluo] widget opened", { tenant });
  });
  document.body.appendChild(host);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
