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

pdfInput.addEventListener("change", () => {
  if (pdfInput.files.length > 0) {
    dropzoneText.textContent = "📄 " + pdfInput.files[0].name;
    generateBtn.disabled = false;
  }
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
