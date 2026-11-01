const $ = (id) => document.getElementById(id);

let source = "upload";            // "upload" yoki "url"
let ready = { upload: false, url: false };
let total = 0, maxSec = 60, maxMb = 100;
let file = null, objUrl = null, pollTimer = null;

// ---------- yordamchilar ----------
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
function showErr(el, msg) { el.textContent = msg || ""; el.classList.toggle("hidden", !msg); }
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

fetch("/api/config").then((r) => r.json()).then((c) => {
  maxSec = c.max_seconds; maxMb = c.max_upload_mb;
  $("maxMb").textContent = maxMb; $("maxS").textContent = maxSec;
}).catch(() => {});

// ---------- tablar ----------
function showSettings() {
  $("settings").classList.toggle("hidden", !ready[source]);
  document.querySelectorAll(".upload-only").forEach((el) =>
    el.classList.toggle("hidden", source !== "upload"));
}
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    source = t.dataset.tab;
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $("panelUpload").classList.toggle("hidden", source !== "upload");
    $("panelUrl").classList.toggle("hidden", source !== "url");
    $("resultCard").classList.add("hidden");
    $("progressCard").classList.add("hidden");
    showSettings();
  })
);

// ---------- vaqt boshqaruvi ----------
function render(s, e, changed) {
  s = Math.min(Math.max(0, s), total);
  e = Math.min(Math.max(0, e), total);
  if (e - s > maxSec) {
    if (changed === "end") s = Math.max(0, e - maxSec);
    else e = Math.min(total, s + maxSec);
  }
  $("startR").value = s; $("endR").value = e;
  $("startT").value = fmt(s); $("endT").value = fmt(e);
  const d = e - s;
  $("chosen").textContent = `${Math.round(d)} s`;
  $("chosen").classList.toggle("bad", d <= 0 || d > maxSec);
}
function fromText(changed) {
  let s = parse($("startT").value), e = parse($("endT").value);
  if (!Number.isFinite(s)) s = +$("startR").value;
  if (!Number.isFinite(e)) e = +$("endR").value;
  render(s, e, changed);
}
$("startR").addEventListener("input", () => render(+$("startR").value, +$("endR").value, "start"));
$("endR").addEventListener("input", () => render(+$("startR").value, +$("endR").value, "end"));
$("startT").addEventListener("change", () => fromText("start"));
$("endT").addEventListener("change", () => fromText("end"));

$("setStart").addEventListener("click", () => {
  const s = Math.floor($("preview").currentTime);
  let e = +$("endR").value;
  if (e <= s) e = Math.min(total, s + 30);
  render(s, e, "start");
});
$("setEnd").addEventListener("click", () => {
  const e = Math.ceil($("preview").currentTime);
  let s = +$("startR").value;
  if (s >= e) s = Math.max(0, e - 30);
  render(s, e, "end");
});

function initSettings() {
  ["startR", "endR"].forEach((id) => ($(id).max = total));
  render(0, Math.min(total, 30, maxSec), "end");
  ready[source] = true;
  showSettings();
}

document.querySelectorAll(".mode").forEach((m) =>
  m.addEventListener("click", () => {
    document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
    m.classList.add("active");
  })
);

// ---------- 1a. fayl tanlash ----------
function pickFile(f) {
  showErr($("errUpload"), "");
  if (!f) return;
  if (!f.type.startsWith("video/") && !/\.(mp4|mov|m4v|mkv|webm|avi|3gp)$/i.test(f.name)) {
    return showErr($("errUpload"), "Faqat video fayl tanlang.");
  }
  if (f.size > maxMb * 1024 * 1024) {
    return showErr($("errUpload"),
      `Fayl juda katta (${Math.round(f.size / 1048576)} MB). Eng ko'pi ${maxMb} MB.`);
  }
  file = f;
  ready.upload = false;
  if (objUrl) URL.revokeObjectURL(objUrl);
  objUrl = URL.createObjectURL(f);
  $("fileName").textContent = `${f.name} (${(f.size / 1048576).toFixed(1)} MB)`;
  const pv = $("preview");
  pv.classList.remove("hidden");
  pv.src = objUrl;
  showSettings();
}
$("file").addEventListener("change", () => pickFile($("file").files[0]));
$("preview").addEventListener("loadedmetadata", () => {
  const d = $("preview").duration;
  if (Number.isFinite(d) && d > 0) {
    total = Math.ceil(d);
    initSettings();
  }
});
$("preview").addEventListener("error", () => {
  // Brauzer formatni ko'rsata olmasa ham, vaqtni qo'lda kiritib yuborish mumkin
  showErr($("errUpload"),
    "Brauzer bu videoni ko'rsata olmadi, lekin vaqtni qo'lda kiritib yuborishingiz mumkin.");
  total = 6 * 3600;
  initSettings();
});
const drop = $("drop");
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

// ---------- 1b. YouTube havolasi ----------
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
    $("videoInfo").classList.remove("hidden");
    initSettings();
  } catch (err) {
    ready.url = false;
    $("videoInfo").classList.add("hidden");
    showSettings();
    showErr($("errInfo"), err.message);
  } finally {
    $("btnInfo").disabled = false; $("btnInfo").textContent = "Tekshirish";
  }
});
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") $("btnInfo").click(); });

// ---------- 2. yaratish ----------
function uploadFile(start, end, mode) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("start", start);
    fd.append("end", end);
    fd.append("mode", mode);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      $("pmsg").textContent = p < 100 ? `Yuklanmoqda: ${p}%` : "Server qabul qilmoqda...";
      $("pbar").style.width = p * 0.3 + "%";
    };
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch (_) { /* HTML javob */ }
      if (xhr.status >= 200 && xhr.status < 300 && d.id) resolve(d);
      else reject(new Error(d.error || `Yuklashda xatolik (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Aloqa uzildi. Internetni tekshirib, qayta urinib ko'ring."));
    xhr.send(fd);
  });
}

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
  $("settings").classList.add("hidden");
  $("pbar").style.width = "0";
  $("pmsg").textContent = "Boshlanmoqda...";
  $("progressCard").classList.remove("hidden");
  try {
    const { id } = source === "upload"
      ? await uploadFile(start, end, mode)
      : await post("/api/shorts", { url: $("url").value.trim(), start, end, mode });
    poll(id);
  } catch (err) {
    backToSettings(err.message);
  }
});

// ---------- 3. jarayon ----------
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

// ---------- 4. qayta boshlash ----------
$("btnAgain").addEventListener("click", () => {
  $("resultCard").classList.add("hidden");
  $("player").removeAttribute("src");
  $("pbar").style.width = "0";
  $("settings").classList.remove("hidden");
  $("btnMake").disabled = false;
});
