// ── State ───────────────────────────────────────────────────────────────
let questions = [];          // current live-quiz questions, sorted by id
let currentEditQid = null;   // which question a modal is currently editing
let pendingDeleteQid = null;
let pendingImageQid = null;

// ── DOM refs ────────────────────────────────────────────────────────────
const backMenuBtnTop = document.getElementById("backMenuBtnTop");
const backMenuBtn = document.getElementById("backMenuBtn");
const updateNowBtn = document.getElementById("updateNowBtn");
const questionsCol = document.getElementById("questionsCol");
const loadingMsg = document.getElementById("loadingMsg");
const questionGrid = document.getElementById("questionGrid");
const toast = document.getElementById("toast");

const editIndexModal = document.getElementById("editIndexModal");
const editIndexInput = document.getElementById("editIndexInput");
const editIndexError = document.getElementById("editIndexError");
const saveIndexBtn = document.getElementById("saveIndexBtn");
const cancelIndexBtn = document.getElementById("cancelIndexBtn");

const editNoteModal = document.getElementById("editNoteModal");
const editNoteInput = document.getElementById("editNoteInput");
const saveNoteBtn = document.getElementById("saveNoteBtn");
const cancelNoteBtn = document.getElementById("cancelNoteBtn");

const editAnswerModal = document.getElementById("editAnswerModal");
const cancelAnswerBtn = document.getElementById("cancelAnswerBtn");

const addQuestionModal = document.getElementById("addQuestionModal");
const addQIndexInput = document.getElementById("addQIndexInput");
const addQImageInput = document.getElementById("addQImageInput");
const addQNoteInput = document.getElementById("addQNoteInput");
const addQError = document.getElementById("addQError");
const saveAddQBtn = document.getElementById("saveAddQBtn");
const cancelAddQBtn = document.getElementById("cancelAddQBtn");
let addQAnswer = "";
let addQInsertAfter = null; // which question id to insert after (null = at very top)

const confirmDeleteModal = document.getElementById("confirmDeleteModal");
const confirmDeleteYes = document.getElementById("confirmDeleteYes");
const confirmDeleteNo = document.getElementById("confirmDeleteNo");

function showToast(message, duration = 2500) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), duration);
}

backMenuBtnTop.addEventListener("click", () => { window.location.href = `/owner/${encodeURIComponent(QUIZ_ID)}`; });
backMenuBtn.addEventListener("click", () => { window.location.href = `/owner/${encodeURIComponent(QUIZ_ID)}`; });

// ── Load + render ───────────────────────────────────────────────────────
async function loadQuizForEdit() {
  loadingMsg.classList.remove("hidden");
  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}`);
  const data = await res.json();
  if (!data.ok) {
    loadingMsg.textContent = "❌ " + data.error;
    return;
  }
  questions = data.questions;
  render();
}

function render() {
  loadingMsg.classList.add("hidden");
  questionsCol.innerHTML = "";

  // "Add question" card at the very top
  questionsCol.appendChild(buildAddQCard(null));

  questions.forEach((q, idx) => {
    questionsCol.appendChild(buildQuestionCard(q, idx));
    questionsCol.appendChild(buildAddQCard(q.id));
  });

  renderGrid();
}

function buildAddQCard(afterId) {
  const div = document.createElement("div");
  div.className = "add-q-card";
  div.innerHTML = `<span>➕ Add Question Here</span>`;
  div.addEventListener("click", () => openAddQuestion(afterId));
  return div;
}

function buildQuestionCard(q, idx) {
  const card = document.createElement("div");
  card.className = "question-panel edit-q-card";
  card.dataset.qid = q.id;
  card.id = `qcard-${q.id}`;

  const noteHtml = q.options_note
    ? `<div class="options-note-display">📝 ${escapeHtml(q.options_note)}</div>`
    : `<div class="options-abcd options-abcd-preview">
         <div class="option-abcd"><span class="option-letter">A</span></div>
         <div class="option-abcd"><span class="option-letter">B</span></div>
         <div class="option-abcd"><span class="option-letter">C</span></div>
         <div class="option-abcd"><span class="option-letter">D</span></div>
       </div>`;

  const answerLabel = q.correct ? q.correct : "None / Subjective";

  card.innerHTML = `
    <div class="q-header edit-q-header">
      <span class="q-badge">${q.id}</span>
      <button type="button" class="pencil-btn edit-index-btn" title="Edit question number">✏️</button>
      <div class="edit-move-btns">
        <button type="button" class="mini-move-btn move-up-btn" title="Move up">▲</button>
        <button type="button" class="mini-move-btn move-down-btn" title="Move down">▼</button>
      </div>
      <button type="button" class="delete-q-btn" title="Remove question">🗑</button>
    </div>

    <div class="question-image-wrap edit-image-wrap">
      <img class="question-image" src="${q.image_url}" alt="Question ${q.id}">
      <button type="button" class="pencil-btn image-pencil-btn" title="Replace image">✏️</button>
    </div>

    <div class="options-edit-row">
      ${noteHtml}
      <button type="button" class="pencil-btn note-pencil-btn" title="Edit options / mark subjective">✏️</button>
    </div>

    <button type="button" class="answer-badge edit-answer-btn">Edit Answer: <strong>${answerLabel}</strong></button>
  `;

  // Wire up interactions
  card.querySelector(".edit-index-btn").addEventListener("click", () => openEditIndex(q.id));
  card.querySelector(".move-up-btn").addEventListener("click", () => moveQuestion(q.id, "up"));
  card.querySelector(".move-down-btn").addEventListener("click", () => moveQuestion(q.id, "down"));
  card.querySelector(".delete-q-btn").addEventListener("click", () => openConfirmDelete(q.id));
  card.querySelector(".image-pencil-btn").addEventListener("click", () => triggerImageReplace(q.id));
  card.querySelector(".note-pencil-btn").addEventListener("click", () => openEditNote(q.id, q.options_note));
  card.querySelector(".edit-answer-btn").addEventListener("click", () => openEditAnswer(q.id));

  return card;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function renderGrid() {
  questionGrid.innerHTML = "";
  questions.forEach((q) => {
    const cell = document.createElement("div");
    cell.className = "grid-item";
    cell.textContent = q.id;
    cell.addEventListener("click", () => {
      const el = document.getElementById(`qcard-${q.id}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    questionGrid.appendChild(cell);
  });
}

// ── Edit index number ───────────────────────────────────────────────────
function openEditIndex(qid) {
  currentEditQid = qid;
  editIndexInput.value = qid;
  editIndexError.classList.add("hidden");
  editIndexModal.classList.remove("hidden");
}
cancelIndexBtn.addEventListener("click", () => editIndexModal.classList.add("hidden"));
saveIndexBtn.addEventListener("click", async () => {
  const val = parseInt(editIndexInput.value, 10);
  if (!Number.isInteger(val) || val <= 0) {
    editIndexError.textContent = "Sirf positive whole number allowed hai.";
    editIndexError.classList.remove("hidden");
    return;
  }
  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${currentEditQid}/reindex`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_id: val }),
  });
  const data = await res.json();
  if (!data.ok) {
    editIndexError.textContent = "❌ " + data.error;
    editIndexError.classList.remove("hidden");
    return;
  }
  editIndexModal.classList.add("hidden");
  showToast("Question number updated ✅");
  await loadQuizForEdit();
});

// ── Move up/down ─────────────────────────────────────────────────────────
async function moveQuestion(qid, direction) {
  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${qid}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ direction }),
  });
  const data = await res.json();
  if (!data.ok) {
    showToast("❌ " + data.error);
    return;
  }
  await loadQuizForEdit();
}

// ── Replace image ───────────────────────────────────────────────────────
function triggerImageReplace(qid) {
  pendingImageQid = qid;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.addEventListener("change", async () => {
    if (!input.files.length) return;
    const fd = new FormData();
    fd.append("image", input.files[0]);
    const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${pendingImageQid}/image`, {
      method: "POST",
      body: fd,
    });
    const data = await res.json();
    if (!data.ok) {
      showToast("❌ " + data.error);
      return;
    }
    showToast("Image updated ✅");
    await loadQuizForEdit();
  });
  input.click();
}

// ── Edit options note (subjective marker) ───────────────────────────────
function openEditNote(qid, currentNote) {
  currentEditQid = qid;
  editNoteInput.value = currentNote || "";
  editNoteModal.classList.remove("hidden");
}
cancelNoteBtn.addEventListener("click", () => editNoteModal.classList.add("hidden"));
saveNoteBtn.addEventListener("click", async () => {
  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${currentEditQid}/options-note`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ options_note: editNoteInput.value }),
  });
  const data = await res.json();
  if (!data.ok) {
    showToast("❌ " + data.error);
    return;
  }
  editNoteModal.classList.add("hidden");
  showToast("Options updated ✅");
  await loadQuizForEdit();
});

// ── Edit answer key ──────────────────────────────────────────────────────
function openEditAnswer(qid) {
  currentEditQid = qid;
  editAnswerModal.classList.remove("hidden");
}
cancelAnswerBtn.addEventListener("click", () => editAnswerModal.classList.add("hidden"));
document.querySelectorAll(".answer-pick:not(.add-q-answer-pick)").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const val = btn.dataset.val || null;
    const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${currentEditQid}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ correct: val }),
    });
    const data = await res.json();
    if (!data.ok) {
      showToast("❌ " + data.error);
      return;
    }
    editAnswerModal.classList.add("hidden");
    showToast("Answer updated ✅");
    await loadQuizForEdit();
  });
});

// ── Delete question ──────────────────────────────────────────────────────
function openConfirmDelete(qid) {
  pendingDeleteQid = qid;
  confirmDeleteModal.classList.remove("hidden");
}
confirmDeleteNo.addEventListener("click", () => confirmDeleteModal.classList.add("hidden"));
confirmDeleteYes.addEventListener("click", async () => {
  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question/${pendingDeleteQid}`, {
    method: "DELETE",
  });
  const data = await res.json();
  confirmDeleteModal.classList.add("hidden");
  if (!data.ok) {
    showToast("❌ " + data.error);
    return;
  }
  showToast("Question removed ✅");
  await loadQuizForEdit();
});

// ── Add question ─────────────────────────────────────────────────────────
function openAddQuestion(afterId) {
  addQInsertAfter = afterId;
  addQIndexInput.value = "";
  addQImageInput.value = "";
  addQNoteInput.value = "";
  addQError.classList.add("hidden");
  addQAnswer = "";
  document.querySelectorAll(".add-q-answer-pick").forEach((b) => b.classList.remove("selected"));
  document.querySelector('.add-q-answer-pick[data-val=""]').classList.add("selected");
  addQuestionModal.classList.remove("hidden");
}
cancelAddQBtn.addEventListener("click", () => addQuestionModal.classList.add("hidden"));
document.querySelectorAll(".add-q-answer-pick").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".add-q-answer-pick").forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
    addQAnswer = btn.dataset.val || "";
  });
});
saveAddQBtn.addEventListener("click", async () => {
  const idxVal = parseInt(addQIndexInput.value, 10);
  if (!Number.isInteger(idxVal) || idxVal <= 0) {
    addQError.textContent = "Question number sirf positive whole number ho sakta hai.";
    addQError.classList.remove("hidden");
    return;
  }
  if (!addQImageInput.files.length) {
    addQError.textContent = "Question image upload karna zaroori hai.";
    addQError.classList.remove("hidden");
    return;
  }

  const fd = new FormData();
  fd.append("new_id", idxVal);
  fd.append("image", addQImageInput.files[0]);
  fd.append("correct", addQAnswer);
  fd.append("options_note", addQNoteInput.value);

  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/question`, {
    method: "POST",
    body: fd,
  });
  const data = await res.json();
  if (!data.ok) {
    addQError.textContent = "❌ " + data.error;
    addQError.classList.remove("hidden");
    return;
  }
  addQuestionModal.classList.add("hidden");
  showToast("Question added ✅");
  await loadQuizForEdit();
});

// ── Update Now ────────────────────────────────────────────────────────────
updateNowBtn.addEventListener("click", async () => {
  if (!questions.length) {
    showToast("❌ Quiz me kam se kam ek question hona chahiye.");
    return;
  }
  updateNowBtn.disabled = true;
  updateNowBtn.textContent = "⏳ Updating...";

  const res = await fetch(`/api/quiz-edit/${encodeURIComponent(QUIZ_ID)}/update-now`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const data = await res.json();
  updateNowBtn.disabled = false;
  updateNowBtn.textContent = "Update Now ✅";

  if (!data.ok) {
    showToast("❌ " + data.error);
    return;
  }
  showToast("Updated database ✅");
  setTimeout(() => {
    window.location.href = `/owner/${encodeURIComponent(QUIZ_ID)}`;
  }, 900);
});

// ── Boot ────────────────────────────────────────────────────────────────
loadQuizForEdit();
