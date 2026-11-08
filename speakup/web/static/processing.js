// SpeakUp — polls the server while a recording is transcribed and analysed.
(function () {
  "use strict";

  const root = document.getElementById("processing");
  if (!root) return;

  const order = ["saving", "transcribing", "analysing", "done"];
  const title = document.getElementById("proc-title");
  const message = document.getElementById("proc-message");
  const actions = document.getElementById("proc-actions");

  function markSteps(stage) {
    const current = order.indexOf(stage);
    root.querySelectorAll("[data-step]").forEach((li) => {
      const idx = order.indexOf(li.dataset.step);
      li.classList.toggle("is-done", idx < current);
      li.classList.toggle("is-active", idx === current);
    });
  }

  async function poll() {
    try {
      const res = await fetch(root.dataset.statusUrl, { credentials: "same-origin" });
      const data = await res.json();
      message.textContent = data.message || "";
      if (data.stage === "done" && data.redirect) {
        markSteps("done");
        title.textContent = "All done! 🎉";
        window.location.assign(data.redirect);
        return;
      }
      if (data.stage === "error") {
        root.classList.add("is-error");
        title.textContent = "We couldn't finish this one";
        actions.hidden = false;
        return;
      }
      markSteps(data.stage);
    } catch (err) {
      message.textContent = "Still working… (reconnecting)";
    }
    setTimeout(poll, 1500);
  }

  poll();
})();
