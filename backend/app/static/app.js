// Phone page: welcome splash -> UPI -> live session -> thank you.
(function () {
  const machineId = document.getElementById("app").dataset.machineId;
  const storageKey = "rvm_session_" + machineId;
  const upiRe = /^[a-zA-Z0-9._-]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$/;  // keep in sync with UPI_RE in user.py
  const $ = (id) => document.getElementById(id);
  const WELCOME_MS = 4500;  // splash auto-advances to the UPI form
  let pollTimer = null;
  let lastCounts = null;

  // ---------- Falling cans & bottles ----------

  const BOTTLE_SVG = `
    <svg viewBox="0 0 30 70" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="0" width="10" height="7" rx="2" fill="#1565c0"/>
      <path d="M11 7h8v5c0 3 7 6 7 13v38c0 4-3 7-7 7h-8c-4 0-7-3-7-7V25c0-7 7-10 7-13z"
            fill="rgba(129,212,250,.8)" stroke="#29b6f6" stroke-width="1"/>
      <rect x="4" y="34" width="22" height="12" fill="#26a69a"/>
      <rect x="7" y="24" width="3" height="34" rx="1.5" fill="rgba(255,255,255,.65)"/>
    </svg>`;

  const CAN_COLORS = ["#e53935", "#43a047", "#1e88e5", "#fb8c00", "#8e24aa"];
  const canSvg = (band) => `
    <svg viewBox="0 0 30 50" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="4" width="26" height="42" rx="4" fill="#cfd8dc"/>
      <rect x="2" y="15" width="26" height="20" fill="${band}"/>
      <ellipse cx="15" cy="46" rx="13" ry="3.5" fill="#b0bec5"/>
      <ellipse cx="15" cy="4" rx="13" ry="3.5" fill="#eceff1" stroke="#90a4ae" stroke-width="1"/>
      <rect x="6" y="6" width="3" height="38" rx="1.5" fill="rgba(255,255,255,.7)"/>
    </svg>`;

  const rand = (min, max) => min + Math.random() * (max - min);

  // Drops `count` items from the top over ~`spreadMs`. type: "bottle" | "can" | "mix"
  function rain(count, { spreadMs = 1800, type = "mix" } = {}) {
    const box = $("rain");
    for (let i = 0; i < count; i++) {
      const isCan = type === "can" || (type === "mix" && Math.random() < 0.5);
      const el = document.createElement("div");
      el.className = "drop";
      el.innerHTML = isCan ? canSvg(CAN_COLORS[i % CAN_COLORS.length]) : BOTTLE_SVG;
      el.style.left = rand(-2, 96) + "vw";
      el.style.width = (isCan ? rand(24, 34) : rand(20, 30)) + "px";
      el.style.setProperty("--dx", rand(-40, 40) + "px");
      el.style.setProperty("--r0", rand(-40, 40) + "deg");
      el.style.setProperty("--r1", rand(-360, 360) + "deg");
      el.style.animationDuration = rand(2.2, 4) + "s";
      el.style.animationDelay = rand(0, spreadMs) + "ms";
      el.addEventListener("animationend", () => el.remove());
      box.appendChild(el);
    }
  }

  // ---------- Views ----------

  const VIEWS = ["welcome-view", "start-view", "active-view", "done-view"];
  let currentView = "welcome-view";

  function show(view) {
    if (view === currentView) return;
    currentView = view;
    VIEWS.forEach((v) => $(v).classList.toggle("hidden", v !== view));
  }

  function showError(msg) {
    $("error-box").innerText = msg || "";
    $("error-box").classList.toggle("hidden", !msg);
  }

  function saveSession(id) {
    try { id ? localStorage.setItem(storageKey, id) : localStorage.removeItem(storageKey); } catch (e) {}
  }

  function loadSession() {
    try { return localStorage.getItem(storageKey); } catch (e) { return null; }
  }

  function bump(id) {
    const el = $(id);
    el.classList.remove("bump");
    void el.offsetWidth;  // restart the animation
    el.classList.add("bump");
  }

  // Pops a counter and drops a few matching items when the machine accepts something.
  function celebrateNewItems(s) {
    if (lastCounts) {
      const newBottles = s.bottle_count - lastCounts.bottle_count;
      const newCans = s.can_count - lastCounts.can_count;
      if (newBottles > 0) { bump("bottle-count"); rain(4 * newBottles, { type: "bottle", spreadMs: 600 }); }
      if (newCans > 0) { bump("can-count"); rain(4 * newCans, { type: "can", spreadMs: 600 }); }
      if (newBottles > 0 || newCans > 0) bump("total-amount");
    }
    lastCounts = { bottle_count: s.bottle_count, can_count: s.can_count };
  }

  function render(s) {
    $("bottle-count").innerText = s.bottle_count;
    $("can-count").innerText = s.can_count;
    $("total-amount").innerText = "₹" + s.total_amount;
    $("upi-masked").innerText = s.upi_masked;
    if (s.status === "active") {
      celebrateNewItems(s);
      show("active-view");
      return;
    }

    stopPolling();
    saveSession(null);
    lastCounts = null;
    $("done-title").innerText = "Thanks for using RVM!";
    if (s.payout_status === "success") {
      $("done-icon").innerText = "💚";
      $("done-text").innerText = `₹${s.total_amount} sent to ${s.upi_masked}, ref ${s.payout_ref}`;
    } else if (s.payout_status === "skipped") {
      $("done-icon").innerText = "💚";
      $("done-text").innerText = "No items deposited this time.";
    } else {
      // pending or failed: the operator sees the details on /admin and can refresh the status
      $("done-icon").innerText = "⏳";
      $("done-text").innerText = `Payment processing: ₹${s.total_amount} to ${s.upi_masked}` +
        (s.payout_ref ? `, ref ${s.payout_ref}` : "");
    }
    rain(16, { spreadMs: 1500 });
    $("done-counts").innerText = `${s.bottle_count} bottle(s), ${s.can_count} can(s) recycled`;
    const showProvider = s.provider === "cashfree" && s.provider_status;
    $("done-provider").classList.toggle("hidden", !showProvider);
    if (showProvider) {
      $("done-provider").innerText = `Cashfree: ${s.provider_status}` + (s.provider_ref ? ` · ${s.provider_ref}` : "");
    }
    show("done-view");
  }

  // ---------- Server polling ----------

  async function poll(sessionId) {
    try {
      const res = await fetch("/api/session/" + sessionId);
      const data = await res.json();
      if (!data.ok) { saveSession(null); stopPolling(); show("start-view"); return; }
      showError("");
      render(data);
    } catch (e) {
      showError("Connection lost, retrying...");
    }
  }

  function startPolling(sessionId) {
    stopPolling();
    poll(sessionId);
    pollTimer = setInterval(() => poll(sessionId), 2000);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  // ---------- Buttons ----------

  function goToUpi() {
    if (currentView !== "welcome-view") return;
    show("start-view");
    $("upi-id").focus();
  }

  $("welcome-btn").addEventListener("click", goToUpi);

  $("start-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const upiId = $("upi-id").value.trim();
    if (!upiRe.test(upiId)) { showError("Please enter a valid UPI ID like test@ybl (not an email)."); return; }

    const btn = $("start-btn");
    btn.disabled = true;
    btn.innerText = "Starting...";
    showError("");
    try {
      const res = await fetch("/api/session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ machine_id: machineId, upi_id: upiId }),
      });
      const data = await res.json();
      if (!data.ok) { showError(data.error); return; }
      saveSession(data.session_id);
      lastCounts = null;
      startPolling(data.session_id);
    } catch (err) {
      showError("Unable to reach the server.");
    } finally {
      btn.disabled = false;
      btn.innerText = "Start";
    }
  });

  $("done-btn").addEventListener("click", async () => {
    const sessionId = loadSession();
    if (!sessionId) return;
    const btn = $("done-btn");
    btn.disabled = true;
    btn.innerText = "Finishing...";
    try {
      const res = await fetch("/api/session/" + sessionId + "/end", { method: "POST" });
      const data = await res.json();
      if (data.ok) render(data); else showError(data.error);
    } catch (err) {
      showError("Unable to reach the server.");
    } finally {
      btn.disabled = false;
      btn.innerText = "Done";
    }
  });

  $("again-btn").addEventListener("click", () => {
    $("upi-id").value = "";
    showError("");
    show("start-view");
  });

  // ---------- Start ----------

  const existing = loadSession();
  if (existing) {
    // Page reloaded mid-session: skip the splash
    show("start-view");
    startPolling(existing);
  } else {
    rain(28, { spreadMs: 2500 });
    setTimeout(goToUpi, WELCOME_MS);
  }
})();
