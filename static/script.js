const $ = (id) => document.getElementById(id);
let total = 0, maxSec = 60, pollTimer = null;

// ---- yordamchilar ----
function fmt(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
function parse(str) {
  const parts = String(str).trim().split(":").map(Number);
  if (!parts.length || parts.some((n) => !Number.isFinite(n) || n < 0)) return NaN;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}
function showErr(el, msg) { el.textContent = msg; el.classList.toggle("hidden", !msg); }
async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Server xatosi");
  return data;
}

// ---- vaqt boshqaruvi ----
function syncUI(from) {
  let s, e;
  if (from === "range" || from === "startR") { s = +$("startR").value; e = +$("endR").value; }
  else { s = parse($("startT").value); e = parse($("endT").value); }
  if (!Number.isFinite(s)) s = 0;
  if (!Number.isFinite(e)) e = Math.min(total, s + 30);
  s = Math.min(Math.max(0, s), total);
  e = Math.min(Math.max(0, e), total);
  if (from === "startR" || from === "startT") {
    if (e - s > maxSec) e = Math.min(total, s + maxSec);
  }
  $("startR").value = s; $("endR").value = e;
  $("startT").value = fmt(s); $("endT").value = fmt(e);
  const d = e - s;
  const el = $("chosen");
  el.textContent = `${Math.round(d)} s`;
  el.classList.toggle("bad", d <= 0 || d > maxSec);
}
$("startR").addEventListener("input", () => syncUI("startR"));
$("endR").addEventListener("input", () => {
  const s = +$("startR").value, e = +$("endR").value;
  if (e - s > maxSec) $("startR").value = e - maxSec;
  syncUI("range");
});
$("startT").addEventListener("change", () => syncUI("startT"));
$("endT").addEventListener("change", () => syncUI("endT"));

document.querySelectorAll(".mode").forEach((m) =>
  m.addEventListener("click", () => {
    document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
    m.classList.add("active");
  })
);

// ---- 1. video ma'lumoti ----
$("btnInfo").addEventListener("click", async () => {
  showErr($("errInfo"), "");
  const url = $("url").value.trim();
  if (!url) return showErr($("errInfo"), "Havolani kiriting.");
  $("btnInfo").disabled = true; $("btnInfo").textContent = "Tekshirilmoqda...";
  try {
    const info = await post("/api/info", { url });
    total = Math.floor(info.duration);
    maxSec = info.max_seconds;
    $("title").textContent = info.title;
    $("thumb").src = info.thumbnail;
    $("durTotal").textContent = `Umumiy davomiylik: ${fmt(total)}`;
    $("maxS").textContent = maxSec;
    ["startR", "endR"].forEach((id) => ($(id).max = total));
    const end = Math.min(total, 30);
    $("startR").value = 0; $("endR").value = end;
    $("startT").value = fmt(0); $("endT").value = fmt(end);
    syncUI("range");
    $("settings").classList.remove("hidden");
  } catch (err) {
    $("settings").classList.add("hidden");
    showErr($("errInfo"), err.message);
  } finally {
    $("btnInfo").disabled = false; $("btnInfo").textContent = "Tekshirish";
  }
});
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btnInfo").click(); });

// ---- 2. yaratish ----
$("btnMake").addEventListener("click", async () => {
  showErr($("errMake"), "");
  const start = parse($("startT").value), end = parse($("endT").value);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start)
    return showErr($("errMake"), "Boshlanish va tugash vaqtini to'g'ri kiriting.");
  if (end - start > maxSec)
    return showErr($("errMake"), `Davomiylik ${maxSec} soniyadan oshmasligi kerak.`);
  if (end > total)
    return showErr($("errMake"), "Tugash vaqti video davomiyligidan katta.");

  const mode = document.querySelector("input[name=mode]:checked").value;
  $("btnMake").disabled = true;
  try {
    const { id } = await post("/api/shorts", { url: $("url").value.trim(), start, end, mode });
    $("settings").classList.add("hidden");
    $("progressCard").classList.remove("hidden");
    poll(id);
  } catch (err) {
    showErr($("errMake"), err.message);
    $("btnMake").disabled = false;
  }
});

// ---- 3. jarayonni kuzatish ----
function backToSettings(msg) {
  clearInterval(pollTimer);
  $("progressCard").classList.add("hidden");
  $("settings").classList.remove("hidden");
  $("btnMake").disabled = false;
  showErr($("errMake"), msg);
}

function poll(id) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/status/${id}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || "Vazifa topilmadi");
      $("pmsg").textContent = d.message;
      $("pbar").style.width = d.progress + "%";
      if (d.status === "done") {
        clearInterval(pollTimer);
        $("progressCard").classList.add("hidden");
        $("player").src = `/api/download/${id}?inline=1`;
        $("dl").href = `/api/download/${id}`;
        $("resultCard").classList.remove("hidden");
      } else if (d.status === "error") {
        backToSettings(d.message);
      }
    } catch (err) {
      backToSettings(err.message);
    }
  }, 1500);
}

// ---- 4. qayta boshlash ----
$("btnAgain").addEventListener("click", () => {
  $("resultCard").classList.add("hidden");
  $("player").removeAttribute("src");
  $("pbar").style.width = "0";
  $("settings").classList.remove("hidden");
  $("btnMake").disabled = false;
});