// ── State ───────────────────────────────────────────────────────────────
let questions = [];
let currentIndex = 0;
let answers = {};        // { "1": "A", "2": null, ... }
let visited = {};        // { "1": true } -> answered or explicitly skipped
let startTime = null;
let timerInterval = null;
let solutionsData = [];

// anonymous user id, persisted in this browser so a re-visit is tracked as same user
function getUserId() {
  let id = localStorage.getItem("smartyms_user_id");
  if (!id) {
    id = "user-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem("smartyms_user_id", id);
  }
  return id;
}

// ── DOM refs ────────────────────────────────────────────────────────────
const qNumberBadge = document.getElementById("qNumberBadge");
const questionText = document.getElementById("questionText");
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

const resultModal = document.getElementById("resultModal");
const scoreValue = document.getElementById("scoreValue");
const resCorrect = document.getElementById("resCorrect");
const resIncorrect = document.getElementById("resIncorrect");
const resSkipped = document.getElementById("resSkipped");
const resAccuracy = document.getElementById("resAccuracy");
const resTime = document.getElementById("resTime");
const reattemptBtn = document.getElementById("reattemptBtn");
const viewSolutionsBtn = document.getElementById("viewSolutionsBtn");

const solutionsModal = document.getElementById("solutionsModal");
const solutionsListEl = document.getElementById("solutionsList");
const closeSolutionsBtn = document.getElementById("closeSolutionsBtn");

// ── Init ────────────────────────────────────────────────────────────────
async function loadQuiz() {
  const res = await fetch(`/api/quiz/${QUIZ_ID}`);
  const data = await res.json();
  if (!data.ok) {
    questionText.textContent = "Quiz load nahi hua: " + data.error;
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
  questionText.textContent = q.text;

  optionsList.innerHTML = "";
  ["A", "B", "C", "D"].forEach((letter) => {
    if (!(letter in q.options)) return;
    const div = document.createElement("div");
    div.className = "option-item" + (answers[q.id] === letter ? " selected" : "");
    div.innerHTML = `<span class="option-letter">${letter}</span><span>${q.options[letter]}</span>`;
    div.addEventListener("click", () => selectOption(q.id, letter));
    optionsList.appendChild(div);
  });

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
  // correct/incorrect only known after submit; keep at 0 during attempt
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
    submitQuiz();
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

submitBtn.addEventListener("click", submitQuiz);

// ── Submit / Results ────────────────────────────────────────────────────
async function submitQuiz() {
  const timeTakenStr = stopTimer();
  const [h, m, s] = timeTakenStr.split(":").map(Number);
  const totalSeconds = h * 3600 + m * 60 + s;

  const res = await fetch(`/api/submit/${QUIZ_ID}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      answers,
      time_taken_seconds: totalSeconds,
      user_id: getUserId(),
    }),
  });
  const data = await res.json();
  if (!data.ok) {
    alert("Submit failed: " + data.error);
    return;
  }

  solutionsData = data.solutions;

  scoreValue.textContent = `${data.correct}/${data.total}`;
  resCorrect.textContent = data.correct;
  resIncorrect.textContent = data.incorrect;
  resSkipped.textContent = data.not_answered;
  resAccuracy.textContent = data.accuracy + "%";
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

    let optsHtml = "";
    ["A", "B", "C", "D"].forEach((letter) => {
      if (!(letter in s.options)) return;
      let cls = "";
      if (letter === s.correct) cls = "correct-ans";
      else if (letter === s.chosen && letter !== s.correct) cls = "wrong-chosen";
      optsHtml += `<div class="solution-opt ${cls}">${letter}) ${s.options[letter]}</div>`;
    });

    const statusLabel = s.status === "correct" ? "✅ Correct"
      : s.status === "incorrect" ? "❌ Incorrect" : "⏭ Skipped";

    div.innerHTML = `
      <div class="sol-q">Q${s.id}. ${s.text}</div>
      ${optsHtml}
      <div style="margin-top:8px; font-size:12px; color:#8a8fae;">${statusLabel}</div>
    `;
    solutionsListEl.appendChild(div);
  });
}

// ── Boot ────────────────────────────────────────────────────────────────
loadQuiz();
