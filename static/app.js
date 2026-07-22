let currentBatchId = null;
let currentColumns = [];
let currentRows = [];
let currentFlaggedRows = [];
let currentOwnAccount = null;
let currentMode = "generic";
let sortCol = null;
let sortDir = 1;
let searchQuery = "";
let viewFilter = "all"; // all | flagged | full
const NONE = "__NONE__";

document.querySelectorAll('input[name="analysisMode"]').forEach(radio => {
  radio.addEventListener("change", updateModeVisibility);
});

function updateModeVisibility() {
  const mode = document.querySelector('input[name="analysisMode"]:checked').value;
  document.getElementById("genericMapping").classList.toggle("hidden", mode !== "generic");
  document.getElementById("bankMapping").classList.toggle("hidden", mode !== "bank");
  document.getElementById("transferMapping").classList.toggle("hidden", mode !== "transfer");
  document.getElementById("modeCardGeneric").classList.toggle("selected", mode === "generic");
  document.getElementById("modeCardBank").classList.toggle("selected", mode === "bank");
  document.getElementById("modeCardTransfer").classList.toggle("selected", mode === "transfer");
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

// ---- welcome modal ----
(function initWelcomeModal() {
  const modal = document.getElementById("welcomeModal");
  const closeBtn = document.getElementById("modalCloseBtn");
  const closeX = document.getElementById("modalCloseX");
  const dontShow = document.getElementById("dontShowAgain");

  let skip = false;
  try { skip = localStorage.getItem("paycheckSentinelSkipWelcome") === "1"; } catch (e) { /* ignore */ }

  if (!skip) modal.classList.remove("hidden");

  function closeModal() {
    modal.classList.add("hidden");
    if (dontShow.checked) {
      try { localStorage.setItem("paycheckSentinelSkipWelcome", "1"); } catch (e) { /* ignore */ }
    }
  }
  closeBtn.addEventListener("click", closeModal);
  closeX.addEventListener("click", closeModal);
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
})();

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

function uploadFiles(fileListRaw) {
  clearErr();
  const files = Array.from(fileListRaw).filter(f => /\.xml$/i.test(f.name));
  if (files.length === 0) {
    showErr("Nijedan .xml fajl nije pronađen u onome što je prevučeno.");
    return;
  }

  document.getElementById("dzText").textContent = `Šaljem ${files.length} fajl(ova) na server...`;

  const progressWrap = document.getElementById("uploadProgress");
  const progressFill = document.getElementById("progressFill");
  const progressLabel = document.getElementById("progressLabel");
  progressWrap.classList.remove("hidden", "indeterminate");
  progressFill.style.width = "0%";
  progressLabel.textContent = `Šaljem podatke... 0%`;

  const formData = new FormData();
  files.forEach(f => formData.append("files", f));

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");

  xhr.upload.addEventListener("progress", e => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    progressFill.style.width = pct + "%";
    progressLabel.textContent = `Šaljem podatke... ${pct}%`;
    if (pct >= 100) {
      progressWrap.classList.add("indeterminate");
      progressLabel.textContent = "Obrađujem XML fajlove...";
    }
  });

  xhr.onload = () => {
    progressWrap.classList.add("hidden");
    progressWrap.classList.remove("indeterminate");

    let data;
    try {
      data = JSON.parse(xhr.responseText);
    } catch (e) {
      showErr("Greška u komunikaciji sa serverom: neispravan odgovor.");
      return;
    }

    if (xhr.status < 200 || xhr.status >= 300) {
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
    populateTransferColumnSelectors(data.columns);
    populateAcctFilterColumn(data.columns);
    resetResultFilters();
    document.getElementById("mapPanel").classList.remove("hidden");
    document.getElementById("mapPanel").classList.add("fade-in");
    document.getElementById("resultsPanel").classList.add("hidden");
    setStep(2);

    loadHistory();
  };

  xhr.onerror = () => {
    progressWrap.classList.add("hidden");
    progressWrap.classList.remove("indeterminate");
    showErr("Greška u komunikaciji sa serverom.");
  };

  xhr.send(formData);
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

function resetResultFilters() {
  viewFilter = "all";
  document.querySelectorAll("#viewFilterGroup .seg-btn").forEach(b => b.classList.toggle("active", b.dataset.filter === "all"));
  document.getElementById("acctFilterA").value = "";
  document.getElementById("acctFilterB").value = "";
  document.getElementById("acctFilter").classList.remove("filtering");
  document.getElementById("searchBox").value = "";
  searchQuery = "";
}

function populateAcctFilterColumn(columns) {
  const sel = document.getElementById("acctFilterCol");
  sel.innerHTML = '<option value="">— kolona sa brojem računa —</option>';
  columns.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    sel.appendChild(opt);
  });
  const guess = columns.find(c => normalize(c).includes("racun")) ||
                columns.find(c => normalize(c).includes("acct")) ||
                columns.find(c => normalize(c).includes("iban")) ||
                columns.find(c => normalize(c).includes("duznik")) ||
                columns.find(c => normalize(c).includes("platilac"));
  if (guess) sel.value = guess;
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

function populateTransferColumnSelectors(columns) {
  const fromSel = document.getElementById("colFrom");
  const toSel = document.getElementById("colTo");
  const amountSel = document.getElementById("colTransferAmount");
  const dateSel = document.getElementById("colTransferDate");

  fillSelect(fromSel, columns, false);
  fillSelect(toSel, columns, false);
  fillSelect(amountSel, columns, true);
  fillSelect(dateSel, columns, true);

  const fromGuess = columns.find(c => normalize(c).includes("nalogodavac")) ||
                     columns.find(c => normalize(c).includes("duznik")) ||
                     columns.find(c => normalize(c).includes("platilac")) ||
                     columns.find(c => normalize(c).includes("racun"));
  const toGuess = columns.find(c => c !== fromGuess && normalize(c).includes("primalac")) ||
                  columns.find(c => c !== fromGuess && normalize(c).includes("poverilac")) ||
                  columns.find(c => c !== fromGuess && normalize(c).includes("racun"));
  const amountGuess = columns.find(c => normalize(c).includes("iznos")) ||
                      columns.find(c => normalize(c).includes("trnamt")) ||
                      columns.find(c => normalize(c).includes("amount"));
  const dateGuess = columns.find(c => normalize(c).includes("datum")) ||
                     columns.find(c => normalize(c).includes("dtposted"));

  if (fromGuess) fromSel.value = fromGuess;
  if (toGuess) toSel.value = toGuess;
  if (amountGuess) amountSel.value = amountGuess;
  if (dateGuess) dateSel.value = dateGuess;
}

document.getElementById("analyzeTransferBtn").addEventListener("click", runTransferAnalysis);
document.getElementById("analyzeBankBtn").addEventListener("click", runBankAnalysis);

async function runTransferAnalysis() {
  if (!currentBatchId) return;

  const accountFrom = document.getElementById("accountFrom").value.trim();
  const accountTo = document.getElementById("accountTo").value.trim();
  if (!accountFrom && !accountTo) {
    showErr("Unesi bar jedan broj računa (A ili B).");
    return;
  }

  const body = {
    from_col: document.getElementById("colFrom").value,
    to_col: document.getElementById("colTo").value,
    amount_col: (() => { const v = document.getElementById("colTransferAmount").value; return v === NONE ? null : v; })(),
    date_col: (() => { const v = document.getElementById("colTransferDate").value; return v === NONE ? null : v; })(),
    account_from: accountFrom,
    account_to: accountTo,
  };

  try {
    const resp = await fetch(`/api/batches/${currentBatchId}/analyze_transfer`, {
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
    currentMode = "transfer";
    currentFlaggedRows = currentRows.filter(r => r.flags.length > 0);
    renderResults();
    setStep(3);
    loadHistory();
  } catch (err) {
    showErr("Greška u komunikaciji sa serverom: " + err.message);
  }
}

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
  const fullType = currentMode === "bank" ? "circular_confirmed" : (currentMode === "transfer" ? "transfer" : "full");
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

  const fullType = currentMode === "bank" ? "circular_confirmed" : (currentMode === "transfer" ? "transfer" : "full");
  if (viewFilter === "flagged") {
    toShow = toShow.filter(r => r.flags.length > 0);
  } else if (viewFilter === "full") {
    toShow = toShow.filter(r => r.flags.some(f => f.type === fullType));
  }

  const acctCol = document.getElementById("acctFilterCol").value;
  const acctA = document.getElementById("acctFilterA").value.trim().toLowerCase();
  const acctB = document.getElementById("acctFilterB").value.trim().toLowerCase();
  if (acctCol && (acctA || acctB)) {
    toShow = toShow.filter(r => {
      const val = String(r.raw[acctCol] ?? "").toLowerCase();
      const matchA = acctA && val.includes(acctA);
      const matchB = acctB && val.includes(acctB);
      if (acctA && acctB) return matchA || matchB;
      return matchA || matchB;
    });
  }

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
    const isFull = r.flags.some(f => f.type === "full" || f.type === "circular_confirmed" || f.type === "transfer");
    let rowClass = isFull ? "flagged-full" : (r.flags.length ? "flagged-warn" : "");
    if (r.is_false_alarm) rowClass += " false-alarm";
    const checkboxCell = `<td class="no-strike"><input type="checkbox" ${r.is_false_alarm ? "checked" : ""} onchange="toggleFalseAlarm(${r.idx}, this.checked)"></td>`;
    return `<tr class="${rowClass}"><td>${r.idx + 1}</td><td>${escapeHtml(r.raw.__source_file ?? "")}</td>${cells}<td>${badges}</td>${checkboxCell}</tr>`;
  }).join("");

  if (toShow.length === 0) {
    const colspan = colDefs.length;
    tbody.innerHTML = `<tr><td colspan="${colspan}">
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        Nijedan red ne odgovara trenutnom filteru.
      </div>
    </td></tr>`;
  }

  const stats = computeClientStats(currentRows);
  document.getElementById("statTotal").textContent = stats.total;
  document.getElementById("statAny").textContent = stats.flagged_count;
  document.getElementById("statFull").textContent = stats.full_count;
  document.getElementById("statSum").textContent = stats.full_sum.toLocaleString("sr-RS", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const legendBox = document.getElementById("legendBox");
  const statFullLabel = document.querySelector("#statFull").closest(".stat").querySelector(".l");
  const statSumLabel = document.querySelector("#statSum").closest(".stat").querySelector(".l");
  if (currentMode === "transfer") {
    legendBox.innerHTML = `<span><span class="badge transfer">TRANSFER A→B</span> transakcija između zadatih računa</span>`;
    statFullLabel.textContent = "Pronađenih transfera";
    statSumLabel.textContent = "Ukupan iznos transfera";
  } else if (currentMode === "bank") {
    legendBox.innerHTML = `
      <span><span class="badge circular_confirmed">POVRAT (ref. broj)</span> pouzdano uparen povrat</span>
      <span><span class="badge circular_possible">MOGUĆ POVRAT (iznos)</span> uparen po iznosu/datumu</span>`;
    statFullLabel.textContent = "Potvrđen povrat";
    statSumLabel.textContent = "Iznos potvrđenog povrata";
  } else {
    legendBox.innerHTML = `
      <span><span class="badge full">PUN POVRAT</span> tačno poklapanje</span>
      <span><span class="badge partial">DELIMIČAN</span> delimičan povrat</span>
      <span><span class="badge dupid">DUP. ID</span> dupli broj naloga</span>
      <span><span class="badge duppay">DUP. UPLATA</span> ista uplata ponovljena</span>
      <span><span class="badge outlier">OUTLIER</span> neuobičajen iznos</span>`;
    statFullLabel.textContent = "Pun povrat";
    statSumLabel.textContent = "Iznos punog povrata";
  }

  const resultsPanel = document.getElementById("resultsPanel");
  const wasHidden = resultsPanel.classList.contains("hidden");
  resultsPanel.classList.remove("hidden");
  if (wasHidden) resultsPanel.classList.add("fade-in");
}

document.querySelectorAll("#viewFilterGroup .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    viewFilter = btn.dataset.filter;
    document.querySelectorAll("#viewFilterGroup .seg-btn").forEach(b => b.classList.toggle("active", b === btn));
    if (currentRows.length) renderResults();
  });
});

["acctFilterCol", "acctFilterA", "acctFilterB"].forEach(id => {
  document.getElementById(id).addEventListener("input", () => {
    const a = document.getElementById("acctFilterA").value.trim();
    const b = document.getElementById("acctFilterB").value.trim();
    document.getElementById("acctFilter").classList.toggle("filtering", !!(a || b));
    if (currentRows.length) renderResults();
  });
  document.getElementById(id).addEventListener("change", () => {
    if (currentRows.length) renderResults();
  });
});

document.getElementById("acctFilterClear").addEventListener("click", () => {
  document.getElementById("acctFilterA").value = "";
  document.getElementById("acctFilterB").value = "";
  document.getElementById("acctFilter").classList.remove("filtering");
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
      tbody.innerHTML = `<tr><td colspan="7">
        <div class="empty-state">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>
          Nema još učitanih fajlova.
        </div>
      </td></tr>`;
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
  currentMode = data.batch.mode === "bank_statement" ? "bank" : (data.batch.mode === "transfer" ? "transfer" : "generic");
  currentFlaggedRows = currentRows.filter(r => r.flags.length > 0);

  document.getElementById("rowCount").textContent = data.batch.row_count;
  document.getElementById("colCount").textContent = currentColumns.length;
  document.getElementById("batchLabel").textContent = `(batch #${batchId})`;
  document.getElementById("dzText").textContent = `Otvoren batch #${batchId} — ${data.batch.row_count} redova`;

  populateColumnSelectors(currentColumns);
  populateTransferColumnSelectors(currentColumns);
  populateAcctFilterColumn(currentColumns);
  resetResultFilters();

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

  if (data.batch.mode === "transfer") {
    if (data.batch.from_col) document.getElementById("colFrom").value = data.batch.from_col;
    if (data.batch.to_col) document.getElementById("colTo").value = data.batch.to_col;
    if (data.batch.amount_col) document.getElementById("colTransferAmount").value = data.batch.amount_col;
    if (data.batch.date_col) document.getElementById("colTransferDate").value = data.batch.date_col;
    document.getElementById("accountFrom").value = data.batch.account_from ?? "";
    document.getElementById("accountTo").value = data.batch.account_to ?? "";
  }

  document.getElementById("mapPanel").classList.remove("hidden");

  if (currentMode === "bank") {
    document.getElementById("modeBank").checked = true;
  } else if (currentMode === "transfer") {
    document.getElementById("modeTransfer").checked = true;
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
