// ── Admin page deterrents (UI-level only) ───────────────────────────────
// These block casual right-click / DevTools-shortcut access as a light
// deterrent. They are NOT real security — anyone can still open DevTools
// via the browser menu, a different shortcut, or a separate tool (e.g.
// eruda). The actual protection is that admin keys are validated
// server-side (see /login in app.py) and never sent to this page, so
// there is nothing sensitive left to view even with DevTools open.

document.addEventListener("contextmenu", (e) => e.preventDefault());

document.addEventListener("keydown", (e) => {
  const key = (e.key || "").toUpperCase();
  const blockCombo =
    key === "F12" ||
    (e.ctrlKey && e.shiftKey && (key === "I" || key === "J" || key === "C")) ||
    (e.ctrlKey && key === "U");
  if (blockCombo) {
    e.preventDefault();
  }
});
