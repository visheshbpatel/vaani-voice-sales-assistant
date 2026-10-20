const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const startBtn = document.getElementById("start");
const endBtn = document.getElementById("end");
const durationEl = document.getElementById("duration");
const transcriptEl = document.getElementById("transcript");
const followUpEl = document.getElementById("follow-up");
const recordingConsent = document.getElementById("recording-consent");
const leadNameEl = document.getElementById("lead-name");
const leadCompanyEl = document.getElementById("lead-company");
const leadContactEl = document.getElementById("lead-contact");
const leadPreferredTimeEl = document.getElementById("lead-preferred-time");
const refreshHistoryBtn = document.getElementById("refresh-history");
const historyStatusEl = document.getElementById("history-status");
const historyListEl = document.getElementById("history-list");
const historyDetailEl = document.getElementById("history-detail");

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
let starting = false;
let transcriptEntries = new Map();
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
let pendingLeadDetail = null;
let recordingConsentSent = false;
let customerTranscript = [];
let leadDetails = { name: "", company: "", contact: "", preferred_time: "" };
let leadDetailsConfirmed = false;
let historyCalls = [];
let selectedHistoryId = null;

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
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function formatDate(value) {
  if (!value) return "Start unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Start unavailable" : date.toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayStatus(status) {
  return status || "No Response";
}

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

async function renderHistoryDetail(call) {
  selectedHistoryId = call.id;
  historyDetailEl.hidden = false;
  historyDetailEl.innerHTML = `
    <h3>Call #${call.id}</h3>
    <p class="history-meta">Started ${escapeHtml(formatDate(call.started_at))} · ${formatDuration(Math.round(call.duration_seconds || 0))} · ${escapeHtml(displayStatus(call.status))}</p>
    <h4>Summary</h4>
    <p>${escapeHtml(call.summary || "Summary unavailable.")}</p>
    <h4>Transcript</h4>
    <div class="history-transcript">${renderHistoryTranscript(call.transcript)}</div>
    <h4>Recording</h4>
    <p id="recording-state">Loading recording...</p>
  `;
  renderHistoryList();
  const recordingState = document.getElementById("recording-state");
  if (!call.recording_object_key) {
    recordingState.textContent = "Recording unavailable.";
    return;
  }
  try {
    const response = await fetch(`/api/calls/${call.id}/recording`);
    if (!response.ok) throw new Error("recording unavailable");
    const data = await response.json();
    const player = document.createElement("audio");
    player.className = "history-recording";
    player.controls = true;
    player.preload = "metadata";
    player.src = data.url;
    recordingState.replaceWith(player);
  } catch (_error) {
    recordingState.textContent = "Recording unavailable or playback failed.";
  }
}

function renderHistoryList() {
  historyListEl.innerHTML = "";
  if (!historyCalls.length) {
    historyListEl.innerHTML = '<p class="history-empty">No calls saved yet.</p>';
    return;
  }
  for (const call of historyCalls) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.setAttribute("aria-current", String(call.id === selectedHistoryId));
    button.innerHTML = `
      <span>
        <span class="history-item-title">Call #${call.id}</span>
        <span class="history-item-meta">${formatDate(call.started_at)} · ${formatDuration(Math.round(call.duration_seconds || 0))}</span>
      </span>
      <span class="history-item-status">${displayStatus(call.status)}</span>
    `;
    button.addEventListener("click", () => renderHistoryDetail(call));
    historyListEl.appendChild(button);
  }
}

async function loadHistory() {
  historyStatusEl.textContent = "Loading history...";
  try {
    const response = await fetch("/api/calls");
    if (!response.ok) throw new Error("history unavailable");
    historyCalls = await response.json();
    historyStatusEl.textContent = historyCalls.length
      ? `${historyCalls.length} saved call${historyCalls.length === 1 ? "" : "s"}`
      : "";
    renderHistoryList();
    if (selectedHistoryId) {
      const selected = historyCalls.find((call) => call.id === selectedHistoryId);
      if (selected) await renderHistoryDetail(selected);
    }
  } catch (_error) {
    historyCalls = [];
    historyStatusEl.textContent = "Call history is temporarily unavailable.";
    historyListEl.innerHTML = '<p class="history-error">Could not load saved calls. Try Refresh.</p>';
    historyDetailEl.hidden = true;
  }
}

function setDuration(seconds) {
  durationEl.textContent = formatDuration(seconds);
}

function setLeadDetail(name, value) {
  if (value && leadDetails[name] !== undefined) {
    leadDetails[name] = value.trim();
  }
}

function extractAssistantLeadDetails(text) {
  const value = text.trim();
  const name = value.match(
    /(?:your|the) name is\s+(.+?)(?=,\s*(?:the )?company is|,\s*company is)/i,
  );
  const company = value.match(
    /(?:the )?company is\s+(.+?)(?=,\s*(?:your )?(?:email|phone)|,\s*and\s+(?:your )?(?:email|phone))/i,
  );
  const email = value.match(/[^\s,;]+@[^\s,;]+\.[^\s,;]+/);
  const phone = value.match(/(?:\+?\d[\d\s().-]{7,}\d)/);
  const preferred = value.match(
    /(?:you.?d prefer|preferred time is|prefer(?:red)? time(?: would be)? is?)\s+(.+?)(?=\.\s|,\s*is that|\?|$)/i,
  );
  if (name) setLeadDetail("name", name[1].trim());
  if (company) setLeadDetail("company", company[1].trim());
  if (email) setLeadDetail("contact", email[0]);
  else if (phone) setLeadDetail("contact", phone[0].trim());
  if (preferred) {
    setLeadDetail(
      "preferred_time",
      preferred[1]
        .replace(/^the demo\s+/i, "")
        .replace(/[.!]+$/, "")
        .trim(),
    );
  }
  if (name || company || email || phone || preferred) {
    leadDetailsConfirmed = true;
    renderLeadDetails();
  }
}

function renderLeadDetails() {
  leadNameEl.textContent = leadDetails.name || "Not collected";
  leadCompanyEl.textContent = leadDetails.company || "Not collected";
  leadContactEl.textContent = leadDetails.contact || "Not collected";
  leadPreferredTimeEl.textContent = leadDetails.preferred_time || "Not collected";
}

function clearTranscript() {
  transcriptEntries = new Map();
  activeTranscriptEntry = null;
  activeTranscriptSpeaker = null;
  transcriptEl.innerHTML = "";
  const empty = document.createElement("p");
  empty.className = "transcript-empty";
  empty.textContent = "Transcript will appear here when available.";
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
  if (speaker === "assistant" && !partial) {
    const completeText = message.lastChild.textContent;
    extractAssistantLeadDetails(completeText);
    pendingLeadDetail = detailPromptType(completeText);
  }
  if (speaker === "customer" && !partial) {
    const completeText = message.lastChild.textContent;
    updateLeadDraft(completeText);
    if (/^(correct|yes|that'?s correct|looks good)$/i.test(completeText.trim())) {
      leadDetailsConfirmed = true;
      renderLeadDetails();
    }
  }
  message.dataset.partial = String(partial);
  if (!partial) {
    finalizeTranscriptMessage();
  }

  function detailPromptType(text) {
    const value = text.toLowerCase();
    if (value.includes("full name") || value.includes("your name")) return "name";
    if (value.includes("company")) return "company";
    if (value.includes("phone") || value.includes("email")) return "contact";
    if (value.includes("preferred time") || value.includes("best time")) return "preferred_time";
    return null;
  }

  function updateLeadDraft(text) {
    const value = text.trim();
    customerTranscript.push(value);
    const email = value.match(/[^\s,;]+@[^\s,;]+\.[^\s,;]+/);
    const phone = value.match(/(?:\+?\d[\d\s().-]{7,}\d)/);
    const name = value.match(/(?:my\s+)?name\s+is\s+([^,.;]+?)(?=\s+and\s+|\s+contact\s+is\s+|$)/i);
    const company = value.match(/(?:my\s+)?company\s+is\s+([^,.;]+?)(?=\s+and\s+|$)/i);
    if (name) setLeadDetail("name", name[1]);
    if (company) setLeadDetail("company", company[1]);
    if (email) {
      setLeadDetail("contact", email[0]);
    }
    if (phone) {
      setLeadDetail("contact", phone[0]);
    }
    if (!name && pendingLeadDetail === "name") {
      setLeadDetail("name", value);
    }
    if (!company && pendingLeadDetail === "company") {
      setLeadDetail("company", value);
    }
    if (pendingLeadDetail === "preferred_time") {
      setLeadDetail("preferred_time", value);
    }
    if (pendingLeadDetail && !email) {
      setLeadDetail(pendingLeadDetail, value
        .replace(/^(my name is|name is|my company is|company is)\s+/i, "")
        .replace(/[.!?]+$/, "")
        .trim());
    }
    if (pendingLeadDetail === "contact" && !email) {
      setLeadDetail("contact", value);
    }
    pendingLeadDetail = null;
    extractLeadDetailsFromTranscript();
  }

  function extractLeadDetailsFromTranscript() {
    const text = customerTranscript.join(" ");
    const email = text.match(/[^\s,;]+@[^\s,;]+\.[^\s,;]+/);
    const phone = text.match(/(?:\+?\d[\d\s().-]{7,}\d)/);
    const name = text.match(/(?:my\s+)?name\s+is\s+([^,.;]+?)(?=\s+and\s+|\s+contact\s+is\s+|$)/i);
    const company = text.match(/(?:my\s+)?company\s+is\s+([^,.;]+?)(?=\s+and\s+|$)/i);
    if (name) setLeadDetail("name", name[1]);
    if (company) setLeadDetail("company", company[1]);
    if (email) setLeadDetail("contact", email[0]);
    else if (phone) setLeadDetail("contact", phone[0]);
    renderLeadDetails();
  }
  if (shouldAutoScroll) {
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
}

function showTranscriptUnavailable(message) {
  if (transcriptEntries.size || transcriptEl.querySelector(".transcript-message")) {
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
  starting = false;
  startBtn.disabled = false;
  endBtn.disabled = true;
}

async function endCall() {
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
    if (Object.values(leadDetails).some(Boolean)) {
      ws.send(JSON.stringify({ type: "lead_details", value: leadDetails }));
    }
    ws.send(JSON.stringify({ type: "end" }));
    cleanup(false);
  } else {
    cleanup();
  }
  setStatus("ended");
  await saveLeadDetails();
  followUpEl.hidden = false;
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

async function startCall() {
  if (starting || ws) {
    return;
  }
  starting = true;
  setError("");
  setStatus("connecting");
  setDuration(0);
  clearTranscript();
  currentCallId = null;
  recordingUploadPromise = null;
  pendingLeadDetail = null;
  recordingConsentSent = false;
  customerTranscript = [];
  leadDetails = { name: "", company: "", contact: "", preferred_time: "" };
  leadDetailsConfirmed = false;
  renderLeadDetails();
  followUpEl.hidden = true;
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
      if (statusEl.textContent !== "ended") {
        endCall();
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
    starting = false;
  } catch (err) {
    setError(micErrorMessage(err));
    endCall();
  }
}

async function uploadRecording() {
  if (!recordingChunks.length) return;
  if (!currentCallId) {
    setError("The call ended, but its recording could not be linked to the call record.");
    return;
  }
  try {
    const response = await fetch(`/api/calls/${currentCallId}/recording`, {
      method: "PUT",
      headers: { "Content-Type": recordingType },
      body: new Blob(recordingChunks, { type: recordingType }),
    });
    if (!response.ok) throw new Error("upload failed");
  } catch (_err) {
    setError("The call ended, but its recording could not be saved.");
  }
}

async function saveLeadDetails() {
  if (!currentCallId) {
    setError("This call was not saved, so collected details cannot be stored.");
    return;
  }
  try {
    let savedCall = null;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const response = await fetch(`/api/calls/${currentCallId}`);
      if (response.ok) {
        savedCall = await response.json();
        if (
          savedCall.lead_name ||
          savedCall.lead_company ||
          savedCall.lead_contact ||
          savedCall.lead_preferred_time
        ) {
          break;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    if (!savedCall) throw new Error("call unavailable");
    const savedDetails = {
      name: savedCall.lead_name || leadDetails.name,
      company: savedCall.lead_company || leadDetails.company,
      contact: savedCall.lead_contact || leadDetails.contact,
      preferred_time: savedCall.lead_preferred_time || leadDetails.preferred_time,
    };
    if (!Object.values(savedDetails).some(Boolean)) {
      throw new Error("details unavailable");
    }
    leadDetails = savedDetails;
    renderLeadDetails();
    const leadResponse = await fetch(`/api/calls/${currentCallId}/lead`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...savedDetails, consent: true }),
    });
    if (!leadResponse.ok) throw new Error("save failed");
    setError("");
  } catch (_err) {
    setError("Could not save collected details.");
  }
}

startBtn.addEventListener("click", startCall);
endBtn.addEventListener("click", endCall);
refreshHistoryBtn.addEventListener("click", loadHistory);
transcriptEl.addEventListener("scroll", () => {
  shouldAutoScroll =
    transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 16;
});
window.addEventListener("beforeunload", cleanup);
loadHistory();
