// Basic local storage helpers
const getLS = (k, d) => {
  try { const v = localStorage.getItem(k); return v ?? d; } catch { return d; }
};
const setLS = (k, v) => { try { localStorage.setItem(k, v); } catch {} };

// Elements
const chatBaseEl = document.getElementById('chatBase');
const saveChatBaseBtn = document.getElementById('saveChatBase');
const tasksBaseEl = document.getElementById('tasksBase');
const saveTasksBaseBtn = document.getElementById('saveTasksBase');

const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const chatSend = document.getElementById('chatSend');
const chatScroll = document.getElementById('chatScroll');
const chatConversation = document.getElementById('chatConversation');
const resetConversationBtn = document.getElementById('resetConversation');

const taskForm = document.getElementById('taskForm');
const taskInput = document.getElementById('taskInput');
const taskLog = document.getElementById('taskLog');
const todoList = document.getElementById('todoList');
const activeTaskIdEl = document.getElementById('activeTaskId');

// State
const sameOriginDefault = (() => {
  try {
    const o = window.location.origin || '';
    if (o && o.startsWith('http')) return o;
  } catch {}
  return 'http://localhost:8000';
})();

let CHAT_BASE = getLS('chatBase', sameOriginDefault);
let TASKS_BASE = getLS('tasksBase', sameOriginDefault);
let conversationId = null;
let previousResponseId = null;
let taskEventSource = null;
let currentTaskId = null;

// Initialize inputs
chatBaseEl.value = CHAT_BASE;
tasksBaseEl.value = TASKS_BASE;

saveChatBaseBtn.addEventListener('click', () => {
  CHAT_BASE = chatBaseEl.value.trim() || 'http://localhost:8000';
  setLS('chatBase', CHAT_BASE);
  toast('Saved chat base');
});
saveTasksBaseBtn.addEventListener('click', () => {
  TASKS_BASE = tasksBaseEl.value.trim() || 'http://localhost:8000';
  setLS('tasksBase', TASKS_BASE);
  toast('Saved tasks base');
  ensureTaskEvents();
});

resetConversationBtn.addEventListener('click', () => {
  conversationId = null;
  previousResponseId = null;
  chatConversation.textContent = '(none)';
  toast('Conversation reset');
});

// UI helpers
function toast(msg) {
  const el = document.createElement('div');
  el.className = 'fixed bottom-4 right-4 bg-black/80 text-white text-sm px-3 py-2 rounded';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1600);
}

function addMessage(role, text, isStreaming=false) {
  const row = document.createElement('div');
  row.className = 'flex ' + (role === 'assistant' ? '' : 'justify-end');
  const bubble = document.createElement('div');
  bubble.className = 'bubble rounded-2xl px-4 py-3 shadow ' + (role === 'assistant' ? 'bg-slate-100' : 'bg-indigo-600 text-white');
  bubble.innerText = text || '';
  if (isStreaming) bubble.dataset.streaming = '1';
  row.appendChild(bubble);
  chatScroll.appendChild(row);
  chatScroll.scrollTop = chatScroll.scrollHeight;
  return bubble;
}

function appendToStreamingBubble(bubble, delta) {
  bubble.innerText += delta;
  chatScroll.scrollTop = chatScroll.scrollHeight;
}

// SSE parser for fetch streams (supports POST)
function createSSEParser(onEvent) {
  let buffer = '';
  let eventName = 'message';
  let dataLines = [];
  return (chunk) => {
    buffer += chunk;
    const parts = buffer.split(/\r?\n/);
    buffer = parts.pop() ?? '';
    for (const line of parts) {
      if (line === '') {
        if (dataLines.length) {
          const data = dataLines.join('\n');
          onEvent({ event: eventName, data });
        }
        eventName = 'message';
        dataLines = [];
        continue;
      }
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5));
    }
  };
}

async function postSSE(url, body, handlers) {
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok || !res.body) throw new Error('Bad response');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSSEParser(({ event, data }) => {
    if (handlers.onEvent) handlers.onEvent(event, data);
  });
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    parse(decoder.decode(value, { stream: true }));
  }
  if (handlers.onDone) handlers.onDone();
}

// Chat behavior
chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = '';

  // Show user message
  addMessage('user', text);
  // Create assistant placeholder for streaming
  const bubble = addMessage('assistant', '', true);

  chatSend.disabled = true;
  try {
    await postSSE(`${CHAT_BASE}/`, { items: text, previousResponseId, conversationId }, {
      onEvent: (event, data) => {
        if (event === 'done') return; // ignore here
        try {
          const obj = JSON.parse(data);
          if (event === 'raw_response_event') {
            const t = obj.type;
            if (t === 'response.created') {
              const conv = obj.response && obj.response.conversation;
              const cid = conv && conv.id;
              if (cid) {
                conversationId = cid;
                chatConversation.textContent = cid;
              }
            } else if (t === 'response.output_text.delta') {
              appendToStreamingBubble(bubble, obj.delta || '');
            } else if (t === 'response.output_text.done') {
              bubble.dataset.streaming = '0';
            } else if (t === 'response.completed') {
              const rid = obj.response && obj.response.id;
              if (rid) previousResponseId = rid;
            } else if (t === 'synthesized.message') {
              // Friendly tool result line
              const line = obj.text || '';
              addMessage('assistant', line);
            }
          }
        } catch (err) {
          // Non-JSON or parse issue; ignore quietly
        }
      },
      onDone: () => {
        chatSend.disabled = false;
      }
    });
  } catch (err) {
    chatSend.disabled = false;
    addMessage('assistant', 'Failed to reach chat server.');
  }
});

// Tasks behavior
function ensureTaskEvents() {
  if (taskEventSource) {
    taskEventSource.close();
    taskEventSource = null;
  }
  try {
    taskEventSource = new EventSource(`${TASKS_BASE}/events`);
  } catch (e) {
    // ignore for now
    return;
  }

  taskEventSource.addEventListener('task.created', (ev) => {
    // Useful general info; show on log if it matches active task
    try {
      const data = JSON.parse(ev.data || '{}');
      logTask(`task.created: ${JSON.stringify(data)}`);
    } catch {}
  });

  taskEventSource.addEventListener('task.updated', (ev) => {
    try {
      const data = JSON.parse(ev.data || '{}');
      const tid = data.task_id;
      if (!currentTaskId || tid !== currentTaskId) return; // filter to active
      if (data.status === 'done') {
        logTask('Task finished.');
        return;
      }
      const eobj = data.event || {};
      const type = eobj.type;

      if (type === 'synthesized.message') {
        logTask(eobj.text || '');
      } else if (type === 'response.output_text.delta') {
        logTask((eobj.delta || ''), false);
      } else if (type === 'response.output_text.done') {
        logTask('\n');
      } else if (type === 'todo.added') {
        upsertTodo(eobj.todo);
      } else if (type === 'todo.status') {
        updateTodoStatus(eobj.todoId, eobj.status);
      } else if (type === 'function.tool_result') {
        // Pretty-print brief note
        const name = eobj.name || 'tool';
        logTask(`[${name}] result`);
      }
    } catch {}
  });
}

function logTask(text, newLine=true) {
  if (!text) return;
  const span = document.createElement('span');
  span.textContent = text;
  taskLog.appendChild(span);
  if (newLine) taskLog.appendChild(document.createElement('br'));
  taskLog.scrollTop = taskLog.scrollHeight;
}

function upsertTodo(todo) {
  if (!todo || !todo.id) return;
  let li = document.querySelector(`[data-todo-id="${todo.id}"]`);
  if (!li) {
    li = document.createElement('li');
    li.dataset.todoId = todo.id;
    li.className = 'border rounded-lg px-3 py-2 flex items-center justify-between';
    li.innerHTML = `<span class="truncate">${escapeHtml(todo.text || '')}</span><span class="text-xs rounded px-2 py-1 bg-slate-100" data-role="status"></span>`;
    todoList.appendChild(li);
  }
  const statusEl = li.querySelector('[data-role="status"]');
  statusEl.textContent = (todo.status || 'idle');
  statusEl.className = `text-xs rounded px-2 py-1 ${badgeClass(todo.status)}`;
}

function updateTodoStatus(id, status) {
  const li = document.querySelector(`[data-todo-id="${id}"]`);
  if (!li) return;
  const statusEl = li.querySelector('[data-role="status"]');
  statusEl.textContent = status;
  statusEl.className = `text-xs rounded px-2 py-1 ${badgeClass(status)}`;
}

function badgeClass(s) {
  switch (s) {
    case 'running': return 'bg-amber-100 text-amber-900';
    case 'done': return 'bg-emerald-100 text-emerald-900';
    default: return 'bg-slate-100 text-slate-900';
  }
}

function escapeHtml(str) {
  return (str || '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

taskForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = taskInput.value.trim();
  if (!text) return;
  taskInput.value = '';
  logTask('Creating task…');
  try {
    const res = await fetch(`${TASKS_BASE}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: text })
    });
    const json = await res.json();
    currentTaskId = json.task_id;
    activeTaskIdEl.textContent = currentTaskId;
    // Clear UI for new run
    taskLog.innerHTML = '';
    todoList.innerHTML = '';
    logTask(`Task ${currentTaskId} started…`);
    ensureTaskEvents();
  } catch (err) {
    logTask('Failed to create task.');
  }
});

// Start listening to tasks events by default
ensureTaskEvents();
