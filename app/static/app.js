const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const startBtn = document.getElementById("start");
const endBtn = document.getElementById("end");
const durationEl = document.getElementById("duration");
const transcriptEl = document.getElementById("transcript");
const resultEl = document.getElementById("result");
const recordingConsent = document.getElementById("recording-consent");
const refreshHistoryBtn = document.getElementById("refresh-history");
const historyStatusEl = document.getElementById("history-status");
const historyListEl = document.getElementById("history-list");
const historyDetailEl = document.getElementById("history-detail");
const historyUnlockEl = document.getElementById("history-unlock");
const historyPasswordEl = document.getElementById("history-password");
const historyUnlockButton = document.getElementById("history-unlock-button");

// State
const OUTPUT_RATE = 24000;
let ws = null;
let captureCtx = null;
let playbackCtx = null;
let mediaStream = null;
let captureNode = null;
let sourceNode = null;
let nextPlayTime = 0;
let activeSources = [];
let playGeneration = 0;
let callPhase = "idle";
let activeTranscriptEntry = null;
let activeTranscriptSpeaker = null;
let shouldAutoScroll = true;
let assistantAudioPending = false;
let currentCallId = null;
let recorder = null;
let recordingChunks = [];
let recordingType = "";
let recordingDestination = null;
let recordingSourceNode = null;
let recordingUploadPromise = null;
let recordingConsentSent = false;
let recordingCallId = null;
let recordingCallToken = null;
let callToken = null;
let finishTimer = null;
let summaryPollTimer = null;
let historyCalls = [];
let selectedHistoryId = null;

function apiHeaders(extra = {}) {
  const headers = { ...extra };
  if (callToken) headers["X-Call-Token"] = callToken;
  try {
    const password = sessionStorage.getItem("historyPassword");
    if (password) headers["X-History-Password"] = password;
  } catch (_error) {
    // Storage may be unavailable in privacy-restricted browsers.
  }
  return headers;
}

async function requestJson(url, options = {}, fallbackMessage = "Request failed.") {
  let response;
  try {
    response = await fetch(url, options);
  } catch (_error) {
    throw new Error("Could not connect to the server.");
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.detail || fallbackMessage);
    error.status = response.status;
    throw error;
  }
  return payload;
}

// Shared UI helpers
function setStatus(value) {
  statusEl.textContent = value;
}

function finalizeTranscriptMessage() {
  if (activeTranscriptEntry) {
    activeTranscriptEntry.dataset.partial = "false";
  }
  activeTranscriptEntry = null;
  activeTranscriptSpeaker = null;
}

function setError(message) {
  if (!message) {
    errorEl.hidden = true;
    errorEl.textContent = "";
    return;
  }
  errorEl.hidden = false;
  errorEl.textContent = message;
  errorEl.classList.remove("error-toast-visible");
  requestAnimationFrame(() => errorEl.classList.add("error-toast-visible"));
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function formatDate(value) {
  if (!value) return "Start unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Start unavailable"
    : date.toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayStatus(call) {
  if (call.status) return call.status;
  if (call.summary_generation_status === "failed") return "Summary failed";
  if (!call.ended_at) return "In progress";
  return "Summary pending";
}

function statusClass(status) {
  return `status-${String(status).toLowerCase().replaceAll(" ", "-")}`;
}

function renderStatusBadge(status) {
  return `<span class="badge ${statusClass(status)}">${escapeHtml(status)}</span>`;
}

// History
function renderHistoryTranscript(transcript) {
  if (!Array.isArray(transcript) || !transcript.length) {
    return "<p class=\"history-empty\">Transcript unavailable.</p>";
  }
  return transcript
    .map((entry) => {
      const speaker = entry.speaker === "customer" ? "Customer" : "Assistant";
      return `<p>${escapeHtml(speaker)}: ${escapeHtml(entry.text || "")}</p>`;
    })
    .join("");
}

const summaryFields = [
  ["business_use_case", "Business use case"],
  ["needs", "Needs"],
  ["modules_discussed", "Modules discussed"],
  ["questions", "Questions"],
  ["objections", "Objections"],
  ["interest", "Interest"],
  ["interest_evidence", "Interest evidence"],
  ["next_steps", "Next steps"],
  ["unknowns", "Unknowns"],
];

function summaryValue(value) {
  if (Array.isArray(value)) return value.filter(Boolean).map(escapeHtml).join("<br />");
  if (value && typeof value === "object") return escapeHtml(JSON.stringify(value));
  return escapeHtml(value || "");
}

function renderSummary(summary) {
  if (!summary || typeof summary !== "object") {
    return '<p class="history-empty">Summary unavailable.</p>';
  }
  return summaryFields
    .filter(([key]) => summary[key] !== null && summary[key] !== undefined && summary[key] !== "" &&
      (!Array.isArray(summary[key]) || summary[key].length))
    .map(([key, label]) => `<div><h4>${escapeHtml(label)}</h4><p>${summaryValue(summary[key])}</p></div>`)
    .join("");
}

// Result panel
function retryButton(callId) {
  return `<button type="button" class="retry-summary" data-call-id="${escapeHtml(callId)}">Retry summary</button>`;
}

function bindRetryButtons(container) {
  container.querySelectorAll(".retry-summary").forEach((button) => {
    button.addEventListener("click", () => retrySummary(button.dataset.callId, button));
  });
}

async function retrySummary(callId, button) {
  button.disabled = true;
  try {
    const call = await requestJson(`/api/calls/${callId}/summary/retry`, {
      method: "POST",
      headers: apiHeaders(),
    }, "Could not retry the summary.");
    if (String(callId) === String(currentCallId)) renderResult(call);
    if (String(selectedHistoryId) === String(callId)) await renderHistoryDetail(call);
  } catch (error) {
    if (error.status === 401) {
      clearHistoryPassword();
      error.message = "History password rejected.";
    }
    setError(error.message);
    button.disabled = false;
  }
}

function renderLeadDetails(call) {
  const fields = [
    ["lead_name", "Name"],
    ["lead_company", "Company"],
    ["lead_contact", "Contact"],
    ["lead_preferred_time", "Preferred time"],
  ].filter(([key]) => call[key]);
  if (!fields.length) return "";
  return `
    <div class="lead-details">
      <h4>Lead details</h4>
      ${fields.map(([key, label]) => `
        <div><span>${escapeHtml(label)}</span><p>${escapeHtml(call[key])}</p></div>
      `).join("")}
    </div>
  `;
}

function renderResult(call) {
  if (!call || !call.id) return;
  const summaryStatus = call.summary_generation_status || "pending";
  resultEl.hidden = false;
  const summaryContent = summaryStatus === "completed"
    ? renderSummary(call.summary_data)
    : summaryStatus === "failed"
      ? `<p>Summary generation failed.</p>${retryButton(call.id)}`
      : '<p class="summary-loading"><span class="spinner" aria-hidden="true"></span>Generating summary...</p>';
  resultEl.innerHTML = `
    <h2>Call result</h2>
    <p>${renderStatusBadge(displayStatus(call))}</p>
    ${summaryContent}
  `;
  resultEl.innerHTML += renderLeadDetails(call);
  bindRetryButtons(resultEl);
}

async function pollSummary(callId) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const call = await requestJson(
        `/api/calls/${callId}`,
        { headers: apiHeaders() },
        "Could not load the call result.",
      );
      renderResult(call);
      if (call.summary_generation_status !== "pending") return;
    } catch (error) {
      resultEl.hidden = false;
      resultEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      setError(error.message);
      return;
    }
    await new Promise((resolve) => {
      summaryPollTimer = setTimeout(resolve, 2000);
    });
  }
  try {
    renderResult(await requestJson(
      `/api/calls/${callId}`,
      { headers: apiHeaders() },
      "Could not load the call result.",
    ));
  } catch (error) {
    resultEl.hidden = false;
    resultEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    setError(error.message);
  }
}

async function renderHistoryDetail(call) {
  selectedHistoryId = call.id;
  historyDetailEl.hidden = false;
  historyDetailEl.innerHTML = `
    <h3>Call #${call.id}</h3>
    <p class="history-meta">Started ${escapeHtml(formatDate(call.started_at))} · ${formatDuration(Math.round(call.duration_seconds || 0))}</p>
    <p>${renderStatusBadge(displayStatus(call))}</p>
    <h4>Summary</h4>
    <div>${renderSummary(call.summary_data)}</div>
    ${call.summary_generation_status !== "completed" ? retryButton(call.id) : ""}
    ${renderLeadDetails(call)}
    <h4>Transcript</h4>
    <div class="history-transcript">${renderHistoryTranscript(call.transcript)}</div>
    <h4>Recording</h4>
    <p id="recording-state">Loading recording...</p>
  `;
  bindRetryButtons(historyDetailEl);
  renderHistoryList();
  const recordingState = document.getElementById("recording-state");
  if (!call.has_recording) {
    recordingState.textContent = "Recording unavailable.";
    return;
  }
  try {
    const data = await requestJson(
      `/api/calls/${call.id}/recording`,
      { headers: apiHeaders() },
      "Recording unavailable or playback failed.",
    );
    const player = document.createElement("audio");
    player.className = "history-recording";
    player.controls = true;
    player.preload = "metadata";
    player.src = data.url;
    recordingState.replaceWith(player);
  } catch (error) {
    recordingState.textContent = error.message;
    setError(error.message);
  }
}

function renderHistoryList() {
  historyListEl.innerHTML = "";
  if (!historyCalls.length) {
    historyListEl.innerHTML = '<p class="history-empty">No calls yet</p>';
    return;
  }
  for (const call of historyCalls) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.setAttribute("aria-current", String(call.id === selectedHistoryId));
    button.innerHTML = `
      <span>
        <span class="history-item-title">Call #${escapeHtml(call.id)}</span>
        <span class="history-item-meta">${escapeHtml(formatDate(call.started_at))} · ${formatDuration(Math.round(call.duration_seconds || 0))}</span>
      </span>
      <span class="history-item-status">${renderStatusBadge(displayStatus(call))}</span>
    `;
    button.addEventListener("click", () => selectHistoryCall(call.id));
    historyListEl.appendChild(button);
  }
}

async function loadHistory() {
  historyStatusEl.textContent = "Loading history...";
  try {
    historyCalls = await requestJson(
      "/api/calls?limit=50",
      { headers: apiHeaders() },
      "Call history is temporarily unavailable.",
    );
    if (!Array.isArray(historyCalls)) {
      throw new Error("Call history returned an invalid response.");
    }
    historyUnlockEl.hidden = true;
    historyStatusEl.textContent = historyCalls.length
      ? `${historyCalls.length} saved call${historyCalls.length === 1 ? "" : "s"}`
      : "";
    renderHistoryList();
    if (selectedHistoryId) {
      const selected = historyCalls.find((call) => call.id === selectedHistoryId);
      if (selected) await selectHistoryCall(selected.id);
    }
  } catch (error) {
    if (error.status === 401) {
      clearHistoryPassword();
      historyUnlockEl.hidden = false;
      historyStatusEl.textContent = "";
      return;
    }
    historyCalls = [];
    historyStatusEl.textContent = error.message;
    historyListEl.innerHTML = `<p class="history-error">${escapeHtml(error.message)} Try Refresh.</p>`;
    setError(error.message);
    historyDetailEl.hidden = true;
  }

  async function selectHistoryCall(callId) {
    try {
      const call = await requestJson(
        `/api/calls/${callId}`,
        { headers: apiHeaders() },
        "Could not load this call.",
      );
      await renderHistoryDetail(call);
    } catch (error) {
      if (error.status === 401) {
        clearHistoryPassword();
        return;
      }
      historyDetailEl.textContent = error.message;
      setError(error.message);
    }
  }

  function clearHistoryPassword() {
    try {
      sessionStorage.removeItem("historyPassword");
    } catch (_error) {
      // Storage may be unavailable.
    }
    historyUnlockEl.hidden = false;
  }
}

function setDuration(seconds) {
  durationEl.textContent = formatDuration(seconds);
}

// Audio capture
function clearTranscript() {
  activeTranscriptEntry = null;
  activeTranscriptSpeaker = null;
  transcriptEl.innerHTML = "";
  const empty = document.createElement("p");
  empty.className = "transcript-empty";
  empty.textContent = "Press Start Call and say hello to Vaani";
  transcriptEl.appendChild(empty);
}

function mergeTranscriptText(previous, fragment) {
  const next = fragment.trim();
  if (!previous) {
    return next;
  }
  if (!next || previous === next || previous.endsWith(next)) {
    return previous;
  }
  if (next.startsWith(previous)) {
    return next;
  }
  const maxOverlap = Math.min(previous.length, next.length);
  for (let length = maxOverlap; length > 0; length -= 1) {
    if (previous.slice(-length).toLowerCase() === next.slice(0, length).toLowerCase()) {
      return `${previous}${next.slice(length)}`.replace(/\s+/g, " ").trim();
    }
  }
  return `${previous} ${next}`.replace(/\s+/g, " ").trim();
}

function addTranscriptMessage(speaker, text, partial) {
  if (!text) {
    return;
  }
  if (activeTranscriptSpeaker !== speaker) {
    finalizeTranscriptMessage();
    activeTranscriptSpeaker = speaker;
    activeTranscriptEntry = null;
  }
  const message = activeTranscriptEntry || document.createElement("p");
  if (!activeTranscriptEntry) {
    message.className = "transcript-message";
    const label = document.createElement("span");
    label.className = "transcript-speaker";
    label.textContent = speaker === "customer" ? "Customer" : "Assistant";
    message.appendChild(label);
    message.appendChild(document.createTextNode(""));
    transcriptEl.querySelector(".transcript-empty")?.remove();
    transcriptEl.appendChild(message);
    activeTranscriptEntry = message;
  }
  const previousText = message.lastChild.textContent;
  message.lastChild.textContent =
    !partial && message.dataset.partial === "true"
      ? text.trim()
      : mergeTranscriptText(previousText, text);
  message.dataset.partial = String(partial);
  if (!partial) {
    finalizeTranscriptMessage();
  }
  if (shouldAutoScroll) {
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
}

function showTranscriptUnavailable(message) {
  if (transcriptEl.querySelector(".transcript-message")) {
    return;
  }
  transcriptEl.innerHTML = "";
  const unavailable = document.createElement("p");
  unavailable.className = "transcript-empty";
  unavailable.textContent = message;
  transcriptEl.appendChild(unavailable);
}

function micErrorMessage(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "Microphone permission was denied. Allow the mic for this site and try again.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No microphone was found. Connect a mic and try again.";
  }
  if (name === "NotReadableError") {
    return "The microphone is already in use or could not be opened.";
  }
  if (name === "SecurityError") {
    return "Microphone access is blocked for this page.";
  }
  return `Could not initialize the call audio: ${err?.message || "unknown browser error"}`;
}

// Playback
function stopPlayback() {
  playGeneration += 1;
  nextPlayTime = 0;
  for (const source of activeSources) {
    try {
      source.stop();
    } catch (_err) {
      /* already stopped */
    }
  }
  activeSources = [];
}

function enqueuePcm16(pcmBuffer) {
  if (!playbackCtx) {
    return;
  }
  const generation = playGeneration;
  const int16 = new Int16Array(pcmBuffer);
  if (!int16.length) {
    return;
  }
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);
  }
  const buffer = playbackCtx.createBuffer(1, float32.length, OUTPUT_RATE);
  buffer.copyToChannel(float32, 0);
  const source = playbackCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackCtx.destination);
  if (recordingDestination) {
    source.connect(recordingDestination);
  }
  const startAt = Math.max(playbackCtx.currentTime, nextPlayTime);
  source.onended = () => {
    activeSources = activeSources.filter((item) => item !== source);
    if (!activeSources.length && assistantAudioPending) {
      assistantAudioPending = false;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "playback_idle" }));
      }
    }
  };
  try {
    source.start(startAt);
  } catch (_err) {
    return;
  }
  if (generation !== playGeneration) {
    try {
      source.stop();
    } catch (_stopErr) {
      /* interrupted */
    }
    return;
  }
  nextPlayTime = startAt + buffer.duration;
  activeSources.push(source);
}

// Websocket
function cleanup(closeSocket = true) {
  stopPlayback();
  if (ws && closeSocket) {
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
    ws = null;
  }
  if (captureNode) {
    captureNode.port.onmessage = null;
    captureNode.disconnect();
    captureNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (recordingSourceNode) {
    recordingSourceNode.disconnect();
    recordingSourceNode = null;
  }
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) {
      track.stop();
    }
    mediaStream = null;
  }
  if (captureCtx) {
    captureCtx.close();
    captureCtx = null;
  }
  if (playbackCtx) {
    playbackCtx.close();
    playbackCtx = null;
  }
  recordingDestination = null;
  recorder = null;
}

function finishCall() {
  if (callPhase === "idle") return;
  if (finishTimer) {
    clearTimeout(finishTimer);
    finishTimer = null;
  }
  cleanup();
  callPhase = "idle";
  startBtn.disabled = false;
  endBtn.disabled = true;
  setStatus("ended");
  if (currentCallId) {
    pollSummary(currentCallId);
  }
}

async function endCall() {
  if (callPhase !== "active") return;
  callPhase = "ending";
  setStatus("ending");
  startBtn.disabled = true;
  endBtn.disabled = true;
  if (recorder && recorder.state === "recording") {
    recordingUploadPromise = new Promise((resolve) => {
      const previous = recorder.onstop;
      recorder.onstop = async () => {
        if (previous) await previous();
        resolve();
      };
    });
    recorder.stop();
    await recordingUploadPromise;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" }));
    stopPlayback();
    if (mediaStream) {
      for (const track of mediaStream.getTracks()) track.stop();
      mediaStream = null;
    }
    finishTimer = setTimeout(finishCall, 4000);
  } else {
    finishCall();
  }
}

function handleSocketMessage(event) {
  if (typeof event.data !== "string") {
    assistantAudioPending = true;
    enqueuePcm16(event.data);
    return;
  }
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch (_err) {
    return;
  }
  if (payload.type === "status" && payload.value) {
    setStatus(payload.value);
    if (payload.value === "listening") {
      finalizeTranscriptMessage();
      if (!assistantAudioPending && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "playback_idle" }));
      }
    }
    return;
  }
  if (payload.type === "duration" && Number.isFinite(payload.seconds)) {
    setDuration(payload.seconds);
    return;
  }
  if (payload.type === "call_started") {
    currentCallId = payload.call_id;
    callToken = payload.token;
    recordingCallId = payload.call_id;
    recordingCallToken = payload.token;
    return;
  }
  if (payload.type === "transcript" && payload.speaker && payload.text) {
    addTranscriptMessage(payload.speaker, payload.text, payload.partial === true);
    return;
  }
  if (payload.type === "transcript_unavailable" && payload.message) {
    showTranscriptUnavailable(payload.message);
    return;
  }
  if (payload.type === "interrupted") {
    stopPlayback();
    assistantAudioPending = false;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "playback_idle" }));
    }
    finalizeTranscriptMessage();
    setStatus("listening");
    return;
  }
  if (payload.type === "error" && payload.message) {
    setError(payload.message);
    if (payload.fatal !== false) {
      endCall();
    }
  }
}

// Call flow
async function startCall() {
  if (callPhase !== "idle") {
    return;
  }
  callPhase = "active";
  setError("");
  setStatus("connecting");
  setDuration(0);
  clearTranscript();
  if (summaryPollTimer) {
    clearTimeout(summaryPollTimer);
    summaryPollTimer = null;
  }
  currentCallId = null;
  callToken = null;
  recordingCallId = null;
  recordingCallToken = null;
  recordingUploadPromise = null;
  recordingConsentSent = false;
  resultEl.hidden = true;
  startBtn.disabled = true;
  endBtn.disabled = false;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    captureCtx = new AudioContext();
    playbackCtx = new AudioContext({ sampleRate: OUTPUT_RATE });
    await captureCtx.resume();
    await playbackCtx.resume();
    await captureCtx.audioWorklet.addModule("/static/pcm-capture-processor.js");
    sourceNode = captureCtx.createMediaStreamSource(mediaStream);
    captureNode = new AudioWorkletNode(captureCtx, "pcm-capture");
    captureNode.port.onmessage = (event) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(event.data);
      }
    };
    sourceNode.connect(captureNode);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/voice`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = handleSocketMessage;
    ws.onerror = () => {
      setError("The voice connection failed.");
      endCall();
    };
    ws.onclose = () => {
      ws = null;
      if (callPhase === "active") {
        endCall();
      } else if (callPhase === "ending") {
        finishCall();
      }
    };
    ws.onopen = () => {
      if (recordingConsent.checked && recorder && !recordingConsentSent) {
        ws.send(JSON.stringify({ type: "recording_consent", value: true }));
        recordingConsentSent = true;
      }
    };
    if (recordingConsent.checked) {
      recordingType = ["audio/webm", "audio/ogg"].find((type) =>
        MediaRecorder.isTypeSupported(type),
      );
      if (recordingType) {
        recordingChunks = [];
        recordingDestination = playbackCtx.createMediaStreamDestination();
        recordingSourceNode = playbackCtx.createMediaStreamSource(mediaStream);
        recordingSourceNode.connect(recordingDestination);
        recorder = new MediaRecorder(recordingDestination.stream, {
          mimeType: recordingType,
        });
        recorder.ondataavailable = (event) => {
          if (event.data.size) recordingChunks.push(event.data);
        };
        recorder.onstop = uploadRecording;
        recorder.start();
        if (ws.readyState === WebSocket.OPEN && !recordingConsentSent) {
          ws.send(JSON.stringify({ type: "recording_consent", value: true }));
          recordingConsentSent = true;
        }
      } else {
        setError("This browser cannot record in a supported audio format. The call can continue without recording.");
      }
    }
  } catch (err) {
    setError(micErrorMessage(err));
    endCall();
  }
}

async function uploadRecording() {
  if (!recordingChunks.length) return;
  if (!recordingCallId) {
    setError("The call ended, but its recording could not be linked to the call record.");
    return;
  }
  try {
    await requestJson(`/api/calls/${recordingCallId}/recording`, {
      method: "PUT",
      headers: {
        "Content-Type": recordingType,
        ...(recordingCallToken ? { "X-Call-Token": recordingCallToken } : {}),
      },
      body: new Blob(recordingChunks, { type: recordingType }),
    }, "The recording could not be saved.");
  } catch (error) {
    setError(`The call ended, but its recording could not be saved: ${error.message}`);
  }
}

startBtn.addEventListener("click", startCall);
endBtn.addEventListener("click", endCall);
refreshHistoryBtn.addEventListener("click", loadHistory);

historyUnlockButton.addEventListener("click", () => {
  try {
    sessionStorage.setItem("historyPassword", historyPasswordEl.value);
  } catch (_error) {
    setError("Could not store the history password in this browser.");
    return;
  }
  loadHistory();
});
transcriptEl.addEventListener("scroll", () => {
  shouldAutoScroll =
    transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 16;
});
window.addEventListener("beforeunload", cleanup);
loadHistory();
