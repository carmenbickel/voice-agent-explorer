const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
const messages = document.querySelector('#messages');
const status = document.querySelector('#status');
const error = document.querySelector('#error');
let pending = false;

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
