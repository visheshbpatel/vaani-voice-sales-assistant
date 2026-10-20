const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const startBtn = document.getElementById("start");
const endBtn = document.getElementById("end");
const durationEl = document.getElementById("duration");
const transcriptEl = document.getElementById("transcript");

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

function setDuration(seconds) {
  durationEl.textContent = formatDuration(seconds);
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
  message.lastChild.textContent = mergeTranscriptText(
    message.lastChild.textContent,
    text,
  );
  message.dataset.partial = String(partial);
  if (!partial) {
    finalizeTranscriptMessage();
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
  return "Could not access the microphone.";
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
  starting = false;
  startBtn.disabled = false;
  endBtn.disabled = true;
}

function endCall() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" }));
    cleanup(false);
  } else {
    cleanup();
  }
  setStatus("ended");
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
    endCall();
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
    starting = false;
  } catch (err) {
    setError(micErrorMessage(err));
    endCall();
  }
}

startBtn.addEventListener("click", startCall);
endBtn.addEventListener("click", endCall);
transcriptEl.addEventListener("scroll", () => {
  shouldAutoScroll =
    transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 16;
});
window.addEventListener("beforeunload", cleanup);
