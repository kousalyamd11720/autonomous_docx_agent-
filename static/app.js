const EXAMPLES = {
  standard:
    "Write a project kickoff proposal for a new mobile banking app for a mid-size regional bank. Include scope, timeline, and budget.",
  complex:
    "We need something for the client meeting tomorrow about the delayed vendor integration -- marketing wants a positive spin, engineering wants to flag the real risks, and leadership just wants a one-pager they can forward. Figure out what to make and just produce it, we don't have the full timeline yet.",
};

const requestInput = document.getElementById("requestInput");
const runBtn = document.getElementById("runBtn");
const consoleFooter = document.getElementById("consoleFooter");

const emptyState = document.getElementById("emptyState");
const docTypeRow = document.getElementById("docTypeRow");
const docTypeValue = document.getElementById("docTypeValue");
const stepLog = document.getElementById("stepLog");
const assumptionsBox = document.getElementById("assumptionsBox");
const assumptionsList = document.getElementById("assumptionsList");
const errorBox = document.getElementById("errorBox");

const docSection = document.getElementById("docSection");
const docTitle = document.getElementById("docTitle");
const docBody = document.getElementById("docBody");
const docFallbackNote = document.getElementById("docFallbackNote");
const downloadBtn = document.getElementById("downloadBtn");

document.querySelectorAll(".chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    requestInput.value = EXAMPLES[btn.dataset.example];
    requestInput.focus();
  });
});

function resetPanels() {
  emptyState.classList.add("hidden");
  docTypeRow.classList.add("hidden");
  assumptionsBox.classList.add("hidden");
  errorBox.classList.add("hidden");
  docSection.classList.add("hidden");
  downloadBtn.classList.add("hidden");
  docFallbackNote.classList.add("hidden");
  stepLog.innerHTML = "";
  docBody.innerHTML = "";
  assumptionsList.innerHTML = "";
}

function setFooter(state, text) {
  consoleFooter.className = "console-footer " + state;
  consoleFooter.textContent = text;
}

function renderPlan(plan) {
  docTypeValue.textContent = plan.document_type;
  docTypeRow.classList.remove("hidden");

  plan.steps.forEach((step) => {
    const li = document.createElement("li");
    li.className = "step-item queued";
    li.id = `step-${step.step_id}`;
    li.innerHTML = `
      <span class="step-num">${String(step.step_id).padStart(2, "0")}</span>
      <span class="step-status"><span class="status-dot"></span></span>
      <span class="step-body">
        <div class="step-title">${escapeHtml(step.title)}</div>
        <div class="step-desc">${escapeHtml(step.description)}</div>
      </span>`;
    stepLog.appendChild(li);
  });

  if (plan.assumptions && plan.assumptions.length) {
    plan.assumptions.forEach((a) => {
      const li = document.createElement("li");
      li.textContent = a;
      assumptionsList.appendChild(li);
    });
    assumptionsBox.classList.remove("hidden");
  }

  docTitle.textContent = plan.document_title;
}

function markStepRunning(stepId) {
  const el = document.getElementById(`step-${stepId}`);
  if (el) el.className = "step-item running";
}

function markStepDone(result) {
  const el = document.getElementById(`step-${result.step_id}`);
  if (el) {
    el.className = "step-item " + (result.used_fallback ? "fallback" : "done");
    if (result.used_fallback) {
      const tag = document.createElement("span");
      tag.className = "step-tag";
      tag.textContent = "fallback content";
      el.querySelector(".step-body").appendChild(tag);
    }
  }

  const block = document.createElement("div");
  block.className = "doc-section-block";
  const fallbackFlag = result.used_fallback
    ? `<span class="fallback-flag">fallback</span><br/>`
    : "";
  const paragraphs = result.content
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      if (/^[-*\u2022]\s+/.test(line)) {
        return `<li>${escapeHtml(line.replace(/^[-*\u2022]\s+/, ""))}</li>`;
      }
      return `<p>${escapeHtml(line)}</p>`;
    })
    .join("");
  block.innerHTML = `${fallbackFlag}<h2>${escapeHtml(result.section_heading)}</h2>${paragraphs}`;
  docBody.appendChild(block);
  docSection.classList.remove("hidden");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function runAgent() {
  const text = requestInput.value.trim();
  if (!text) {
    requestInput.focus();
    return;
  }

  resetPanels();
  runBtn.disabled = true;
  setFooter("running", "Planning - agent is deciding the document structure...");

  try {
    const resp = await fetch("/agent/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: text }),
    });

    if (!resp.ok || !resp.body) {
      throw new Error(`Server responded with ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let parts = buffer.split("\n\n");
      buffer = parts.pop(); // last part may be incomplete

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const payload = JSON.parse(line.slice(5).trim());
        handleEvent(payload);
      }
    }
  } catch (err) {
    errorBox.textContent = `Agent pipeline error: ${err.message}`;
    errorBox.classList.remove("hidden");
    setFooter("error", "Failed - see error above");
  } finally {
    runBtn.disabled = false;
  }
}

function handleEvent(payload) {
  switch (payload.type) {
    case "plan":
      renderPlan(payload.plan);
      setFooter("running", `Executing - 0 / ${payload.plan.steps.length} sections written`);
      break;
    case "step_start":
      markStepRunning(payload.step_id);
      setFooter("running", `Executing - writing "${payload.title}"...`);
      break;
    case "step_done":
      markStepDone(payload);
      break;
    case "complete":
      downloadBtn.href = payload.download_url;
      downloadBtn.classList.remove("hidden");
      if (payload.used_fallback_llm) {
        docFallbackNote.textContent =
          "Note: one or more sections used fallback content because the LLM was unavailable during this run.";
        docFallbackNote.classList.remove("hidden");
      }
      setFooter("done", "Done - document generated");
      break;
    case "error":
      errorBox.textContent = `Agent pipeline error: ${payload.message}`;
      errorBox.classList.remove("hidden");
      setFooter("error", "Failed - see error above");
      break;
  }
}

runBtn.addEventListener("click", runAgent);
requestInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runAgent();
});
