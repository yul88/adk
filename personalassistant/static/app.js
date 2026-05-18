function showLogin() {
  document.getElementById('signup-form').style.display = 'none';
  document.getElementById('login-form').style.display = 'block';
  document.getElementById('auth-error').classList.add('hidden');
}

function showSignup() {
  document.getElementById('login-form').style.display = 'none';
  document.getElementById('signup-form').style.display = 'block';
  document.getElementById('auth-error').classList.add('hidden');
}

function showError(msg) {
  const el = document.getElementById('auth-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// Helper function to handle auth requests
async function performAuth(endpoint, username, password) {
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await response.json();
    if (response.ok) {
      startApp();
    } else {
      showError(data.detail || 'Authentication failed');
    }
  } catch (e) {
    showError('Network error');
  }
}

async function login() {
  const u = document.getElementById('login-username').value;
  const p = document.getElementById('login-password').value;
  if (!u || !p) return showError('Please fill in all fields');

  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await response.json();
    if (response.ok) {
      startApp();
    } else {
      showError(data.detail || 'Login failed');
    }
  } catch (e) {
    showError('Network error');
  }
}

async function signup() {
  const u = document.getElementById('signup-username').value;
  const p = document.getElementById('signup-password').value;
  if (!u || !p) return showError('Please fill in all fields');

  try {
    const response = await fetch('/api/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: u, password: p })
    });
    const data = await response.json();
    if (response.ok) {
      startApp();
    } else {
      showError(data.detail || 'Signup failed');
    }
  } catch (e) {
    showError('Network error');
  }
}

async function logout() {
  try {
    await fetch('/api/logout', { method: 'POST' });
  } catch (e) {
    console.error('Logout failed:', e);
  }
  const chatApp = document.getElementById('chat-app');
  chatApp.classList.add('hidden');
  chatApp.classList.remove('flex'); // Ensure flex is removed
  document.getElementById('landing-page').classList.remove('hidden');

  // Clear inline styles if any
  chatApp.style.display = '';
  document.getElementById('landing-page').style.display = '';
  document.getElementById('login-form').style.display = '';
  document.getElementById('signup-form').style.display = '';

  // Clear Chat History
  document.getElementById('messages').innerHTML = `
            <div class="flex gap-4">
                <div class="flex-shrink-0">
                    <div class="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center text-primary">
                        <i class="bi bi-robot text-xl"></i>
                    </div>
                </div>
                <div class="bg-card border border-slate-700 px-6 py-4 rounded-2xl rounded-tl-sm shadow-md max-w-3xl">
                    <div class="prose">
                        <p>Hi! I'm your Personal Assistant. I can help you stash links or manage checklists. How can I assist you today?</p>
                    </div>
                </div>
            </div>`;

  showLogin();
}

function startApp() {
  document.getElementById('landing-page').classList.add('hidden');
  const chatApp = document.getElementById('chat-app');
  chatApp.classList.remove('hidden');
  chatApp.classList.add('flex');

  // Clear inline styles if any
  document.getElementById('landing-page').style.display = '';
  chatApp.style.display = '';

  document.getElementById('message-input').focus();
}

const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const messagesContainer = document.getElementById('messages');

function appendMessage(role, text) {
  const isUser = role === 'user';
  const msgDiv = document.createElement('div');
  msgDiv.className = `flex gap-4 ${isUser ? 'flex-row-reverse' : ''}`;

  // Avatar
  const avatar = document.createElement('div');
  avatar.className = 'flex-shrink-0';
  avatar.innerHTML = `
        <div class="w-10 h-10 rounded-full flex items-center justify-center text-xl ${isUser ? 'bg-secondary/20 text-secondary' : 'bg-primary/20 text-primary'}">
            <i class="bi ${isUser ? 'bi-person-fill' : 'bi-robot'}"></i>
        </div>
    `;

  // Content
  const card = document.createElement('div');
  // User gets gradient, bot gets card style
  card.className = `px-6 py-4 rounded-2xl shadow-md max-w-3xl border border-slate-700 ${isUser ? 'bg-gradient-to-r from-primary to-secondary text-white border-transparent rounded-tr-sm' : 'bg-card text-slate-200 rounded-tl-sm'}`;

  const prose = document.createElement('div');
  prose.className = 'prose';

  if (role === 'assistant') {
    prose.innerHTML = marked.parse(text);
  } else {
    prose.textContent = text;
  }

  card.appendChild(prose);

  msgDiv.appendChild(avatar);
  msgDiv.appendChild(card);

  messagesContainer.appendChild(msgDiv);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;

  // Clear input
  messageInput.value = '';

  // Intercept Logout Command (Regex)
  if (/^\s*(logoff|logout|sign\s?out|exit)\W*\s*$/i.test(message)) {
    appendMessage('user', message);
    setTimeout(() => logout(), 500);
    return;
  }

  // Show user message
  appendMessage('user', message);

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ message })
    });

    if (response.status === 401) {
      logout(); // Session expired
      return;
    }

    if (!response.ok) {
      throw new Error('Network response was not ok');
    }

    const data = await response.json();
    appendMessage('assistant', data.response);

    // Watch for Agent-initiated Logout
    if (/sign(ed)?\s?out|session.*cleared/i.test(data.response)) {
      setTimeout(() => logout(), 1000);
    }
  } catch (error) {
    console.error('Error:', error);
    appendMessage('assistant', 'Sorry, something went wrong. Please try again.');
  }
});

// Check Auth on Load
(async () => {
  try {
    const response = await fetch('/api/me');
    const data = await response.json();
    if (data.username) {
      startApp();
    }
  } catch (e) {
    console.log("Not logged in");
  }
})();
