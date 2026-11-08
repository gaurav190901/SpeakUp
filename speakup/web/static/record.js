// SpeakUp — in-browser recorder for the Practice page.
(function () {
  "use strict";

  const root = document.getElementById("practice");
  if (!root) return;

  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const minSeconds = Number(root.dataset.minSeconds) || 60;
  const maxSeconds = Number(root.dataset.maxSeconds) || 0;

  const el = {
    start: document.getElementById("btn-start"),
    stop: document.getElementById("btn-stop"),
    abandon: document.getElementById("btn-abandon"),
    timer: document.getElementById("timer"),
    note: document.getElementById("timer-note"),
    progress: document.getElementById("progress"),
    level: document.getElementById("level"),
    error: document.getElementById("rec-error"),
    camera: document.getElementById("camera"),
  };

  let stream = null;
  let recorder = null;
  let chunks = [];
  let startedAt = 0;
  let tick = null;
  let audioCtx = null;
  let rafId = null;
  let state = "idle";

  function setState(next) {
    state = next;
    document.querySelectorAll("[data-state]").forEach((node) => {
      node.hidden = node.dataset.state !== next;
    });
    document.querySelectorAll("[data-recording-hide]").forEach((node) => {
      node.hidden = next !== "idle";
    });
    root.classList.toggle("is-recording", next === "recording");
  }

  function showError(message) {
    el.error.textContent = "⚠️ " + message;
    el.error.hidden = false;
  }

  function fmt(seconds) {
    const s = Math.floor(seconds);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "X-CSRF-Token": csrf },
      body: body,
      credentials: "same-origin",
    }).then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "The server couldn't save that. Please try again.");
      return data;
    });
  }

  function pickMimeType() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    return types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
  }

  async function getStream() {
    const audio = { echoCancellation: true, noiseSuppression: true };
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: audio, video: { width: 640 } });
    } catch (err) {
      // No camera (or camera blocked) — audio is all we really need.
      return navigator.mediaDevices.getUserMedia({ audio: audio });
    }
  }

  function startLevelMeter(audioStream) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(audioStream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      const draw = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
        el.level.style.setProperty("--w", Math.min(100, (peak / 64) * 100) + "%");
        rafId = requestAnimationFrame(draw);
      };
      draw();
    } catch (err) {
      /* level meter is optional */
    }
  }

  function stopDevices() {
    if (tick) clearInterval(tick);
    if (rafId) cancelAnimationFrame(rafId);
    if (audioCtx) audioCtx.close().catch(() => {});
    if (stream) stream.getTracks().forEach((t) => t.stop());
    tick = rafId = audioCtx = stream = null;
  }

  function updateTimer() {
    const elapsed = (Date.now() - startedAt) / 1000;
    el.timer.textContent = fmt(elapsed);
    const pct = Math.min(100, (elapsed / minSeconds) * 100);
    el.progress.style.setProperty("--w", pct + "%");
    if (elapsed < minSeconds) {
      el.note.textContent = "Keep going — " + fmt(minSeconds - elapsed) + " to go";
      el.stop.disabled = true;
    } else {
      el.note.textContent = "✓ Great! Finish whenever you're ready.";
      el.progress.classList.add("is-done");
      el.stop.disabled = false;
    }
    if (maxSeconds > 0) {
      const left = maxSeconds - elapsed;
      if (left <= 60 && left > 0) el.note.textContent = "Wrapping up — " + fmt(left) + " left";
      if (left <= 0) finish();
    }
  }

  async function start() {
    el.error.hidden = true;
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      showError("This browser can't record audio. Please use a recent Chrome, Edge or Firefox.");
      return;
    }
    el.start.disabled = true;
    try {
      stream = await getStream();
    } catch (err) {
      el.start.disabled = false;
      showError("We need your microphone. Allow microphone access in the browser's address bar, then try again.");
      return;
    }

    try {
      await post(root.dataset.startUrl);
    } catch (err) {
      stopDevices();
      el.start.disabled = false;
      showError(err.message);
      return;
    }

    if (stream.getVideoTracks().length) {
      el.camera.srcObject = stream;
    }
    const audioOnly = new MediaStream(stream.getAudioTracks());
    const mimeType = pickMimeType();
    recorder = new MediaRecorder(audioOnly, mimeType ? { mimeType: mimeType } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = upload;
    recorder.start(1000);

    startedAt = Date.now();
    setState("recording");
    if (stream.getVideoTracks().length === 0) {
      document.querySelector(".camera").hidden = true;
    }
    startLevelMeter(audioOnly);
    updateTimer();
    tick = setInterval(updateTimer, 250);
  }

  function finish() {
    if (!recorder || recorder.state === "inactive") return;
    if (tick) clearInterval(tick);
    el.stop.disabled = true;
    el.stop.textContent = "Uploading…";
    el.abandon.hidden = true;
    recorder.stop();
  }

  async function upload() {
    const duration = (Date.now() - startedAt) / 1000;
    const type = recorder.mimeType || "audio/webm";
    stopDevices();
    const blob = new Blob(chunks, { type: type });
    const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
    const form = new FormData();
    form.append("audio", blob, "recording." + ext);
    form.append("duration", duration.toFixed(1));
    try {
      const data = await post(root.dataset.uploadUrl, form);
      state = "done";
      window.location.assign(data.redirect);
    } catch (err) {
      showError(err.message);
      el.stop.textContent = "⏹ Finish & get feedback";
    }
  }

  async function abandon() {
    if (!window.confirm("Cancel this recording? Nothing will be saved.")) return;
    if (recorder && recorder.state !== "inactive") {
      recorder.onstop = null;
      recorder.stop();
    }
    stopDevices();
    state = "done";
    try {
      const data = await post(root.dataset.abandonUrl);
      window.location.assign(data.redirect);
    } catch (err) {
      window.location.reload();
    }
  }

  el.start.addEventListener("click", start);
  el.stop.addEventListener("click", finish);
  el.abandon.addEventListener("click", abandon);
  window.addEventListener("beforeunload", (e) => {
    if (state === "recording") {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  setState("idle");
})();
