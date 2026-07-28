const toast = document.getElementById("toast");
function showToast(message, duration = 2500) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), duration);
}

document.getElementById("backBtn").addEventListener("click", () => {
  window.location.href = `/play?v=${encodeURIComponent(QUIZ_ID)}`;
});

// ── View Original Link ──────────────────────────────────────────────────
const viewLinkModal = document.getElementById("viewLinkModal");
document.getElementById("viewLinkBtn").addEventListener("click", () => {
  viewLinkModal.classList.remove("hidden");
});
document.getElementById("closeLinkBtn").addEventListener("click", () => {
  viewLinkModal.classList.add("hidden");
});
document.getElementById("copyLinkBtn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(PLAY_LINK);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = PLAY_LINK;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
  showToast("Link copied ✅");
});

// ── Delete Quiz Link ─────────────────────────────────────────────────────
const deleteConfirmModal = document.getElementById("deleteConfirmModal");
const deleteKeyModal = document.getElementById("deleteKeyModal");
const deleteKeyInput = document.getElementById("deleteKeyInput");
const deleteKeyError = document.getElementById("deleteKeyError");

document.getElementById("deleteQuizBtn").addEventListener("click", () => {
  deleteConfirmModal.classList.remove("hidden");
});
document.getElementById("deleteConfirmNo").addEventListener("click", () => {
  deleteConfirmModal.classList.add("hidden");
});
document.getElementById("deleteConfirmYes").addEventListener("click", () => {
  deleteConfirmModal.classList.add("hidden");
  deleteKeyInput.value = "";
  deleteKeyError.classList.add("hidden");
  deleteKeyModal.classList.remove("hidden");
});
document.getElementById("deleteKeyCancelBtn").addEventListener("click", () => {
  deleteKeyModal.classList.add("hidden");
});
document.getElementById("deleteKeySaveBtn").addEventListener("click", async () => {
  const val = deleteKeyInput.value.trim();
  if (!val) {
    deleteKeyError.textContent = "Confirmation Key likhna zaroori hai.";
    deleteKeyError.classList.remove("hidden");
    return;
  }
  const res = await fetch(`/api/owner/${encodeURIComponent(QUIZ_ID)}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_key: val }),
  });
  const data = await res.json();
  if (!data.ok) {
    // Wrong key -> silently return to the dashboard page.
    deleteKeyModal.classList.add("hidden");
    return;
  }
  showToast("Quiz link deleted ✅");
  setTimeout(() => {
    window.location.href = "/";
  }, 1200);
});
