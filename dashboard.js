// ── NASRDA Pan-Sharpening Dashboard JS ────────────────
var API = "http://localhost:5000/api";

var S = {
  method: "ihs", msData: null, panData: null,
  msW: 0, msH: 0, msBands: 1, panW: 0, panH: 0,
  msRGBA: null, msFile: null, panFile: null,
  jobId: null, pollTimer: null, lastLogCount: 0
};

// ── Init ──────────────────────────────────────────────
addLog("INFO", "NASRDA Pan-Sharpening Engine v2.0.0 — ready.");
addLog("INFO", "Upload MS and PAN images, select algorithm, then click RUN.");
drawPH("cv-ms", "ms"); drawPH("cv-pan", "pan"); drawPH("cv-fused", "fused");

// ── Tab switching ─────────────────────────────────────
function switchTab(el) {
  document.querySelectorAll(".tab-item").forEach(function(t) { t.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(p) { p.classList.remove("active"); });
  el.classList.add("active");
  document.getElementById("tab-" + el.dataset.tab).classList.add("active");
}

// ── Method selector ───────────────────────────────────
document.getElementById("method-grid").addEventListener("click", function(e) {
  var c = e.target.closest(".algo-card");
  if (!c) return;
  document.querySelectorAll(".algo-card").forEach(function(x) { x.classList.remove("active"); });
  c.classList.add("active");
  S.method = c.dataset.m;
});

// ── Drop zones ────────────────────────────────────────
function setupDrop(dzId, fiId, type) {
  var dz = document.getElementById(dzId);
  var fi = document.getElementById(fiId);
  fi.addEventListener("change", function(e) { if (e.target.files[0]) loadFile(e.target.files[0], type); });
  dz.addEventListener("dragover", function(e) { e.preventDefault(); dz.classList.add("drag-over"); });
  dz.addEventListener("dragleave", function() { dz.classList.remove("drag-over"); });
  dz.addEventListener("drop", function(e) {
    e.preventDefault(); dz.classList.remove("drag-over");
    if (e.dataTransfer.files[0]) loadFile(e.dataTransfer.files[0], type);
  });
}
setupDrop("dz-ms", "fi-ms", "ms");
setupDrop("dz-pan", "fi-pan", "pan");

// ── Load file ─────────────────────────────────────────
function loadFile(file, type) {
  if (type === "ms") S.msFile = file; else S.panFile = file;
  var name = file.name.toLowerCase();
  addLog("INFO", "Loading " + type.toUpperCase() + ": " + file.name + " (" + (file.size / 1048576).toFixed(1) + " MB)");
  var dz = document.getElementById("dz-" + type);
  if (dz) dz.classList.add("has-file");
  if (name.endsWith(".tif") || name.endsWith(".tiff")) loadGeoTIFF(file, type);
  else loadImg(file, type);
}

// ── GeoTIFF loader ────────────────────────────────────
function loadGeoTIFF(file, type) {
  file.arrayBuffer().then(function(buf) {
    return GeoTIFF.fromArrayBuffer(buf);
  }).then(function(tiff) {
    return tiff.getImage().then(function(img) {
      return img.readRasters().then(function(rasters) {
        var w = img.getWidth(), h = img.getHeight(), nb = img.getSamplesPerPixel();
        addLog("INFO", "  " + w + " x " + h + " px  |  " + nb + " band(s)");
        if (type === "ms") {
          S.msData = rasters; S.msW = w; S.msH = h; S.msBands = nb;
          document.getElementById("m-ms-b").textContent = nb;
          document.getElementById("m-ms-s").textContent = w + " x " + h;
          document.getElementById("st-ms").textContent = nb + "B";
          document.getElementById("ms-ok").classList.add("visible");
          renderGTIFF("cv-ms", rasters, w, h, nb, "ms");
        } else {
          S.panData = rasters; S.panW = w; S.panH = h;
          document.getElementById("m-pan-s").textContent = w + " x " + h;
          document.getElementById("st-pan").textContent = w + "x" + h;
          document.getElementById("pan-ok").classList.add("visible");
          renderGTIFF("cv-pan", rasters, w, h, 1, "pan");
        }
        addLog("OK", "  " + type.toUpperCase() + " loaded successfully.");
      });
    });
  }).catch(function(err) {
    addLog("WARN", "  GeoTIFF decode error: " + err.message);
    drawPH(type === "ms" ? "cv-ms" : "cv-pan", type);
  });
}

// ── Regular image loader ──────────────────────────────
function loadImg(file, type) {
  var url = URL.createObjectURL(file);
  var img = new Image();
  img.onload = function() {
    var cid = type === "ms" ? "cv-ms" : "cv-pan";
    var cv = document.getElementById(cid);
    var ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    var sc = Math.min(cv.width / img.width, cv.height / img.height);
    var sw = img.width * sc, sh = img.height * sc;
    ctx.drawImage(img, (cv.width - sw) / 2, (cv.height - sh) / 2, sw, sh);
    var tmp = document.createElement("canvas");
    tmp.width = cv.width; tmp.height = cv.height;
    var tc = tmp.getContext("2d");
    tc.drawImage(img, (cv.width - sw) / 2, (cv.height - sh) / 2, sw, sh);
    if (type === "ms") {
      S.msData = "rgba"; S.msW = cv.width; S.msH = cv.height; S.msBands = 3;
      S.msRGBA = tc.getImageData(0, 0, cv.width, cv.height);
      document.getElementById("m-ms-b").textContent = "3";
      document.getElementById("m-ms-s").textContent = img.width + " x " + img.height;
      document.getElementById("st-ms").textContent = "3B";
      document.getElementById("ms-ok").classList.add("visible");
    } else {
      S.panData = "rgba"; S.panW = cv.width; S.panH = cv.height;
      document.getElementById("m-pan-s").textContent = img.width + " x " + img.height;
      document.getElementById("st-pan").textContent = img.width + "x" + img.height;
      document.getElementById("pan-ok").classList.add("visible");
    }
    URL.revokeObjectURL(url);
    addLog("OK", "  " + type.toUpperCase() + " rendered.");
  };
  img.src = url;
}

// ── Render GeoTIFF to canvas ──────────────────────────
function renderGTIFF(cid, rasters, srcW, srcH, nb, type) {
  var cv = document.getElementById(cid), ctx = cv.getContext("2d");
  var W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  var id = ctx.createImageData(W, H), d = id.data;
  var b0 = rasters[0], b1 = nb >= 3 ? rasters[1] : rasters[0], b2 = nb >= 3 ? rasters[2] : rasters[0];
  function pc(arr, p) { var s = Float64Array.from(arr).sort(); return s[Math.floor(s.length * (p / 100))]; }
  function mk(arr) { var lo = pc(arr, 2), hi = pc(arr, 98), r = hi - lo + 1e-9; return function(v) { return Math.max(0, Math.min(255, Math.round(((v - lo) / r) * 255))); }; }
  var n0 = mk(b0), n1 = mk(b1), n2 = mk(b2);
  for (var cy = 0; cy < H; cy++) {
    for (var cx = 0; cx < W; cx++) {
      var sx = Math.floor((cx / W) * srcW), sy = Math.floor((cy / H) * srcH);
      var si = sy * srcW + sx, di = (cy * W + cx) * 4;
      if (type === "pan") { var g = n0(b0[si]); d[di] = g; d[di+1] = g; d[di+2] = g; }
      else { d[di] = n0(b0[si]); d[di+1] = n1(b1[si]); d[di+2] = n2(b2[si]); }
      d[di+3] = 255;
    }
  }
  ctx.putImageData(id, 0, 0);
}

// ── Run pipeline ──────────────────────────────────────
document.getElementById("run-btn").addEventListener("click", function() {
  if (!S.msFile || !S.panFile) { addLog("WARN", "Upload both MS and PAN images first."); return; }
  var btn = document.getElementById("run-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="cursor-blink"></span> SENDING TO ENGINE...';
  document.getElementById("prog-wrap").classList.add("visible");
  document.getElementById("dl-area").innerHTML = "";
  S.lastLogCount = 0;
  for (var i = 0; i < 4; i++) {
    var p = document.getElementById("ps-" + i);
    p.classList.remove("running", "done");
    p.querySelector(".stage-status").textContent = "waiting";
  }
  addLog("INFO", "Submitting to Python engine — method: " + S.method.toUpperCase());
  var form = new FormData();
  form.append("ms_file", S.msFile); form.append("pan_file", S.panFile);
  form.append("method", S.method); form.append("resample", document.getElementById("sel-res").value);
  fetch(API + "/run", { method: "POST", body: form })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) { addLog("ERR", "Server error: " + data.error); resetBtn(); return; }
      S.jobId = data.job_id;
      addLog("INFO", "Job accepted — ID: " + S.jobId);
      btn.innerHTML = '<span class="cursor-blink"></span> PROCESSING...';
      startPolling();
    })
    .catch(function(err) {
      addLog("ERR", "Cannot reach server: " + err.message);
      addLog("WARN", "Make sure server.py is running: python server.py");
      resetBtn();
    });
});

// ── Polling ───────────────────────────────────────────
var SNAMES = ["Alignment", "Colour Sep.", "Fusion", "Export"];
function startPolling() { S.pollTimer = setInterval(pollStatus, 800); }
function pollStatus() {
  fetch(API + "/status/" + S.jobId).then(function(r) { return r.json(); }).then(function(data) {
    var logs = data.logs || [];
    for (var i = S.lastLogCount; i < logs.length; i++) addLog(logs[i].level, logs[i].msg);
    S.lastLogCount = logs.length;
    var stage = data.stage || 0;
    for (var s = 0; s < 4; s++) {
      var card = document.getElementById("ps-" + s);
      card.classList.remove("running", "done");
      if (s < stage - 1) { card.classList.add("done"); card.querySelector(".stage-status").textContent = "complete"; }
      else if (s === stage - 1) { card.classList.add("running"); card.querySelector(".stage-status").textContent = "running..."; }
      else { card.querySelector(".stage-status").textContent = "waiting"; }
    }
    var pct = stage > 0 ? Math.round((stage / 4) * 100) : 5;
    document.getElementById("prog-fill").style.width = pct + "%";
    document.getElementById("prog-pct").textContent = pct + "%";
    if (stage > 0) document.getElementById("prog-stage").textContent = SNAMES[stage - 1];
    if (data.status === "complete") { clearInterval(S.pollTimer); pipelineDone(data.result); }
    else if (data.status === "failed") { clearInterval(S.pollTimer); addLog("ERR", "Pipeline failed: " + (data.error || "unknown")); resetBtn(); }
  }).catch(function(err) { addLog("WARN", "Polling error: " + err.message); });
}

// ── Pipeline done ─────────────────────────────────────
function pipelineDone(result) {
  for (var i = 0; i < 4; i++) {
    var p = document.getElementById("ps-" + i);
    p.classList.remove("running"); p.classList.add("done");
    p.querySelector(".stage-status").textContent = "complete";
  }
  document.getElementById("prog-fill").style.width = "100%";
  document.getElementById("prog-pct").textContent = "100%";
  document.getElementById("prog-stage").textContent = "Complete";
  if (result) {
    document.getElementById("st-out").textContent = (result.width || "-") + "x" + (result.height || "-");
    document.getElementById("st-rt").textContent = (result.processing_time_s || "-") + "s";
    document.getElementById("m-method").textContent = (result.method || S.method).toUpperCase();
    document.getElementById("m-fused-s").textContent = (result.width || "-") + " x " + (result.height || "-");
  }
  resetBtn(); showDownloadBtn(result); setTimeout(renderFused, 300);
}

function resetBtn() {
  var btn = document.getElementById("run-btn");
  btn.disabled = false;
  btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg> COMPLETE — RUN AGAIN';
}

// ── Download button ───────────────────────────────────
function showDownloadBtn(result) {
  var area = document.getElementById("dl-area");
  area.innerHTML = "";
  var isReal = result && result.status !== "simulated" && result.output_file && result.output_file !== "simulation_no_output.tif";
  var btn = document.createElement("button");
  btn.className = "btn-download";
  if (isReal) {
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> Download Fused GeoTIFF';
    btn.onclick = function() { window.open(API + "/download/" + S.jobId, "_blank"); addLog("INFO", "Downloading: " + result.output_file); };
    addLog("OK", "Real GeoTIFF ready — click Download to save it.");
  } else {
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg> Download Preview (PNG)';
    btn.onclick = function() {
      var cv = document.getElementById("cv-fused");
      var a = document.createElement("a"); a.download = "pansharp_preview.png"; a.href = cv.toDataURL("image/png"); a.click();
      addLog("INFO", "Preview PNG saved.");
    };
    addLog("WARN", "Running in simulation mode — preview PNG only.");
  }
  var note = document.createElement("div");
  note.className = "download-note";
  note.textContent = isReal ? "GeoTIFF with geographic coordinates — open in QGIS." : "Upload .tif files and ensure engine is in same folder for real output.";
  area.appendChild(btn); area.appendChild(note);
}

// ── Fused canvas render ───────────────────────────────
function renderFused() {
  var cv = document.getElementById("cv-fused"), ctx = cv.getContext("2d"), W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  if (S.msData && S.msData !== "rgba" && S.panData && S.panData !== "rgba") renderBrovey(ctx, W, H);
  else if (S.msRGBA) renderRGBA(ctx, W, H);
  else drawFusedPH(ctx, W, H);
  addLog("OK", "Fused preview rendered on canvas.");
}

function renderBrovey(ctx, W, H) {
  var ms = S.msData, pan = S.panData, mW = S.msW, mH = S.msH, nb = S.msBands, pW = S.panW, pH = S.panH;
  var b0 = ms[0], b1 = nb >= 3 ? ms[1] : ms[0], b2 = nb >= 3 ? ms[2] : ms[0], p0 = pan[0];
  function pc(arr, p) { var s = Float64Array.from(arr).sort(); return s[Math.floor(s.length * (p / 100))]; }
  function mk(arr) { var lo = pc(arr, 2), hi = pc(arr, 98), r = hi - lo + 1e-9; return function(v) { return Math.max(0, Math.min(1, (v - lo) / r)); }; }
  var n0 = mk(b0), n1 = mk(b1), n2 = mk(b2), np = mk(p0);
  var id = ctx.createImageData(W, H), d = id.data;
  for (var cy = 0; cy < H; cy++) {
    for (var cx = 0; cx < W; cx++) {
      var msx = Math.floor((cx / W) * mW), msy = Math.floor((cy / H) * mH);
      var psx = Math.floor((cx / W) * pW), psy = Math.floor((cy / H) * pH);
      var mi = msy * mW + msx, pi = psy * pW + psx, di = (cy * W + cx) * 4;
      var R = n0(b0[mi]), G = n1(b1[mi]), B = n2(b2[mi]), PAN = np(p0[pi]);
      var sum = R + G + B + 1e-9;
      d[di] = Math.min(255, Math.round((R / sum) * PAN * 255));
      d[di+1] = Math.min(255, Math.round((G / sum) * PAN * 255));
      d[di+2] = Math.min(255, Math.round((B / sum) * PAN * 255));
      d[di+3] = 255;
    }
  }
  ctx.putImageData(id, 0, 0);
}

function renderRGBA(ctx, W, H) {
  var src = S.msRGBA.data, id = ctx.createImageData(W, H), d = id.data;
  for (var i = 0; i < W * H; i++) {
    var si = i * 4, di = i * 4;
    d[di] = Math.min(255, Math.round((src[si] - 20) * 1.2));
    d[di+1] = Math.min(255, Math.round((src[si+1] - 15) * 1.22));
    d[di+2] = Math.min(255, Math.round((src[si+2] - 10) * 1.18));
    d[di+3] = 255;
  }
  ctx.putImageData(id, 0, 0);
}

function drawFusedPH(ctx, W, H) {
  var g = ctx.createLinearGradient(0, 0, W, H);
  g.addColorStop(0, "#0A1A14"); g.addColorStop(1, "#142820");
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  ctx.font = "11px 'JetBrains Mono', monospace"; ctx.fillStyle = "rgba(29,184,126,0.5)";
  ctx.fillText("PAN-SHARPENED OUTPUT", 14, H - 14);
}

function drawPH(id, type) {
  var cv = document.getElementById(id), ctx = cv.getContext("2d");
  ctx.fillStyle = type === "pan" ? "#0A0E18" : "#080E16";
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.font = "12px 'Inter', sans-serif"; ctx.fillStyle = "#2A3448"; ctx.textAlign = "center";
  ctx.fillText("Upload " + type.toUpperCase() + " image", cv.width / 2, cv.height / 2);
  ctx.textAlign = "left";
}

// ── Log ───────────────────────────────────────────────
function addLog(level, msg) {
  var now = new Date();
  var ts = [now.getHours(), now.getMinutes(), now.getSeconds()].map(function(x) { return String(x).padStart(2, "0"); }).join(":");
  var box = document.getElementById("log-box");
  var cls = { INFO: "log-level-info", OK: "log-level-ok", WARN: "log-level-warn", ERR: "log-level-err" }[level] || "log-level-info";
  var line = document.createElement("div");
  line.className = "log-line";
  line.innerHTML = '<span class="log-time">' + ts + '</span><span class="' + cls + '">[' + level + ']</span><span class="log-msg">' + msg + '</span>';
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function clearLog() { document.getElementById("log-box").innerHTML = ""; }
