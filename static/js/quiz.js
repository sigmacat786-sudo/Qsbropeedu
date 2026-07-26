// ── State ───────────────────────────────────────────────────────────────
let questions = [];
let currentIndex = 0;
let answers = {};        // { "1": "A", "2": null, ... }
let visited = {};        // { "1": true } -> answered or explicitly skipped
let startTime = null;
let timerInterval = null;
let solutionsData = [];
let userName = "";
let lastResult = null;   // stores latest submit() response for the download button

function getUserId() {
  let id = localStorage.getItem("smartyms_user_id");
  if (!id) {
    id = "user-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("smartyms_user_id", id);
  }
  return id;
}

// ── DOM refs ────────────────────────────────────────────────────────────
const nameGate = document.getElementById("nameGate");
const nameInput = document.getElementById("nameInput");
const nameError = document.getElementById("nameError");
const saveAttendBtn = document.getElementById("saveAttendBtn");
const helpBtn = document.getElementById("helpBtn");
const quizWrapper = document.getElementById("quizWrapper");

const qNumberBadge = document.getElementById("qNumberBadge");
const questionImage = document.getElementById("questionImage");
const optionsList = document.getElementById("optionsList");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const skipBtn = document.getElementById("skipBtn");
const submitBtn = document.getElementById("submitBtn");
const backBtn = document.getElementById("backBtn");
const timerEl = document.getElementById("timer");
const questionGrid = document.getElementById("questionGrid");
const statCorrect = document.getElementById("statCorrect");
const statIncorrect = document.getElementById("statIncorrect");
const statNotAnswered = document.getElementById("statNotAnswered");

const confirmSubmitModal = document.getElementById("confirmSubmitModal");
const confirmYesBtn = document.getElementById("confirmYesBtn");
const confirmNoBtn = document.getElementById("confirmNoBtn");

const resultModal = document.getElementById("resultModal");
const resName = document.getElementById("resName");
const scoreValue = document.getElementById("scoreValue");
const scorePercent = document.getElementById("scorePercent");
const resMessage = document.getElementById("resMessage");
const resCorrect = document.getElementById("resCorrect");
const resIncorrect = document.getElementById("resIncorrect");
const resSkipped = document.getElementById("resSkipped");
const resTime = document.getElementById("resTime");
const reattemptBtn = document.getElementById("reattemptBtn");
const viewSolutionsBtn = document.getElementById("viewSolutionsBtn");
const downloadScoreBtn = document.getElementById("downloadScoreBtn");

const solutionsModal = document.getElementById("solutionsModal");
const solutionsListEl = document.getElementById("solutionsList");
const closeSolutionsBtn = document.getElementById("closeSolutionsBtn");

// ── Name gate ───────────────────────────────────────────────────────────
// Blocks emoji / pictograph characters; letters, numbers, spaces, basic punctuation allowed.
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{1F1E6}-\u{1F1FF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}]/u;

function validateName(value) {
  const trimmed = value.trim();
  if (!trimmed) return "Naam likhna zaroori hai.";
  if (trimmed.length > 50) return "Naam 50 characters se zyada nahi ho sakta.";
  if (EMOJI_RE.test(trimmed)) return "Naam me emoji allowed nahi hai.";
  return null;
}

saveAttendBtn.addEventListener("click", () => {
  const err = validateName(nameInput.value);
  if (err) {
    nameError.textContent = err;
    nameError.classList.remove("hidden");
    return;
  }
  userName = nameInput.value.trim();
  nameGate.classList.add("hidden");
  quizWrapper.classList.remove("hidden");
  loadQuiz();
});

helpBtn.addEventListener("click", () => {
  window.open(HELP_URL, "_blank");
});

// ── Init ────────────────────────────────────────────────────────────────
async function loadQuiz() {
  const res = await fetch(`/api/quiz/${encodeURIComponent(QUIZ_ID)}`);
  const data = await res.json();
  if (!data.ok) {
    questionImage.alt = "Quiz load nahi hua: " + data.error;
    return;
  }
  questions = data.questions;
  questions.forEach(q => { answers[q.id] = null; visited[q.id] = false; });

  buildQuestionGrid();
  renderQuestion(0);
  startTimer();
}

function startTimer() {
  startTime = Date.now();
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const h = String(Math.floor(elapsed / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    timerEl.textContent = `${h}:${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
  return timerEl.textContent;
}

// ── Rendering ───────────────────────────────────────────────────────────
function renderQuestion(index) {
  currentIndex = index;
  const q = questions[index];
  qNumberBadge.textContent = q.id;
  questionImage.src = q.image_url;
  questionImage.alt = `Question ${q.id}`;

  optionsList.innerHTML = "";

  if (q.options_note) {
    const note = document.createElement("div");
    note.className = "options-note-display";
    note.textContent = q.options_note;
    optionsList.appendChild(note);
  } else {
    ["A", "B", "C", "D"].forEach((letter) => {
      const div = document.createElement("div");
      div.className = "option-item option-abcd" + (answers[q.id] === letter ? " selected" : "");
      div.innerHTML = `<span class="option-letter">${letter}</span>`;
      div.addEventListener("click", () => selectOption(q.id, letter));
      optionsList.appendChild(div);
    });
  }

  prevBtn.disabled = index === 0;
  nextBtn.textContent = index === questions.length - 1 ? "Finish" : "Next";
  updateGridHighlight();
}

function selectOption(qid, letter) {
  answers[qid] = letter;
  visited[qid] = true;
  renderQuestion(currentIndex);
  updateStats();
}

function buildQuestionGrid() {
  questionGrid.innerHTML = "";
  questions.forEach((q, i) => {
    const cell = document.createElement("div");
    cell.className = "grid-item";
    cell.textContent = q.id;
    cell.dataset.qid = q.id;
    cell.addEventListener("click", () => renderQuestion(i));
    questionGrid.appendChild(cell);
  });
}

function updateGridHighlight() {
  document.querySelectorAll(".grid-item").forEach((cell) => {
    const qid = parseInt(cell.dataset.qid);
    cell.classList.remove("current", "answered", "skipped");
    if (answers[qid]) cell.classList.add("answered");
    else if (visited[qid]) cell.classList.add("skipped");
    if (questions[currentIndex].id === qid) cell.classList.add("current");
  });
}

function updateStats() {
  const answeredCount = Object.values(answers).filter(a => a).length;
  const notAnswered = questions.length - answeredCount;
  statNotAnswered.textContent = notAnswered;
}

// ── Navigation ──────────────────────────────────────────────────────────
prevBtn.addEventListener("click", () => {
  if (currentIndex > 0) renderQuestion(currentIndex - 1);
});

nextBtn.addEventListener("click", () => {
  const q = questions[currentIndex];
  visited[q.id] = true;
  if (currentIndex < questions.length - 1) {
    renderQuestion(currentIndex + 1);
  } else {
    openConfirmSubmit();
  }
  updateGridHighlight();
});

skipBtn.addEventListener("click", () => {
  const q = questions[currentIndex];
  answers[q.id] = null;
  visited[q.id] = true;
  if (currentIndex < questions.length - 1) {
    renderQuestion(currentIndex + 1);
  }
  updateStats();
  updateGridHighlight();
});

backBtn.addEventListener("click", () => {
  window.location.href = "/";
});

// ── Submit confirmation ─────────────────────────────────────────────────
submitBtn.addEventListener("click", openConfirmSubmit);

function openConfirmSubmit() {
  confirmSubmitModal.classList.remove("hidden");
}

confirmNoBtn.addEventListener("click", () => {
  confirmSubmitModal.classList.add("hidden");
});

confirmYesBtn.addEventListener("click", () => {
  confirmSubmitModal.classList.add("hidden");
  submitQuiz();
});

// ── Submit / Results ────────────────────────────────────────────────────
async function submitQuiz() {
  const timeTakenStr = stopTimer();
  const [h, m, s] = timeTakenStr.split(":").map(Number);
  const totalSeconds = h * 3600 + m * 60 + s;

  const res = await fetch(`/api/submit/${encodeURIComponent(QUIZ_ID)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      answers,
      time_taken_seconds: totalSeconds,
      user_id: getUserId(),
      name: userName,
    }),
  });
  const data = await res.json();
  if (!data.ok) {
    alert("Submit failed: " + data.error);
    return;
  }

  lastResult = data;
  lastResult.time_taken_str = timeTakenStr;
  solutionsData = data.solutions;

  resName.textContent = userName;
  scoreValue.textContent = `${data.marks_obtained} / ${data.total_marks}`;
  scorePercent.textContent = data.percentage + "%";
  resMessage.textContent = data.message;
  resCorrect.textContent = data.correct;
  resIncorrect.textContent = data.incorrect;
  resSkipped.textContent = data.not_answered;
  resTime.textContent = timeTakenStr;

  statCorrect.textContent = data.correct;
  statIncorrect.textContent = data.incorrect;
  statNotAnswered.textContent = data.not_answered;

  resultModal.classList.remove("hidden");
}

reattemptBtn.addEventListener("click", () => {
  answers = {};
  visited = {};
  questions.forEach(q => { answers[q.id] = null; visited[q.id] = false; });
  statCorrect.textContent = 0;
  statIncorrect.textContent = 0;
  statNotAnswered.textContent = questions.length;
  resultModal.classList.add("hidden");
  renderQuestion(0);
  startTimer();
});

viewSolutionsBtn.addEventListener("click", () => {
  renderSolutions();
  resultModal.classList.add("hidden");
  solutionsModal.classList.remove("hidden");
});

closeSolutionsBtn.addEventListener("click", () => {
  solutionsModal.classList.add("hidden");
  resultModal.classList.remove("hidden");
});

function renderSolutions() {
  solutionsListEl.innerHTML = "";
  solutionsData.forEach((s) => {
    const div = document.createElement("div");
    div.className = "solution-item";

    let bottomHtml;
    if (s.options_note) {
      bottomHtml = `
        <div class="options-note-display" style="margin-bottom:8px;">${escapeHtmlLocal(s.options_note)}</div>
        <div style="font-size:12px; color:#8a8fae;">📝 It's Subjective Question so Marks Can't Be Counted!</div>
      `;
    } else {
      let chipsHtml = "";
      ["A", "B", "C", "D"].forEach((letter) => {
        let cls = "";
        if (letter === s.correct) cls = "correct-ans";
        else if (letter === s.chosen && letter !== s.correct) cls = "wrong-chosen";
        chipsHtml += `<span class="solution-chip ${cls}">${letter}</span>`;
      });
      const statusLabel = s.status === "correct" ? "✅ Correct"
        : s.status === "incorrect" ? "❌ Incorrect" : "⏭ Skipped";
      bottomHtml = `
        <div class="solution-chips">${chipsHtml}</div>
        <div style="margin-top:8px; font-size:12px; color:#8a8fae;">${statusLabel}</div>
      `;
    }

    div.innerHTML = `
      <div class="sol-q">Q${s.id}</div>
      <img class="sol-image" src="${s.image_url}" alt="Question ${s.id}">
      ${bottomHtml}
    `;
    solutionsListEl.appendChild(div);
  });
}

function escapeHtmlLocal(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Download score (.txt) ───────────────────────────────────────────────
downloadScoreBtn.addEventListener("click", () => {
  if (!lastResult) return;

  const content = [
    `SmartyMS Quiz Score Card`,
    `========================`,
    `Quiz: ${QUIZ_TITLE}`,
    `Name: ${lastResult.name}`,
    ``,
    `Total Questions: ${lastResult.total}`,
    `Correct: ${lastResult.correct}`,
    `Incorrect: ${lastResult.incorrect}`,
    `Skipped: ${lastResult.not_answered}`,
    ``,
    `Total Marks: ${lastResult.total_marks}`,
    `Marks Obtained: ${lastResult.marks_obtained}`,
    `Percentage: ${lastResult.percentage}%`,
    `Time Taken: ${lastResult.time_taken_str}`,
    ``,
    lastResult.message,
  ].join("\n");

  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${QUIZ_ID}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});
