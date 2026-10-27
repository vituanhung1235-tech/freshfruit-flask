const toggle = document.getElementById('chat-toggle');
const chat = document.getElementById('chatbox');
const close = document.getElementById('chat-close');
const messages = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const chatText = document.getElementById('chat-text');
let history = [];

toggle.addEventListener('click', () => {
    chat.hidden = !chat.hidden;
    if (!chat.hidden) chatText.focus();
});
close.addEventListener('click', () => chat.hidden = true);

function formatMarkdown(text) {
    if (!text) return '';
    const temp = document.createElement('div');
    temp.textContent = text;
    let safe = temp.innerHTML;
    safe = safe.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    safe = safe.replace(/\*(.*?)\*/g, '<em>$1</em>');
    safe = safe.replace(/\n/g, '<br>');
    return safe;
}

function addMessage(content, type) {
    const div = document.createElement('div');
    div.className = 'bubble ' + type;
    div.innerHTML = formatMarkdown(content);
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
}

form.addEventListener('submit', async ev => {
    ev.preventDefault();
    const message = chatText.value.trim();
    if (!message) return;
    chatText.value = '';
    addMessage(message, 'user');
    const pending = addMessage('Đang suy nghĩ...', 'bot');
    const sendBtn = form.querySelector('button');
    sendBtn.disabled = true;
    try {
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const csrfToken = csrfMeta ? csrfMeta.content : '';
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken
            },
            body: JSON.stringify({ message, history })
        });
        const data = await res.json();
        const replyText = data.reply || data.error || 'Có lỗi xảy ra.';
        pending.innerHTML = formatMarkdown(replyText);
        if (res.ok) {
            history.push({ role: 'user', content: message }, { role: 'assistant', content: replyText });
            history = history.slice(-8);
        }
    } catch (e) {
        pending.innerHTML = 'Không kết nối được chatbot. Vui lòng thử lại.';
    } finally {
        sendBtn.disabled = false;
        messages.scrollTop = messages.scrollHeight;
    }
});
