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
  sender.textContent = role === 'user' ? 'You' : 'FUN SHOES assistant';
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

/** Show the exact purchase proposal and its explicit confirm control. */
function showProposal(proposal) {
  const panel = document.createElement('div');
  panel.className = 'proposal';
  const title = document.createElement('p');
  title.className = 'proposal-title';
  title.textContent = `FUN SHOES ${proposal.kind} proposal (review before confirming)`;
  panel.append(title);
  const list = document.createElement('ul');
  for (const item of proposal.items || []) {
    const line = document.createElement('li');
    line.textContent = `${item.quantity} × ${item.name} size ${item.size} ${item.colour} — €${(item.unit_price_cents / 100).toFixed(2)}`;
    list.append(line);
  }
  const terms = document.createElement('p');
  terms.textContent = proposal.terms || proposal.summary || proposal.detail ||
    (proposal.order_id ? `Order: ${proposal.order_id}` : 'Review this request before confirming.');
  if (proposal.total_cents !== undefined) {
    terms.textContent += ` Total: €${(proposal.total_cents / 100).toFixed(2)}`;
  }
  panel.append(list, terms);
  const confirmButton = document.createElement('button');
  confirmButton.type = 'button';
  confirmButton.textContent = `Confirm ${proposal.kind}`;
  confirmButton.addEventListener('click', async () => {
    confirmButton.disabled = true;
    try {
      const response = await fetch(
        `/actions/${encodeURIComponent(proposal.proposal_id)}/confirm`, { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Proposal is no longer valid. Ask for a new proposal.');
      replaceWithResult(panel, result.detail ||
        `FUN SHOES ${proposal.kind} confirmed. Reference: ${result.return_reference || result.exchange_reference || result.ticket_reference || result.reference || result.order_id || result.operation_id}.`);
      loadCatalog();
      loadDemoOrders();
    } catch (failure) {
      replaceWithResult(panel, `${failure.message} If the outcome is uncertain, check the order before trying again.`);
    }
  });
  panel.append(confirmButton);
  messages.append(panel);
  messages.scrollTop = messages.scrollHeight;
}

function replaceWithResult(panel, text) {
  const note = document.createElement('p');
  note.className = 'proposal-note';
  note.textContent = text;
  panel.replaceChildren(note);
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
    if (data.action_proposal) showProposal(data.action_proposal);
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
  loadDemoOrders();
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
    loadDemoOrders();
    addMessage('assistant', 'Welcome to FUN SHOES! Customer switched; please give your order ID for order support.');
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
    loadDemoOrders();
    addMessage('assistant', 'Welcome back to FUN SHOES! How can I help with shoes or your order?');
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

async function loadCatalog() {
  const target = document.querySelector('#catalog');
  try {
    const response = await fetch('/catalog');
    if (!response.ok) throw new Error();
    const data = await response.json();
    target.replaceChildren();
    const products = new Map();
    for (const variant of data.variants) {
      if (!products.has(variant.product_id)) products.set(variant.product_id, []);
      products.get(variant.product_id).push(variant);
    }
    for (const variants of products.values()) {
      const card = document.createElement('article');
      card.className = 'product-card';
      const title = document.createElement('h3');
      title.textContent = variants[0].name;
      const category = document.createElement('p');
      category.textContent = variants[0].category;
      const list = document.createElement('ul');
      for (const v of variants) {
        const item = document.createElement('li');
        item.textContent = `EU ${v.size} · ${v.colour} · €${(v.price_cents / 100).toFixed(2)} · ${v.available > 0 ? v.available + ' available' : 'Out of stock'}`;
        list.append(item);
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `Ask about ${variants[0].name}`;
      button.onclick = () => {
        input.value = `Tell me about ${variants[0].name} at FUN SHOES`;
        input.focus();
        form.scrollIntoView({block: 'center'});
      };
      card.append(title, category, list, button);
      target.append(card);
    }
    if (!products.size) target.textContent = 'No products loaded. Initialize the demo fixtures as described in README.';
  } catch {
    target.textContent = 'Could not load shoes. Refresh the page to try again.';
  }
}

async function loadDemoOrders() {
  const target = document.querySelector('#demo-orders');
  target.textContent = 'Loading your demo orders…';
  try {
    const response = await fetch('/demo/orders');
    if (!response.ok) throw new Error();
    const data = await response.json();
    target.replaceChildren();
    for (const order of data.orders) {
      const line = document.createElement('p');
      line.textContent = `${order.id} — ${order.state}: ` + order.lines.map(item => `${item.quantity} × ${item.item_name}, EU ${item.size}, ${item.colour}`).join('; ');
      target.append(line);
    }
    if (!data.orders.length) target.textContent = 'Select a demo customer to see test orders.';
  } catch {
    target.textContent = 'Could not load demo orders. Select your customer again to retry.';
  }
}
loadCatalog();
