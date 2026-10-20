const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const startBtn = document.getElementById("start");
const endBtn = document.getElementById("end");

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

function setStatus(value) {
  statusEl.textContent = value;
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

function cleanup() {
  stopPlayback();
  if (ws) {
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
  cleanup();
  setStatus("ended");
}

function handleSocketMessage(event) {
  if (typeof event.data !== "string") {
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
    return;
  }
  if (payload.type === "interrupted") {
    stopPlayback();
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
  startBtn.disabled = true;
  endBtn.disabled = false;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
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
window.addEventListener("beforeunload", cleanup);
