// ==========================================
// TUẤN HÙNG FRUIT - FRONTEND JAVASCRIPT
// ==========================================

// 1. HERO BANNER SLIDER
(function initSlider() {
  const slider = document.getElementById('tuanhung-slider');
  if (!slider) return;

  const slides = slider.querySelectorAll('.slide-item');
  if (!slides.length) return;
  const dots = slider.querySelectorAll('.dot');
  const prevBtn = document.getElementById('slider-prev');
  const nextBtn = document.getElementById('slider-next');
  let currentIndex = 0;
  let timer = null;
  const slideDuration = 4500; // 4.5s

  function showSlide(index) {
    if (index >= slides.length) index = 0;
    if (index < 0) index = slides.length - 1;
    currentIndex = index;

    slides.forEach((s, idx) => {
      s.classList.toggle('active', idx === currentIndex);
    });

    dots.forEach((d, idx) => {
      d.classList.toggle('active', idx === currentIndex);
    });
  }

  function nextSlide() {
    showSlide(currentIndex + 1);
  }

  function prevSlide() {
    showSlide(currentIndex - 1);
  }

  function startTimer() {
    stopTimer();
    timer = setInterval(nextSlide, slideDuration);
  }

  function stopTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', () => {
      nextSlide();
      startTimer();
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', () => {
      prevSlide();
      startTimer();
    });
  }

  dots.forEach(dot => {
    dot.addEventListener('click', () => {
      const idx = parseInt(dot.getAttribute('data-go') || '0', 10);
      showSlide(idx);
      startTimer();
    });
  });

  // Pause on hover
  slider.addEventListener('mouseenter', stopTimer);
  slider.addEventListener('mouseleave', startTimer);

  // Touch swipe support for mobile
  let touchStartX = 0;
  slider.addEventListener('touchstart', e => {
    touchStartX = e.changedTouches[0].screenX;
    stopTimer();
  }, { passive: true });

  slider.addEventListener('touchend', e => {
    const touchEndX = e.changedTouches[0].screenX;
    const diff = touchEndX - touchStartX;
    if (Math.abs(diff) > 40) {
      if (diff < 0) nextSlide();
      else prevSlide();
    }
    startTimer();
  }, { passive: true });

  // Start auto play
  startTimer();
})();

// Recalculate and persist all cart quantities together.
(function initCartAutoUpdate() {
  const form = document.getElementById('cart-quantity-form');
  if (!form) return;

  const formatter = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 });
  const subtotal = document.getElementById('cart-subtotal');
  const status = document.getElementById('cart-save-status');
  let submitTimer = null;

  function recalculate() {
    let total = 0;
    form.querySelectorAll('.cart-row').forEach(row => {
      const input = row.querySelector('[data-cart-quantity]');
      const quantity = Math.max(0, Math.floor(Number(input.value) || 0));
      const lineTotal = Number(row.dataset.unitPrice) * quantity;
      row.querySelector('.cart-line-total').textContent = formatter.format(lineTotal) + 'đ';
      total += lineTotal;
    });
    subtotal.textContent = formatter.format(total) + 'đ';
  }

  form.addEventListener('input', event => {
    if (!event.target.matches('[data-cart-quantity]')) return;
    recalculate();
    status.textContent = 'Đang chờ lưu thay đổi...';
    clearTimeout(submitTimer);
    submitTimer = setTimeout(() => {
      status.textContent = 'Đang lưu giỏ hàng...';
      form.requestSubmit();
    }, 650);
  });
})();

// 2. CHATBOT TUẤN HÙNG AI
const toggle = document.getElementById('chat-toggle');
const chat = document.getElementById('chatbox');
const close = document.getElementById('chat-close');
const messages = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const chatText = document.getElementById('chat-text');
let history = [];

if (toggle && chat) {
  toggle.addEventListener('click', () => {
    chat.hidden = !chat.hidden;
    if (!chat.hidden && chatText) chatText.focus();
  });
}

if (close && chat) {
  close.addEventListener('click', () => {
    chat.hidden = true;
  });
}

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
  if (!messages) return null;
  const div = document.createElement('div');
  div.className = 'bubble ' + type;
  div.innerHTML = formatMarkdown(content);
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

async function handleSendMessage(message) {
  if (!message || !form) return;
  addMessage(message, 'user');
  const pending = addMessage('Đang kết nối Tuấn Hùng AI...', 'bot');
  const sendBtn = form.querySelector('button');
  if (sendBtn) sendBtn.disabled = true;

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
    if (pending) pending.innerHTML = formatMarkdown(replyText);
    if (res.ok) {
      history.push({ role: 'user', content: message }, { role: 'assistant', content: replyText });
      history = history.slice(-8);
    }
  } catch (e) {
    if (pending) pending.innerHTML = 'Không kết nối được trợ lý. Vui lòng thử lại sau.';
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    if (messages) messages.scrollTop = messages.scrollHeight;
  }
}

if (form) {
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    if (!chatText) return;
    const message = chatText.value.trim();
    if (!message) return;
    chatText.value = '';
    await handleSendMessage(message);
  });
}

// Global quick chip helper
window.sendQuickQuery = function(text) {
  if (chat && chat.hidden) {
    chat.hidden = false;
  }
  handleSendMessage(text);
};
