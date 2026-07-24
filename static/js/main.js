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
