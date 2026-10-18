// Shared helpers for all FoodBridge pages
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
};
const postJSON = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
const minsUntil = (iso, serverNowIso) => {
  const skew = serverNowIso ? new Date(serverNowIso) - Date.now() : 0;
  return Math.round((new Date(iso) - (Date.now() + skew)) / 60000);
};
const countdown = (iso) => {
  const s = Math.max(0, Math.round((new Date(iso) - Date.now()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const DIET = { veg: ["Veg", "bg-green-100 text-green-800 border-green-600"], egg: ["Egg", "bg-yellow-100 text-yellow-800 border-yellow-600"], non_veg: ["Non-veg", "bg-red-100 text-red-800 border-red-600"] };
const dietBadge = (d) => { const [t, c] = DIET[d] || ["?", ""]; return `<span class="inline-block border text-xs font-semibold px-2 py-0.5 rounded ${c}">${t}</span>`; };

const STATUS = {
  draft: "bg-slate-100 text-slate-700", offered: "bg-amber-100 text-amber-800", pending: "bg-amber-100 text-amber-800",
  accepted: "bg-emerald-100 text-emerald-800", picked_up: "bg-sky-100 text-sky-800", delivered: "bg-emerald-600 text-white",
  declined: "bg-slate-200 text-slate-600", expired: "bg-rose-100 text-rose-700", cancelled: "bg-slate-200 text-slate-600",
  unmatched: "bg-rose-100 text-rose-700",
};
const statusPill = (s) => `<span class="text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS[s] || ""}">${esc(s.replace("_", " "))}</span>`;

// Map dots behave like a scatter plot: only 3 hues stay distinguishable (validated, CVD-safe), so fold to 3 groups
const GROUPS = [["children", "Children", "#2a78d6"], ["elderly", "Elderly", "#eb6834"], ["other", "Other care homes", "#1baf7a"]];
const groupOf = (cat) => (cat === "children" || cat === "elderly" ? cat : "other");
const CAT_COLOR = new Proxy({}, { get: (_, cat) => GROUPS.find((g) => g[0] === groupOf(cat))[2] });
function addLegend(map) {
  const c = L.control({ position: "bottomleft" });
  c.onAdd = () => {
    const d = L.DomUtil.create("div", "bg-white/90 rounded-lg border px-3 py-2 text-xs space-y-1");
    d.innerHTML = GROUPS.map(([, label, col]) => `<div class="flex items-center gap-2"><span style="background:${col}" class="inline-block w-3 h-3 rounded-full ring-2 ring-white"></span>${label}</div>`).join("");
    return d;
  };
  c.addTo(map);
}

function baseMap(el) {
  const map = L.map(el, { zoomControl: true }).setView([12.97, 77.6], 11);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors", maxZoom: 19, className: "muted-tiles",
  }).addTo(map);
  if (!document.getElementById("muted-tiles-css")) {
    const st = document.createElement("style"); st.id = "muted-tiles-css";
    st.textContent = ".muted-tiles{filter:grayscale(.85) brightness(1.05) contrast(.9)}"; document.head.appendChild(st);
  }
  return map;
}
const dot = (latlng, color, r = 7, opts = {}) =>
  L.circleMarker(latlng, { radius: r, color: "#fff", weight: 2, fillColor: color, fillOpacity: 0.95, ...opts });

// One top bar for every page, so it never shifts when switching tabs
const NAV = [["/", "Donate"], ["/ngo", "NGO inbox"], ["/control", "Control room"], ["/forecast", "Forecast"], ["/dashboard", "Impact"]];
document.head.insertAdjacentHTML("beforeend", "<style>html{scrollbar-gutter:stable}</style>");
function renderTopbar() {
  const h = document.getElementById("topbar");
  if (!h) return;
  const here = location.pathname.replace(/\/$/, "") || "/";
  h.className = "bg-white border-b sticky top-0 z-[1000]";
  h.innerHTML = `<div class="max-w-6xl mx-auto px-4 h-14 flex items-center gap-4">
    <a href="/" class="font-bold text-lg text-emerald-700 shrink-0">FoodBridge</a>
    <nav class="flex gap-1 text-sm overflow-x-auto">${NAV.map(([href, label]) => `<a href="${href}" class="px-2 py-1 rounded-md font-medium whitespace-nowrap ${href === here
      ? "bg-emerald-50 text-emerald-800" : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"}"${href === here ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
    <span id="modelBadge" class="ml-auto hidden md:inline text-xs px-2 py-1 rounded bg-slate-100 text-slate-500 whitespace-nowrap">&nbsp;</span>
  </div>`;
  api("/api/config").then((cfg) => {
    const b = document.getElementById("modelBadge");
    b.textContent = cfg.gemini ? `Gemini: ${cfg.model}` : "Demo mode (no Gemini key)";
    b.className = b.className.replace("text-slate-500", cfg.gemini ? "text-emerald-700" : "text-amber-700 bg-amber-50");
  }).catch(() => {});
}
document.addEventListener("DOMContentLoaded", renderTopbar);
