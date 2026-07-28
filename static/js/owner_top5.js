const tableBody = document.getElementById("tableBody");

document.getElementById("backBtn").addEventListener("click", () => {
  window.location.href = `/owner/${encodeURIComponent(QUIZ_ID)}`;
});

document.getElementById("downloadBtn").addEventListener("click", () => {
  window.location.href = `/api/owner/${encodeURIComponent(QUIZ_ID)}/top5/download?t=${encodeURIComponent(DOWNLOAD_TOKEN)}`;
});

function escapeHtmlLocal(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function refreshTop5() {
  try {
    const res = await fetch(`/api/owner/${encodeURIComponent(QUIZ_ID)}/top5`);
    const data = await res.json();
    if (!data.ok) return;

    if (!data.rows.length) {
      tableBody.innerHTML = `<tr class="owner-empty-row"><td colspan="4">Abhi tak koi student ne attend nahi kiya.</td></tr>`;
      return;
    }

    tableBody.innerHTML = data.rows.map((r) => `
      <tr>
        <td>${r.index}.</td>
        <td>${escapeHtmlLocal(r.name)}</td>
        <td><span class="owner-rank-badge">${r.rank}</span></td>
        <td>${r.marks_obtained}</td>
      </tr>
    `).join("");
  } catch (e) {
    // Silent fail — next poll will retry automatically.
  }
}

refreshTop5();
setInterval(refreshTop5, 2500);
