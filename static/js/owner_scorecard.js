const tableBody = document.getElementById("tableBody");

document.getElementById("backBtn").addEventListener("click", () => {
  window.location.href = `/owner/${encodeURIComponent(QUIZ_ID)}`;
});

document.getElementById("downloadBtn").addEventListener("click", () => {
  window.location.href = `/api/owner/${encodeURIComponent(QUIZ_ID)}/scorecard/download?t=${encodeURIComponent(DOWNLOAD_TOKEN)}`;
});

function escapeHtmlLocal(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function refreshScorecard() {
  try {
    const res = await fetch(`/api/owner/${encodeURIComponent(QUIZ_ID)}/scorecard`);
    const data = await res.json();
    if (!data.ok) return;

    if (!data.rows.length) {
      tableBody.innerHTML = `<tr class="owner-empty-row"><td colspan="5">Abhi tak koi student ne attend nahi kiya.</td></tr>`;
      return;
    }

    tableBody.innerHTML = data.rows.map((r) => `
      <tr>
        <td>${r.index}.</td>
        <td><span class="owner-rank-badge">${r.rank}</span></td>
        <td>${escapeHtmlLocal(r.name)}</td>
        <td>${r.marks_obtained} / ${r.total_marks}</td>
        <td>${r.percentage}%</td>
      </tr>
    `).join("");
  } catch (e) {
    // Silent fail — next poll will retry automatically.
  }
}

refreshScorecard();
setInterval(refreshScorecard, 2500);
