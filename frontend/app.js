const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const messages = document.querySelector('#messages');
const status = document.querySelector('#status');
const error = document.querySelector('#error');
const customerSelect = document.querySelector('#demo-customer');
const resetButton = document.querySelector('#reset');
const listenButton = document.querySelector('#listen');
const stopButton = document.querySelector('#stop');
const logic = window.voiceLogic;
let pending = false;
let sessionReady = false;
let latestTurnId = 0;
let voiceState = 'idle';
let recognition = null;
let listeningForTurn = false;
let micDenied = false;

const speechApi = {
  recognitionSupported: Boolean(
    window.SpeechRecognition || window.webkitSpeechRecognition),
  synthesisSupported: 'speechSynthesis' in window,
};

const capability = logic.resolveCapability({
  recognitionSupported: speechApi.recognitionSupported,
  synthesisSupported: speechApi.synthesisSupported,
  microphoneDenied: false,
});

function clearTranscript() {
  messages.replaceChildren();
}

function setStatus(text) {
  status.textContent = text;
}

function setVoiceState(state) {
  voiceState = state;
  renderVoiceState();
}

function renderVoiceState() {
  listenButton.disabled = pending
    || !capability.capable
    || voiceState === 'listening'
    || voiceState === 'speaking';
  stopButton.hidden = voiceState !== 'listening' && voiceState !== 'speaking';
  const labels = {
    idle: '',
    listening: 'Listening… speak now',
    transcribing: 'Transcribing…',
    thinking: 'Assistant is thinking…',
    speaking: 'Speaking… click Stop to interrupt playback',
  };
  setStatus(labels[voiceState] || '');
}

function cancelSpeech() {
  if (speechApi.synthesisSupported && window.speechSynthesis.speaking) {
    window.speechSynthesis.cancel();
  }
}

function stopActiveVoice() {
  cancelSpeech();
  cancelListening();
  setVoiceState('idle');
  setStatus('Voice interaction stopped. This did not cancel a backend action.');
}

function cancelListening() {
  listeningForTurn = false;
  if (recognition) {
    try { recognition.stop(); } catch (failure) { /* already stopped */ }
    recognition = null;
  }
}

function addMessage(role, text) {
  const message = document.createElement('article');
  message.className = `message ${role}`;
  const sender = document.createElement('p');
  sender.className = 'sender';
  sender.textContent = role === 'user' ? 'You' : 'Assistant';
  const content = document.createElement('p');
  content.className = 'content';
  content.textContent = text;
  message.append(sender, content);
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
  return message;
}

/** Speak a response only when its turn is still the newest turn. */
/** Render the sanitized per-turn trace panel below the latest message. */
function showTrace(data) {
  if (!data.trace_id) return;
  fetch(`/traces/${encodeURIComponent(data.trace_id)}`)
    .then((response) => (response.ok ? response.json() : null))
    .then((trace) => {
      if (!trace) return;
      const panel = document.createElement('details');
      panel.className = 'trace-panel';
      const summary = document.createElement('summary');
      summary.textContent = `Trace ${trace.trace_id.slice(0, 6)} — ${trace.outcome}`;
      const events = document.createElement('ul');
      for (const event of trace.events) {
        const line = document.createElement('li');
        line.className = `trace-${event.status}`;
        const state = {
          executed: '✓', skipped: '—', failed: '✗', denied: '✗', pending: '…',
        }[event.status] || event.status;
        line.textContent = `${state} ${event.stage}`
          + (event.duration_ms !== undefined ? ` (${event.duration_ms} ms)` : '')
          + (event.detail ? ` — ${event.detail}` : '');
        events.append(line);
      }
      panel.append(summary, events);
      messages.append(panel);
      messages.scrollTop = messages.scrollHeight;
    })
    .catch(() => {});
}

function speakResponse(text, turnId) {
  if (!logic.shouldSpeak(turnId, latestTurnId)) {
    setVoiceState('idle');
    return;
  }
  setVoiceState('speaking');
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.onend = () => {
    if (voiceState === 'speaking') setVoiceState('idle');
  };
  utterance.onerror = () => {
    if (voiceState === 'speaking') {
      setVoiceState('idle');
      setStatus('Speech playback failed. The response is in the transcript above.');
    }
  };
  window.speechSynthesis.speak(utterance);
}

async function submitMessage(message) {
  if (pending || !message || !sessionReady) return;
  if (voiceState === 'speaking') cancelSpeech();
  pending = true;
  input.disabled = true;
  send.disabled = true;
  error.hidden = true;
  error.textContent = '';
  setVoiceState('thinking');
  const userMessage = addMessage('user', message);
  latestTurnId += 1;
  const turnId = latestTurnId;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 65000);

  try {
    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error('API request failed');
    const data = await response.json();
    if (typeof data.response !== 'string' || !data.response.trim()) {
      throw new Error('Invalid response');
    }
    addMessage('assistant', data.response);
    showTrace(data, turnId);
    if (speechApi.synthesisSupported) {
      speakResponse(data.response, turnId);
    } else {
      setVoiceState('idle');
    }
  } catch (failure) {
    if (turnId === latestTurnId) {
      userMessage.querySelector('.sender').textContent = 'You · No response received';
      error.textContent = failure.name === 'AbortError'
        ? 'The response took too long. Your question is below so you can try again.'
        : 'Could not get a response. Check that the backend and Ollama are running, then try again.';
      error.hidden = false;
      input.value = message;
    } else {
      // A newer turn has taken over; this late failure must not disturb it.
      userMessage.querySelector('.sender').textContent = 'You · Response arrived late';
    }
    setVoiceState('idle');
  } finally {
    clearTimeout(timeout);
    pending = false;
    renderVoiceState();
    input.disabled = false;
    send.disabled = false;
    input.focus();
  }
}

function startListening() {
  error.hidden = true;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !capability.capable) {
    error.textContent = logic.describeError('not-allowed');
    error.hidden = false;
    return;
  }
  recognition = new Recognition();
  recognition.lang = document.documentElement.lang || 'en';
  listeningForTurn = true;
  setVoiceState('listening');

  recognition.onerror = (event) => {
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      micDenied = true;
      updateCapability();
    }
    error.textContent = logic.describeError(event.error || 'unknown');
    error.hidden = false;
    cancelListening();
    setVoiceState('idle');
  };

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    cancelListening();
    setVoiceState('transcribing');
    input.value = transcript;
    submitMessage(transcript);
  };

  recognition.onend = () => {
    recognition = null;
    if (listeningForTurn && voiceState === 'listening') {
      setVoiceState('idle');
      listeningForTurn = false;
    }
  };

  try {
    recognition.start();
  } catch (failure) {
    // Some browsers throw synchronously outside their supported context.
    recognition.onerror({ error: 'audio-capture' });
  }
}

function updateCapability() {
  const resolved = logic.resolveCapability({
    recognitionSupported: speechApi.recognitionSupported,
    synthesisSupported: speechApi.synthesisSupported,
    microphoneDenied: micDenied,
  });
  capability.mode = resolved.mode;
  capability.capable = resolved.capable;
  capability.status = resolved.status;
  if (capability.status) {
    setStatus(capability.status.title);
  }
}

async function createSession(customerId) {
  const response = await fetch('/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(customerId ? { customer_id: customerId } : {}),
  });
  if (!response.ok) throw new Error('Could not start a session');
  const data = await response.json();
  sessionReady = true;
  updateCustomerLabel(data.customer);
  return data.customer;
}

function loadCustomers() {
  fetch('/sessions/customers')
    .then((response) => response.json())
    .then((data) => {
      for (const customer of data.customers) {
        const option = document.createElement('option');
        option.value = customer.id;
        option.textContent = `${customer.name} (${customer.id})`;
        customerSelect.append(option);
      }
    })
    .catch(() => {});
}

function updateCustomerLabel(customer) {
  for (const option of customerSelect.options) {
    option.selected = customer && option.value === customer.id;
  }
  if (!customer) {
    customerSelect.selectedIndex = 0;
  }
}

customerSelect.addEventListener('change', async () => {
  error.hidden = true;
  const customerId = customerSelect.value;
  if (!customerId || !sessionReady) return;
  const response = await fetch('/sessions/customer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customer_id: customerId }),
  });
  if (response.ok) {
    clearTranscript();
    addMessage('assistant', 'Demo customer switched. The assistant has no memory of previous conversations.');
  } else {
    error.textContent = 'Could not switch the demo customer.';
    error.hidden = false;
    updateCustomerLabel(null);
  }
});

resetButton.addEventListener('click', async () => {
  if (!sessionReady) return;
  const response = await fetch('/sessions/reset', { method: 'POST' });
  if (response.ok) {
    clearTranscript();
    addMessage('assistant', 'Conversation cleared. The assistant has no memory of previous messages.');
  } else {
    error.textContent = 'Could not reset the conversation.';
    error.hidden = false;
  }
});

listenButton.addEventListener('click', () => {
  if (voiceState === 'listening') {
    stopActiveVoice();
    return;
  }
  startListening();
});

stopButton.addEventListener('click', () => stopActiveVoice());

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (pending || !message) return;
  input.value = '';
  submitMessage(message);
});

createSession().catch(() => {});
loadCustomers();
