// Renders the status page. The HTML/CSS lives in ../page_template.html and is
// imported as text (see the Text rule in wrangler.toml) so the Worker and the
// Python renderer can never drift apart.
import TEMPLATE from "../page_template.html";

const PRODUCT_URL = (id) => `https://www.hmtwatches.store/product/${id}`;
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  return m < 2 ? "just now" : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

export function render(state) {
  const entries = Object.entries(state)
    .filter(([k]) => k !== "_meta")
    .sort((a, b) => (a[1].status !== "in_stock") - (b[1].status !== "in_stock") ||
      String(a[1].name).localeCompare(String(b[1].name)));

  const plates = entries.map(([id, s]) => {
    const live = s.status === "in_stock";
    const [cls, label] = live
      ? ["in", "In stock"]
      : s.fails
        ? ["down", "Check failed"]
        : ["out", "Out of stock"];
    const kn = /gandaberunda/i.test(s.name || "") ? '<div class="kn">ಗಂಡಭೇರುಂಡ</div>' : "";
    return `<article class="plate${live ? " instock" : ""}">
      <div class="ref">Ref. ${esc(id.slice(0, 8)).toUpperCase()}</div>
      <h2 class="name">${esc(s.name || "Unknown")}</h2>${kn}
      <div class="state ${cls}"><b></b>${label}</div>
      <div class="price">₹${(s.mrp || 0).toLocaleString("en-IN")}</div>
      <div class="meta"><span data-checked="${(s.checked || 0) / 1000}">Checked ${ago(s.checked || 0)}</span></div>
      <a class="buy" href="${PRODUCT_URL(id)}">${live ? "Buy now" : "View on HMT"} &rarr;</a>
    </article>`;
  });

  const liveCount = entries.filter(([, s]) => s.status === "in_stock").length;
  const n = entries.length;
  const verdict = liveCount
    ? `${liveCount} in stock now`
    : n === 2
      ? "Both out of stock"
      : n
        ? `All ${n} out of stock`
        : "Starting up";

  const updated = new Date().toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });

  return TEMPLATE.replace("__ROWS__", plates.join("\n"))
    .replace("__VERDICT__", verdict)
    .replace("__UPDATED__", updated)
    .replace("checking every 5 min", "checking every minute");
}
