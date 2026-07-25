// ── STRICT ADMIN LOGIN PORTAL ───────────────────────────────────────────
// Case-sensitive on purpose. Any ONE of the listed Admin Keys / VIP Keys
// is accepted, but the Owner Name must match exactly.
// NOTE: this check runs in the browser, so treat it as a simple access
// gate (not cryptographic security) — anyone with page-source access
// could read these values. Good enough for "keep casual visitors out",
// not for protecting truly sensitive data.
const OWNER_NAME = "ViPxMSvBRO";
const ADMIN_KEYS = ["MS#nEET_X9q!7LvP2", "NeeT$MS_A4r!8QxZ5", "mS@NeeT_K7#vP3Lx9"];
const VIP_KEYS = ["ToXic#ViP_X9q!7LvP2", "tOxic@Vip_A4r!8QxZ5", "ToXic$ViP_K7#vP3Lx9"];
const GET_KEYS_URL = "https://t.me/JapaneseFury";

const loginGate = document.getElementById("loginGate");
const uploadWrapper = document.getElementById("uploadWrapper");
const ownerNameInput = document.getElementById("ownerNameInput");
const adminKeyInput = document.getElementById("adminKeyInput");
const vipKeyInput = document.getElementById("vipKeyInput");
const loginError = document.getElementById("loginError");
const loginBtn = document.getElementById("loginBtn");
const getKeysBtn = document.getElementById("getKeysBtn");

loginBtn.addEventListener("click", () => {
  const nameOk = ownerNameInput.value === OWNER_NAME;               // exact, case-sensitive
  const adminOk = ADMIN_KEYS.includes(adminKeyInput.value);         // exact, case-sensitive
  const vipOk = VIP_KEYS.includes(vipKeyInput.value);               // exact, case-sensitive

  if (nameOk && adminOk && vipOk) {
    loginError.classList.add("hidden");
    loginGate.classList.add("hidden");
    uploadWrapper.classList.remove("hidden");
  } else {
    loginError.textContent = "❌ Invalid Name / Admin Key / VIP Key. Check karo aur dobara try karo.";
    loginError.classList.remove("hidden");
  }
});

getKeysBtn.addEventListener("click", () => {
  window.open(GET_KEYS_URL, "_blank");
});

// ── Existing upload page logic (unchanged) ──────────────────────────────
const pdfInput = document.getElementById("pdfInput");
const dropzone = document.getElementById("dropzone");
const dropzoneText = document.getElementById("dropzoneText");
const generateBtn = document.getElementById("generateBtn");
const uploadForm = document.getElementById("uploadForm");
const statusBox = document.getElementById("statusBox");
const toast = document.getElementById("toast");

dropzone.addEventListener("click", () => pdfInput.click());

// ── Filename preview + edit (new) ───────────────────────────────────────
const namePreviewSection = document.getElementById("namePreviewSection");
const displayFileName = document.getElementById("displayFileName");
const copyFileNameBtn = document.getElementById("copyFileNameBtn");
const editFileNameBtn = document.getElementById("editFileNameBtn");

const editConfirmModal = document.getElementById("editConfirmModal");
const editConfirmYes = document.getElementById("editConfirmYes");
const editConfirmNo = document.getElementById("editConfirmNo");

const editNameModal = document.getElementById("editNameModal");
const editOriginalNameText = document.getElementById("editOriginalNameText");
const editedNameInput = document.getElementById("editedNameInput");
const editNameError = document.getElementById("editNameError");
const saveEditedNameBtn = document.getElementById("saveEditedNameBtn");
const cancelEditedNameBtn = document.getElementById("cancelEditedNameBtn");

let originalFileName = "";  // auto-hyphenated version of whatever was uploaded
let activeFileName = "";    // the name actually used for the quiz link (original or edited)

// URLs can't contain raw spaces — auto-convert them to hyphens, same rule
// the backend applies as a safety net too.
function sanitizeToSlug(name) {
  return name.replace(/\.pdf$/i, "").trim().replace(/\s+/g, "-");
}

// Letters (any script incl. Hindi) + combining marks (matras etc.) + digits
// + hyphen/underscore only. No spaces, no emoji, no special characters,
// max 100 chars. \p{M} is required or valid Hindi names like
// "उसने-कहा-था" would be wrongly rejected (matras are combining marks,
// not letters, in Unicode).
const EDIT_NAME_RE = /^[\p{L}\p{M}\p{N}_-]{1,100}$/u;

pdfInput.addEventListener("change", () => {
  if (pdfInput.files.length > 0) {
    const rawName = pdfInput.files[0].name;
    originalFileName = sanitizeToSlug(rawName);
    activeFileName = originalFileName;

    dropzoneText.textContent = "📄 " + rawName;
    displayFileName.textContent = originalFileName;
    namePreviewSection.classList.remove("hidden");
    generateBtn.disabled = false;
  }
});

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
}

copyFileNameBtn.addEventListener("click", async () => {
  await copyText(activeFileName);
  showToast("Copied ✅!");
});

editFileNameBtn.addEventListener("click", () => {
  editConfirmModal.classList.remove("hidden");
});

editConfirmNo.addEventListener("click", () => {
  editConfirmModal.classList.add("hidden");
});

editConfirmYes.addEventListener("click", () => {
  editConfirmModal.classList.add("hidden");
  editOriginalNameText.textContent = originalFileName;
  editedNameInput.value = activeFileName;
  editNameError.classList.add("hidden");
  editNameModal.classList.remove("hidden");
});

saveEditedNameBtn.addEventListener("click", () => {
  const val = editedNameInput.value.trim();
  if (!EDIT_NAME_RE.test(val)) {
    editNameError.textContent = "Naam sirf letters, numbers, hyphen(-) aur underscore(_) allowed hai — spaces, emoji ya special characters (€¥~$¢§π∆% etc.) nahi chalenge. Max 100 characters.";
    editNameError.classList.remove("hidden");
    return;
  }
  activeFileName = val;
  displayFileName.textContent = activeFileName;
  editNameModal.classList.add("hidden");
});

cancelEditedNameBtn.addEventListener("click", () => {
  activeFileName = originalFileName;
  displayFileName.textContent = activeFileName;
  editNameModal.classList.add("hidden");
});

function showToast(message, duration = 3000) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), duration);
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!pdfInput.files.length) return;

  generateBtn.disabled = true;
  generateBtn.textContent = "⏳ Generating...";
  statusBox.classList.add("hidden");

  const formData = new FormData();
  formData.append("pdf_file", pdfInput.files[0]);
  formData.append("desired_name", activeFileName);

  try {
    const res = await fetch("/upload", { method: "POST", body: formData });
    const data = await res.json();

    if (!data.ok) {
      statusBox.textContent = "❌ " + data.error;
      statusBox.classList.remove("hidden");
      statusBox.classList.add("error");
      generateBtn.disabled = false;
      generateBtn.textContent = "⚡ GENERATE QUIZ";
      return;
    }

    showToast("Yummy 😋 Your Quiz Link is Generated Successfully ✅");
    setTimeout(() => {
      window.location.href = "/generated/" + data.quiz_id;
    }, 1200);

  } catch (err) {
    statusBox.textContent = "❌ Something went wrong: " + err.message;
    statusBox.classList.remove("hidden");
    statusBox.classList.add("error");
    generateBtn.disabled = false;
    generateBtn.textContent = "⚡ GENERATE QUIZ";
  }
});
