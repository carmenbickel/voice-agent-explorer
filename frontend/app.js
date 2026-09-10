const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const messages = document.querySelector('#messages');
const status = document.querySelector('#status');
const error = document.querySelector('#error');
const customerSelect = document.querySelector('#demo-customer');
const resetButton = document.querySelector('#reset');
let pending = false;
let sessionReady = false;

function clearTranscript() {
  messages.replaceChildren();
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
    addMessage('assistant', `Demo customer switched. The assistant has no memory of previous conversations.`);
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

createSession().catch(() => {});
loadCustomers();

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

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (pending || !message) return;

  pending = true;
  input.disabled = true;
  send.disabled = true;
  error.hidden = true;
  error.textContent = '';
  status.textContent = 'Assistant is thinking…';
  const userMessage = addMessage('user', message);
  input.value = '';
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
  } catch (failure) {
    userMessage.querySelector('.sender').textContent = 'You · No response received';
    error.textContent = failure.name === 'AbortError'
      ? 'The response took too long. Your question is below so you can try again.'
      : 'Could not get a response. Check that the backend and Ollama are running, then try again.';
    error.hidden = false;
    input.value = message;
  } finally {
    clearTimeout(timeout);
    pending = false;
    status.textContent = '';
    input.disabled = false;
    send.disabled = false;
    input.focus();
  }
});
