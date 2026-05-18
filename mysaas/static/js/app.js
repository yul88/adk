let currentAgent = null;

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
  // Check auth
  try {
    const res = await fetch('/api/current_user');
    const data = await res.json();
    if (!data.username) {
      window.location.href = '/index.html';
    } else {
      document.getElementById('currentUser').innerText = `Hello, ${data.username}`;
      switchAgent('checkmate');
    }
  } catch (e) {
    window.location.href = '/index.html';
  }

  // Enter key sends message
  document.getElementById('chatInput').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
  });
});

async function signout() {
  await fetch('/api/signout', { method: 'POST' });
  window.location.href = '/index.html';
}

function switchAgent(agentName) {
  currentAgent = agentName;

  // UI Updates
  document.querySelectorAll('.btn-icon').forEach(b => b.classList.remove('active-agent'));
  document.getElementById(`btn-${agentName}`).classList.add('active-agent');

  document.getElementById('welcomeMessage').classList.add('d-none');
  document.getElementById('welcomePanel').classList.add('d-none');

  document.getElementById('chatInput').disabled = false;
  document.getElementById('sendBtn').disabled = false;
  document.getElementById('chatInput').focus();

  const chatTitle = agentName === 'checkmate' ? 'Checkmate (Checklist Assistant)' : 'Stash (Link Manager)';
  document.getElementById('chatTitle').innerText = chatTitle;

  // Show/Hide Panels
  document.getElementById('checkmatePanel').classList.add('d-none');
  document.getElementById('stashPanel').classList.add('d-none');
  document.getElementById(`${agentName}Panel`).classList.remove('d-none');

  // Load Artifacts
  if (agentName === 'checkmate') {
    document.getElementById('btn-qa-checklist').classList.remove('d-none');
    document.getElementById('btn-qa-stash').classList.add('d-none');
    loadChecklists();
  }
  if (agentName === 'stash') {
    document.getElementById('btn-qa-checklist').classList.add('d-none');
    document.getElementById('btn-qa-stash').classList.remove('d-none');
    loadLinks();
  }
}

// --- Chat ---
async function sendMessage(text = null) {
  const input = document.getElementById('chatInput');
  const msg = text || input.value.trim();
  if (!msg || !currentAgent) return;

  // Add User Bubble
  addBubble(msg, 'user');
  input.value = '';

  try {
    const res = await fetch(`/api/chat/${currentAgent}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg })
    });

    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }

    const data = await res.json();

    // Add Agent Bubble
    addBubble(data.response || "...", 'agent');

    // Check for implicit signout
    if (data.user === null && document.getElementById('currentUser').innerText.includes('Hello')) {
      // User was logged in but now is null
      window.location.href = '/index.html';
      return;
    }

    // Refresh artifacts
    if (currentAgent === 'checkmate') loadChecklists();
    // if (currentAgent === 'stash') loadLinks();

  } catch (e) {
    addBubble('Error: ' + e.message, 'agent');
  }
}

function addBubble(text, type) {
  const history = document.getElementById('chatHistory');
  const div = document.createElement('div');
  div.className = `chat-bubble ${type}`;
  div.innerHTML = marked.parse(text || ""); // Render markdown in chat, safeguard against null
  history.appendChild(div);
  history.scrollTop = history.scrollHeight;
}

// --- Checkmate Logic ---
async function loadChecklists() {
  const res = await fetch('/api/checklists');
  const lists = await res.json();

  const container = document.getElementById('checklistAccordion');
  container.innerHTML = '';

  lists.forEach((list, index) => {
    const tpl = document.getElementById('checklistTemplate');
    const clone = tpl.content.cloneNode(true);

    // IDs
    const itemId = `collapse${index}`;
    const headerId = `heading${index}`;

    clone.querySelector('.accordion-button').setAttribute('data-bs-target', `#${itemId}`);
    clone.querySelector('.accordion-button').setAttribute('aria-controls', itemId);

    clone.querySelector('.accordion-collapse').id = itemId;
    clone.querySelector('.accordion-collapse').setAttribute('aria-labelledby', headerId);

    // Content
    clone.querySelector('.checklist-title').innerText = list.title || 'Untitled Checklist';
    clone.querySelector('.item-count').innerText = `${list.items.length} items`;

    // Delete Button
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'btn btn-sm btn-outline-danger ms-2 stop-propagation';
    deleteBtn.innerHTML = '<i class="bi bi-trash"></i>';
    deleteBtn.onclick = (e) => {
      e.stopPropagation(); // Prevent accordion toggle
      deleteChecklist(list._id);
    };
    clone.querySelector('.accordion-button').appendChild(deleteBtn);

    const itemsContainer = clone.querySelector('.checklist-items');
    list.items.forEach((item, itemIdx) => {
      const wrapper = document.createElement('div');
      wrapper.className = 'form-check';
      // Indentation for nested items
      const level = item.level || 0;
      wrapper.style.marginLeft = `${level * 20}px`;

      const checkbox = document.createElement('input');
      checkbox.className = 'form-check-input';
      checkbox.type = 'checkbox';
      checkbox.checked = item.is_checked;
      checkbox.id = `chk-${list._id}-${itemIdx}`;
      checkbox.onchange = () => toggleItem(list._id, list.items, itemIdx, checkbox.checked);

      const label = document.createElement('label');
      label.className = `form-check-label ${item.is_checked ? 'checked' : ''}`;
      label.htmlFor = checkbox.id;
      label.innerText = item.text;

      wrapper.appendChild(checkbox);
      wrapper.appendChild(label);
      itemsContainer.appendChild(wrapper);
    });

    container.appendChild(clone);
  });
}

async function toggleItem(listId, items, itemIdx, isChecked) {
  items[itemIdx].is_checked = isChecked;

  // Optimistic UI update
  const checkbox = document.getElementById(`chk-${listId}-${itemIdx}`);
  if (checkbox) {
    const label = checkbox.nextElementSibling;
    if (isChecked) label.classList.add('checked');
    else label.classList.remove('checked');
  }

  try {
    await fetch(`/api/checklists/${listId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: items })
    });
  } catch (e) {
    console.error("Failed to update checklist", e);
    // Revert UI if needed
  }
}

// --- Stash Logic ---
async function loadLinks() {
  const res = await fetch('/api/links');
  const links = await res.json();

  const tbody = document.getElementById('linksTableBody');
  tbody.innerHTML = '';

  if (links.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-3">No links stashed yet.</td></tr>';
    return;
  }

  links.forEach(link => {
    const tr = document.createElement('tr');
    const date = new Date(link.created_at).toLocaleDateString();
    const tags = Array.isArray(link.tags) ? link.tags.map(t => `<span class="badge bg-secondary me-1">${t}</span>`).join('') : '';

    tr.innerHTML = `
            <td><a href="${link.url}" target="_blank" class="text-info text-decoration-none">${link.summary || link.url}</a></td>
            <td>${tags}</td>
            <td class="text-end">
                <button class="btn btn-sm btn-link text-danger p-0" onclick="deleteLink('${link._id}')">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        `;
    tbody.appendChild(tr);
  });
}

function filterLinks(query) {
  const rows = document.querySelectorAll('#linksTableBody tr');
  query = query.toLowerCase();
  rows.forEach(row => {
    const text = row.innerText.toLowerCase();
    row.style.display = text.includes(query) ? '' : 'none';
  });
}

// --- Quick Actions ---
// --- Quick Actions (Modal) ---
let currentModalAction = null;

function quickAction(action) {
  currentModalAction = action;
  const modalInput = document.getElementById('inputModalValue');
  const modalPrompt = document.getElementById('inputModalPrompt');
  const modalTitle = document.getElementById('inputModalLabel');

  modalInput.value = ''; // Clear previous input

  if (action === 'new_checklist') {
    modalTitle.innerText = "New Checklist";
    modalPrompt.innerText = "What is this checklist for?";
    modalInput.placeholder = "e.g., Camping Trip, Grocery List";
  } else if (action === 'stash_link') {
    modalTitle.innerText = "Stash Link";
    modalPrompt.innerText = "Paste the URL to stash:";
    modalInput.placeholder = "https://...";
  }

  // Show Modal
  const modalEl = document.getElementById('inputModal');
  const modal = new bootstrap.Modal(modalEl);
  modal.show();

  // Auto focus
  setTimeout(() => modalInput.focus(), 500);
}

function submitModal() {
  const input = document.getElementById('inputModalValue');
  const value = input.value.trim();

  if (!value) return;

  // Hide Modal properly
  const modalEl = document.getElementById('inputModal');
  const modal = bootstrap.Modal.getInstance(modalEl);
  modal.hide();

  if (currentModalAction === 'new_checklist') {
    sendMessage(`Please create a checklist for: ${value}.`);
  } else if (currentModalAction === 'stash_link') {
    sendMessage(`Please stash this link: ${value}`);
  }

  currentModalAction = null;
}

function handleModalKeypress(e) {
  if (e.key === 'Enter') submitModal();
}

// --- Mobile View Switcher ---
function setMobileView(view) {
  // view: 'chat' or 'artifacts'
  if (window.innerWidth >= 768) return; // Ignore on desktop

  const chatCol = document.getElementById('chatColumn') || document.querySelector('.col-md-5');
  const artifactsCol = document.getElementById('artifactsColumn') || document.querySelector('.col-md-6');

  if (view === 'chat') {
    chatCol.classList.remove('d-none');
    artifactsCol.classList.add('d-none');
    document.getElementById('tab-chat').classList.add('active');
    document.getElementById('tab-artifacts').classList.remove('active');
  } else {
    chatCol.classList.add('d-none');
    artifactsCol.classList.remove('d-none');
    document.getElementById('tab-chat').classList.remove('active');
    document.getElementById('tab-artifacts').classList.add('active');
  }
}

// --- Confirmation Modal ---
let confirmCallback = null;

function showConfirm(message, callback) {
  confirmCallback = callback;
  document.getElementById('confirmModalMessage').innerText = message;
  const modal = new bootstrap.Modal(document.getElementById('confirmModal'));
  modal.show();
}

document.addEventListener('DOMContentLoaded', () => {
  // ... existing init ...
  document.getElementById('confirmModalBtn').addEventListener('click', () => {
    if (confirmCallback) confirmCallback();
    const modal = bootstrap.Modal.getInstance(document.getElementById('confirmModal'));
    modal.hide();
  });
});

async function deleteChecklist(id) {
  showConfirm('This action cannot be undone.', async () => {
    try {
      const res = await fetch(`/api/checklists/${id}`, { method: 'DELETE' });
      if (res.ok) loadChecklists();
      else alert('Failed to delete checklist');
    } catch (e) {
      console.error(e);
    }
  });
}

async function deleteLink(id) {
  showConfirm('Delete this stashed link?', async () => {
    try {
      const res = await fetch(`/api/links/${id}`, { method: 'DELETE' });
      if (res.ok) loadLinks();
      else alert('Failed to delete link');
    } catch (e) {
      console.error(e);
    }
  });
}
