let currentBatchId = null;
let currentColumns = [];
let currentRows = [];
let currentFlaggedRows = [];
let currentOwnAccount = null;
let currentMode = "generic";
let sortCol = null;
let sortDir = 1;
let searchQuery = "";
const NONE = "__NONE__";

document.querySelectorAll('input[name="analysisMode"]').forEach(radio => {
  radio.addEventListener("change", updateModeVisibility);
});

function updateModeVisibility() {
  const mode = document.querySelector('input[name="analysisMode"]:checked').value;
  document.getElementById("genericMapping").classList.toggle("hidden", mode !== "generic");
  document.getElementById("bankMapping").classList.toggle("hidden", mode !== "bank");
  document.getElementById("modeCardGeneric").classList.toggle("selected", mode === "generic");
  document.getElementById("modeCardBank").classList.toggle("selected", mode === "bank");
}
updateModeVisibility();

// ---- step progress indicator ----
function setStep(n) {
  document.querySelectorAll(".step").forEach(el => {
    const s = parseInt(el.dataset.step, 10);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
  });
  document.getElementById("line1").classList.toggle("done", n > 1);
  document.getElementById("line2").classList.toggle("done", n > 2);
}
setStep(1);

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const parseErr = document.getElementById("parseErr");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", e => { e.preventDefault(); dropzone.classList.add("drag"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", e => {
  e.preventDefault();
  dropzone.classList.remove("drag");
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", e => {
  if (e.target.files.length) uploadFiles(e.target.files);
});

function showErr(msg) {
  document.getElementById("parseErrText").textContent = msg;
  parseErr.classList.remove("hidden");
}

function clearErr() {
  parseErr.classList.add("hidden");
}

async function uploadFiles(fileListRaw) {
  clearErr();
  const files = Array.from(fileListRaw).filter(f => /\.xml$/i.test(f.name));
  if (files.length === 0) {
    showErr("Nijedan .xml fajl nije pronađen u onome što je prevučeno.");
    return;
  }

  document.getElementById("dzText").textContent = `Šaljem ${files.length} fajl(ova) na server...`;

  const formData = new FormData();
  files.forEach(f => formData.append("files", f));

  try {
    const resp = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) {
      showErr(data.error || "Greška pri upload-u.");
      return;
    }

    currentBatchId = data.batch_id;
    currentColumns = data.columns;
    currentOwnAccount = data.own_account;

    document.getElementById("dzText").textContent =
      `Učitano ${data.file_names.length} fajl(ova) — ukupno ${data.row_count} redova`;

    if (data.errors && data.errors.length) {
      showErr("Preskočeno neki fajlovi:\n" + data.errors.join("\n"));
    }

    document.getElementById("rowCount").textContent = data.row_count;
    document.getElementById("colCount").textContent = data.columns.length;
    document.getElementById("batchLabel").textContent = `(batch #${data.batch_id})`;
    document.getElementById("batchLabelBank").textContent = `(batch #${data.batch_id})`;

    const ownNote = document.getElementById("ownAccountNote");
    if (data.own_account) {
      ownNote.textContent = `Prepoznat račun vlasnika izvoda: ${data.own_account} — iBank format detektovan, preporučen mod "Bankovni izvod".`;
      document.getElementById("modeBank").checked = true;
    } else {
      ownNote.textContent = "";
      document.getElementById("modeGeneric").checked = true;
    }
    updateModeVisibility();

    populateColumnSelectors(data.columns);
    populateBankColumnSelectors(data.columns);
    document.getElementById("mapPanel").classList.remove("hidden");
    document.getElementById("resultsPanel").classList.add("hidden");
    setStep(2);

    loadHistory();
  } catch (err) {
    showErr("Greška u komunikaciji sa serverom: " + err.message);
  }
}

function normalize(s) {
  return (s || "").toLowerCase()
    .replace(/[čć]/g, "c").replace(/š/g, "s").replace(/ž/g, "z").replace(/đ/g, "dj")
    .replace(/[^a-z0-9]/g, "");
}

function fillSelect(sel, columns, includeNone) {
  sel.innerHTML = "";
  if (includeNone) {
    const o = document.createElement("option");
    o.value = NONE;
    o.textContent = "-- (ne koristi) --";
    sel.appendChild(o);
  }
  columns.forEach(c => {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    sel.appendChild(o);
  });
}

function populateColumnSelectors(columns) {
  const paidSel = document.getElementById("colPaid");
  const retSel = document.getElementById("colReturned");
  const idSel = document.getElementById("colId");
  const debtorSel = document.getElementById("colDebtor");
  const dateSel = document.getElementById("colDate");

  fillSelect(paidSel, columns, false);
  fillSelect(retSel, columns, false);
  fillSelect(idSel, columns, true);
  fillSelect(debtorSel, columns, true);
  fillSelect(dateSel, columns, true);

  const paidGuess = columns.find(c => normalize(c).includes("placenobanci")) ||
                     columns.find(c => normalize(c).includes("placeno") && normalize(c).includes("banc"));
  const retGuess = columns.find(c => normalize(c).includes("bankauplatila")) ||
                    columns.find(c => normalize(c).includes("uplatilameni")) ||
                    columns.find(c => normalize(c).includes("banka") && normalize(c).includes("meni"));
  const idGuess = columns.find(c => normalize(c).includes("brojnalog")) ||
                  columns.find(c => normalize(c).includes("id"));
  const debtorGuess = columns.find(c => normalize(c).includes("duznik")) ||
                       columns.find(c => normalize(c).includes("platilac"));
  const dateGuess = columns.find(c => normalize(c).includes("datum"));

  if (paidGuess) paidSel.value = paidGuess;
  if (retGuess) retSel.value = retGuess;
  if (idGuess) idSel.value = idGuess;
  if (debtorGuess) debtorSel.value = debtorGuess;
  if (dateGuess) dateSel.value = dateGuess;

  refreshCheckAvailability();
}

function refreshCheckAvailability() {
  const idCol = document.getElementById("colId").value;
  const debtorCol = document.getElementById("colDebtor").value;
  const dateCol = document.getElementById("colDate").value;

  const dupIdOk = idCol && idCol !== NONE;
  const dupPayOk = debtorCol && debtorCol !== NONE && dateCol && dateCol !== NONE;

  const dupIdCheckbox = document.getElementById("checkDupId");
  const dupPayCheckbox = document.getElementById("checkDupPay");

  dupIdCheckbox.disabled = !dupIdOk;
  document.getElementById("cardDupId").classList.toggle("disabled", !dupIdOk);
  if (!dupIdOk) dupIdCheckbox.checked = false;

  dupPayCheckbox.disabled = !dupPayOk;
  document.getElementById("cardDupPay").classList.toggle("disabled", !dupPayOk);
  if (!dupPayOk) dupPayCheckbox.checked = false;
}

["colId", "colDebtor", "colDate"].forEach(id => {
  document.getElementById(id).addEventListener("change", refreshCheckAvailability);
});

function populateBankColumnSelectors(columns) {
  const amountSel = document.getElementById("colAmount");
  const benefitSel = document.getElementById("colBenefit");
  const refSel = document.getElementById("colRef");
  const dateSel = document.getElementById("colDateBank");

  fillSelect(amountSel, columns, false);
  fillSelect(benefitSel, columns, false);
  fillSelect(refSel, columns, true);
  fillSelect(dateSel, columns, true);

  const amountGuess = columns.find(c => normalize(c).includes("trnamt")) ||
                      columns.find(c => normalize(c).includes("amount")) ||
                      columns.find(c => normalize(c).includes("iznos"));
  const benefitGuess = columns.find(c => normalize(c) === "benefit") ||
                       columns.find(c => normalize(c).includes("smer"));
  const refGuess = columns.find(c => normalize(c) === "refnumber") ||
                    columns.find(c => normalize(c).includes("refbroj"));
  const dateGuess = columns.find(c => normalize(c).includes("dtposted")) ||
                     columns.find(c => normalize(c).includes("datum"));

  if (amountGuess) amountSel.value = amountGuess;
  if (benefitGuess) benefitSel.value = benefitGuess;
  if (refGuess) refSel.value = refGuess;
  if (dateGuess) dateSel.value = dateGuess;
}

document.getElementById("analyzeBankBtn").addEventListener("click", runBankAnalysis);

async function runBankAnalysis() {
  if (!currentBatchId) return;

  const body = {
    amount_col: document.getElementById("colAmount").value,
    benefit_col: document.getElementById("colBenefit").value,
    ref_col: (() => { const v = document.getElementById("colRef").value; return v === NONE ? null : v; })(),
    date_col: (() => { const v = document.getElementById("colDateBank").value; return v === NONE ? null : v; })(),
    debit_value: document.getElementById("debitValue").value || "debit",
    credit_value: document.getElementById("creditValue").value || "credit",
    max_days_gap: parseFloat(document.getElementById("maxDaysGap").value) || 30,
    require_refnumber: document.getElementById("requireRefnumber").checked,
  };

  try {
    const resp = await fetch(`/api/batches/${currentBatchId}/analyze_bank`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showErr(data.error || "Greška pri analizi.");
      return;
    }

    currentRows = data.rows;
    currentMode = "bank";
    currentFlaggedRows = currentRows.filter(r => r.flags.length > 0);
    renderResults();
    setStep(3);
    loadHistory();
  } catch (err) {
    showErr("Greška u komunikaciji sa serverom: " + err.message);
  }
}



document.getElementById("analyzeBtn").addEventListener("click", runAnalysis);

async function runAnalysis() {
  if (!currentBatchId) return;

  const paidCol = document.getElementById("colPaid").value;
  const retCol = document.getElementById("colReturned").value;
  const idCol = document.getElementById("colId").value;
  const debtorCol = document.getElementById("colDebtor").value;
  const dateCol = document.getElementById("colDate").value;

  const body = {
    paid_col: paidCol,
    returned_col: retCol,
    id_col: idCol === NONE ? null : idCol,
    debtor_col: debtorCol === NONE ? null : debtorCol,
    date_col: dateCol === NONE ? null : dateCol,
    tolerance: parseFloat(document.getElementById("tolerance").value) || 0,
    outlier_multiplier: parseFloat(document.getElementById("outlierMultiplier").value) || 5,
    check_full: document.getElementById("checkFull").checked,
    check_partial: document.getElementById("checkPartial").checked,
    check_dupid: document.getElementById("checkDupId").checked,
    check_duppay: document.getElementById("checkDupPay").checked,
    check_outlier: document.getElementById("checkOutlier").checked,
  };

  try {
    const resp = await fetch(`/api/batches/${currentBatchId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showErr(data.error || "Greška pri analizi.");
      return;
    }

    currentRows = data.rows;
    currentMode = "generic";
    currentFlaggedRows = currentRows.filter(r => r.flags.length > 0);
    renderResults();
    setStep(3);
    loadHistory();
  } catch (err) {
    showErr("Greška u komunikaciji sa serverom: " + err.message);
  }
}

function computeClientStats(rows) {
  const active = rows.filter(r => !r.is_false_alarm);
  const flagged = active.filter(r => r.flags.length > 0);
  const fullType = currentMode === "bank" ? "circular_confirmed" : "full";
  const fullRows = active.filter(r => r.flags.some(f => f.type === fullType));
  let fullSum = fullRows.reduce((acc, r) => acc + (r.paid_amount || 0), 0);
  if (currentMode === "bank") fullSum = fullSum / 2; // par se racuna 2x (debit+credit)
  return {
    total: rows.length,
    flagged_count: flagged.length,
    full_count: fullRows.length,
    full_sum: fullSum,
  };
}

function getSortValue(r, col) {
  if (col === "__idx") return r.idx;
  if (col === "__source") return r.raw.__source_file || "";
  if (col === "__flags") return r.flags.map(f => f.label).join(", ");
  if (col === "__falsealarm") return r.is_false_alarm ? 1 : 0;
  return r.raw[col] ?? "";
}

function compareValues(a, b) {
  const na = parseFloat(String(a).replace(",", "."));
  const nb = parseFloat(String(b).replace(",", "."));
  const aIsNum = !isNaN(na) && String(a).trim() !== "";
  const bIsNum = !isNaN(nb) && String(b).trim() !== "";
  if (aIsNum && bIsNum) return na - nb;
  return String(a).localeCompare(String(b), "sr");
}

function setSort(col) {
  if (sortCol === col) {
    sortDir = -sortDir;
  } else {
    sortCol = col;
    sortDir = 1;
  }
  renderResults();
}
window.setSort = setSort;

function toggleFalseAlarm(rowIdx, checked) {
  const row = currentRows.find(r => r.idx === rowIdx);
  if (row) row.is_false_alarm = checked;
  renderResults();
  if (!currentBatchId) return;
  fetch(`/api/batches/${currentBatchId}/false_alarm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ row_index: rowIdx, value: checked }),
  }).catch(() => { /* tiho ignorisi - lokalni prikaz je vec azuriran */ });
}
window.toggleFalseAlarm = toggleFalseAlarm;

function renderResults() {
  const onlyFlagged = document.getElementById("onlyFlagged").checked;
  const theadRow = document.getElementById("theadRow");
  const tbody = document.getElementById("tbody");

  const colDefs = [
    { key: "__idx", label: "#" },
    { key: "__source", label: "Izvorni fajl" },
    ...currentColumns.map(c => ({ key: c, label: c })),
    { key: "__flags", label: "Upozorenja" },
    { key: "__falsealarm", label: "Lažna uzbuna" },
  ];

  theadRow.innerHTML = colDefs.map(cd => {
    const arrow = sortCol === cd.key ? `<span class="sort-arrow">${sortDir === 1 ? "▲" : "▼"}</span>` : "";
    return `<th onclick="setSort('${cd.key.replace(/'/g, "\\'")}')">${escapeHtml(cd.label)}${arrow}</th>`;
  }).join("");

  let toShow = onlyFlagged ? currentRows.filter(r => r.flags.length > 0) : currentRows;

  if (searchQuery.trim() !== "") {
    const q = searchQuery.trim().toLowerCase();
    toShow = toShow.filter(r => {
      const haystack = [
        r.raw.__source_file || "",
        ...currentColumns.map(c => r.raw[c] ?? ""),
        r.flags.map(f => f.label).join(" "),
      ].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }

  if (sortCol) {
    toShow = [...toShow].sort((a, b) => compareValues(getSortValue(a, sortCol), getSortValue(b, sortCol)) * sortDir);
  }

  tbody.innerHTML = toShow.map(r => {
    const cells = currentColumns.map(c => `<td>${escapeHtml(r.raw[c] ?? "")}</td>`).join("");
    const badges = r.flags.length
      ? r.flags.map(f => `<span class="badge ${f.type}">${f.label}</span>`).join("")
      : '<span class="badge ok">OK</span>';
    const isFull = r.flags.some(f => f.type === "full" || f.type === "circular_confirmed");
    let rowClass = isFull ? "flagged-full" : (r.flags.length ? "flagged-warn" : "");
    if (r.is_false_alarm) rowClass += " false-alarm";
    const checkboxCell = `<td class="no-strike"><input type="checkbox" ${r.is_false_alarm ? "checked" : ""} onchange="toggleFalseAlarm(${r.idx}, this.checked)"></td>`;
    return `<tr class="${rowClass}"><td>${r.idx + 1}</td><td>${escapeHtml(r.raw.__source_file ?? "")}</td>${cells}<td>${badges}</td>${checkboxCell}</tr>`;
  }).join("");

  const stats = computeClientStats(currentRows);
  document.getElementById("statTotal").textContent = stats.total;
  document.getElementById("statAny").textContent = stats.flagged_count;
  document.getElementById("statFull").textContent = stats.full_count;
  document.getElementById("statSum").textContent = stats.full_sum.toLocaleString("sr-RS", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  document.getElementById("resultsPanel").classList.remove("hidden");
}

document.getElementById("onlyFlagged").addEventListener("change", () => {
  if (currentRows.length) renderResults();
});

document.getElementById("searchBox").addEventListener("input", (e) => {
  searchQuery = e.target.value;
  if (currentRows.length) renderResults();
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

document.getElementById("exportFlaggedBtn").addEventListener("click", () => {
  if (!currentBatchId) return;
  window.location.href = `/api/batches/${currentBatchId}/export.csv?only_flagged=1`;
});

document.getElementById("exportAllBtn").addEventListener("click", () => {
  if (!currentBatchId) return;
  window.location.href = `/api/batches/${currentBatchId}/export.csv?only_flagged=0`;
});

document.getElementById("exportFlaggedPdfBtn").addEventListener("click", () => {
  if (!currentBatchId) return;
  window.location.href = `/api/batches/${currentBatchId}/export.pdf?only_flagged=1`;
});

document.getElementById("exportFullPdfBtn").addEventListener("click", () => {
  if (!currentBatchId) return;
  window.location.href = `/api/batches/${currentBatchId}/export.pdf?only_full=1`;
});

document.getElementById("exportAllPdfBtn").addEventListener("click", () => {
  if (!currentBatchId) return;
  window.location.href = `/api/batches/${currentBatchId}/export.pdf?only_flagged=0`;
});

async function loadHistory() {
  try {
    const resp = await fetch("/api/batches");
    const batches = await resp.json();
    const tbody = document.getElementById("historyBody");

    if (!batches.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="muted">Nema još učitanih fajlova.</td></tr>';
      return;
    }

    tbody.innerHTML = batches.map(b => `
      <tr>
        <td>${b.id}</td>
        <td>${escapeHtml(b.label)}</td>
        <td>${escapeHtml(b.created_at.replace("T", " ").slice(0, 16))}</td>
        <td>${b.row_count}</td>
        <td>${b.analyzed ? "da" : "ne"}</td>
        <td>${b.analyzed ? b.flagged_count : "-"}</td>
        <td>
          <button class="secondary tiny" onclick="openBatch(${b.id})">Otvori</button>
          <button class="secondary tiny" onclick="deleteBatch(${b.id})">Obriši</button>
        </td>
      </tr>
    `).join("");
  } catch (err) {
    // tiho ignorisi gresku istorije, ne blokira glavni tok
  }
}

async function openBatch(batchId) {
  clearErr();
  const resp = await fetch(`/api/batches/${batchId}/results`);
  const data = await resp.json();
  if (!resp.ok) {
    showErr(data.error || "Greška pri učitavanju batch-a.");
    return;
  }

  currentBatchId = batchId;
  currentColumns = data.batch.columns_json;
  currentRows = data.rows;
  currentMode = data.batch.mode === "bank_statement" ? "bank" : "generic";
  currentFlaggedRows = currentRows.filter(r => r.flags.length > 0);

  document.getElementById("rowCount").textContent = data.batch.row_count;
  document.getElementById("colCount").textContent = currentColumns.length;
  document.getElementById("batchLabel").textContent = `(batch #${batchId})`;
  document.getElementById("dzText").textContent = `Otvoren batch #${batchId} — ${data.batch.row_count} redova`;

  populateColumnSelectors(currentColumns);

  // popuni prethodno mapiranje ako postoji
  if (data.batch.paid_col) document.getElementById("colPaid").value = data.batch.paid_col;
  if (data.batch.returned_col) document.getElementById("colReturned").value = data.batch.returned_col;
  if (data.batch.id_col) document.getElementById("colId").value = data.batch.id_col;
  if (data.batch.debtor_col) document.getElementById("colDebtor").value = data.batch.debtor_col;
  if (data.batch.date_col) document.getElementById("colDate").value = data.batch.date_col;
  document.getElementById("tolerance").value = data.batch.tolerance ?? 0;
  document.getElementById("outlierMultiplier").value = data.batch.outlier_multiplier ?? 5;
  document.getElementById("checkFull").checked = !!data.batch.check_full;
  document.getElementById("checkPartial").checked = !!data.batch.check_partial;
  document.getElementById("checkDupId").checked = !!data.batch.check_dupid;
  document.getElementById("checkDupPay").checked = !!data.batch.check_duppay;
  document.getElementById("checkOutlier").checked = !!data.batch.check_outlier;
  refreshCheckAvailability();

  document.getElementById("mapPanel").classList.remove("hidden");

  if (currentMode === "bank") {
    document.getElementById("modeBank").checked = true;
  } else {
    document.getElementById("modeGeneric").checked = true;
  }
  updateModeVisibility();

  if (data.batch.analyzed) {
    renderResults();
    setStep(3);
  } else {
    document.getElementById("resultsPanel").classList.add("hidden");
    setStep(2);
  }
}

async function deleteBatch(batchId) {
  if (!confirm(`Obrisati batch #${batchId}? Ovo je trajno.`)) return;
  await fetch(`/api/batches/${batchId}`, { method: "DELETE" });
  if (currentBatchId === batchId) {
    currentBatchId = null;
    document.getElementById("mapPanel").classList.add("hidden");
    document.getElementById("resultsPanel").classList.add("hidden");
    setStep(1);
  }
  loadHistory();
}

loadHistory();