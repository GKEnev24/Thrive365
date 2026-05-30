// ── ThriveAI chat (shared by the floating widget and the full /thriveai page) ──
// The widget and the page reuse the same element IDs; only one set exists per page.

const ThriveAI = (() => {
  const LANG = window.THRIVEAI_LANG || 'en';
  const NAME = window.THRIVEAI_NAME || '';
  const STORAGE_KEY = 'thriveai_history';
  let history = loadHistory();
  let busy = false;
  let greeted = false;

  const t = (en, bg) => (LANG === 'bg' ? bg : en);

  function loadHistory() {
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)) || []; }
    catch { return []; }
  }
  function saveHistory() {
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-24))); }
    catch { /* ignore quota */ }
  }

  // ── DOM helpers ──
  const $ = id => document.getElementById(id);

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // Light, safe formatting: escape first, then allow **bold**, • bullets, newlines.
  function format(text) {
    let html = escapeHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  function appendMessage(role, text) {
    const box = $('thriveai-messages');
    if (!box) return null;
    const row = document.createElement('div');
    row.className = `thriveai-msg thriveai-msg-${role}`;
    if (role === 'ai') {
      row.innerHTML = `<span class="thriveai-msg-avatar">🌱</span>
                       <div class="thriveai-bubble">${format(text)}</div>`;
    } else {
      row.innerHTML = `<div class="thriveai-bubble">${format(text)}</div>`;
    }
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    return row;
  }

  function showTyping() {
    const box = $('thriveai-messages');
    if (!box) return null;
    const row = document.createElement('div');
    row.className = 'thriveai-msg thriveai-msg-ai';
    row.id = 'thriveai-typing';
    row.innerHTML = `<span class="thriveai-msg-avatar">🌱</span>
      <div class="thriveai-bubble thriveai-typing">
        <span></span><span></span><span></span>
      </div>`;
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
    return row;
  }

  function greeting() {
    const name = NAME ? (LANG === 'bg' ? `, ${NAME}` : ` ${NAME}`) : '';
    return t(
      `Hi${name}! 🌱 I'm ThriveAI, your guide to a greener Burgas. Ask me about today's tasks, your points, rewards, or eco-tips!`,
      `Здравей${name}! 🌱 Аз съм ThriveAI, твоят помощник за по-зелен Бургас. Питай ме за днешните задачи, точки, награди или еко-съвети!`
    );
  }

  // Render any saved history into the current view (and greet if empty).
  function hydrate() {
    const box = $('thriveai-messages');
    if (!box || greeted) return;
    greeted = true;
    box.innerHTML = '';
    if (history.length === 0) {
      appendMessage('ai', greeting());
    } else {
      history.forEach(m => appendMessage(m.role === 'assistant' ? 'ai' : 'user', m.content));
    }
  }

  // ── Public actions ──
  function toggle() {
    const widget = $('thriveai-widget');
    const fab = $('thriveai-fab');
    if (!widget) return;
    const open = widget.classList.toggle('open');
    widget.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (fab) fab.classList.toggle('hidden', open);
    if (open) {
      hydrate();
      setTimeout(() => $('thriveai-input') && $('thriveai-input').focus(), 250);
    }
  }

  function suggest(el) {
    const input = $('thriveai-input');
    if (input) input.value = el.textContent.trim();
    submit();
  }

  function send(event) {
    if (event) event.preventDefault();
    submit();
    return false;
  }

  async function submit() {
    const input = $('thriveai-input');
    if (!input || busy) return;
    const message = input.value.trim();
    if (!message) return;

    const suggestions = $('thriveai-suggestions');
    if (suggestions) suggestions.style.display = 'none';

    busy = true;
    input.value = '';
    appendMessage('user', message);
    history.push({ role: 'user', content: message });
    saveHistory();
    showTyping();

    try {
      const resp = await fetch('/api/thriveai/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, history: history.slice(0, -1) })
      });
      const data = await resp.json();
      const typing = $('thriveai-typing');
      if (typing) typing.remove();

      if (data.success) {
        appendMessage('ai', data.reply);
        history.push({ role: 'assistant', content: data.reply });
        saveHistory();
      } else {
        appendMessage('ai', t('Sorry, something went wrong. Please try again.',
                              'Съжалявам, нещо се обърка. Опитай отново.'));
      }
    } catch (err) {
      const typing = $('thriveai-typing');
      if (typing) typing.remove();
      appendMessage('ai', t('I could not reach the server. Check your connection and try again.',
                            'Не успях да се свържа със сървъра. Провери връзката и опитай отново.'));
    } finally {
      busy = false;
      if (input) input.focus();
    }
  }

  // On the full page, render immediately.
  document.addEventListener('DOMContentLoaded', () => {
    const box = $('thriveai-messages');
    if (box && box.classList.contains('thriveai-messages-page')) {
      hydrate();
    }
  });

  return { toggle, send, suggest };
})();
