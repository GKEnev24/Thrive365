// ── Toast notifications ──────────────────────────────────────────────────────
function showToast(message, type = 'default', duration = 3500) {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✅', error: '❌', warning: '⚠️', default: 'ℹ️' };
  toast.innerHTML = `<span>${icons[type] || icons.default}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-8px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ── Language switcher ────────────────────────────────────────────────────────
function switchLanguage() {
  const form = document.getElementById('lang-form');
  if (form) {
    const input = form.querySelector('input[name="lang"]');
    input.value = input.value === 'en' ? 'bg' : 'en';
    form.submit();
  }
}

// ── Modal management ─────────────────────────────────────────────────────────
function openModal(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeModal(id) {
  const overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.remove('active');
  document.body.style.overflow = '';
}

// Close modal on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('active');
    document.body.style.overflow = '';
  }
});

// ── Task Completion Flow ─────────────────────────────────────────────────────
let currentTaskId = null;
let selectedPhoto = null;
let gpsCoords = null;

function openTaskModal(taskId, taskTitle, taskPoints) {
  currentTaskId = taskId;
  selectedPhoto = null;
  gpsCoords = null;

  // Reset modal state
  document.getElementById('modal-task-title').textContent = taskTitle;
  document.getElementById('modal-task-points').textContent = `+${taskPoints} pts`;
  showModalStep('step-gps');
  resetPhotoUpload();

  openModal('task-modal');
  startGPSCheck();
}

function showModalStep(stepId) {
  ['step-gps', 'step-photo', 'step-verifying', 'step-result'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');
  });
  const el = document.getElementById(stepId);
  if (el) el.classList.remove('hidden');

  // Update step dots
  const dots = document.querySelectorAll('.step-dot');
  const steps = ['step-gps', 'step-photo', 'step-verifying', 'step-result'];
  const idx = steps.indexOf(stepId);
  dots.forEach((dot, i) => dot.classList.toggle('active', i <= idx));
}

function startGPSCheck() {
  const statusEl = document.getElementById('gps-status-text');
  const iconEl = document.getElementById('gps-status-icon');
  const nextBtn = document.getElementById('gps-next-btn');

  statusEl.innerHTML = '<strong>Checking your location…</strong><span>Please allow location access</span>';
  iconEl.textContent = '📍';
  nextBtn.disabled = true;

  if (!navigator.geolocation) {
    statusEl.innerHTML = '<strong>GPS not available</strong><span class="gps-checking">Proceeding without location check</span>';
    iconEl.textContent = '⚠️';
    nextBtn.disabled = false;
    return;
  }

  navigator.geolocation.getCurrentPosition(
    pos => {
      gpsCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      // Burgas bounding box: roughly 42.4–42.7 N, 27.3–27.6 E
      const inBurgas = gpsCoords.lat > 42.0 && gpsCoords.lat < 43.0 &&
                       gpsCoords.lng > 27.0 && gpsCoords.lng < 28.0;
      if (inBurgas) {
        statusEl.innerHTML = '<strong class="gps-ok">Location verified ✓</strong><span>You\'re in Burgas!</span>';
        iconEl.textContent = '✅';
      } else {
        statusEl.innerHTML = '<strong>Location detected</strong><span class="gps-checking">Outside Burgas – continuing anyway</span>';
        iconEl.textContent = '📍';
      }
      nextBtn.disabled = false;
    },
    () => {
      statusEl.innerHTML = '<strong>Location unavailable</strong><span class="gps-checking">Proceeding without GPS check</span>';
      iconEl.textContent = '⚠️';
      nextBtn.disabled = false;
    },
    { timeout: 8000, maximumAge: 60000 }
  );
}

function gpsNext() {
  showModalStep('step-photo');
}

function resetPhotoUpload() {
  selectedPhoto = null;
  const preview = document.getElementById('photo-preview');
  const uploadArea = document.getElementById('upload-area');
  if (preview) preview.classList.add('hidden');
  if (uploadArea) uploadArea.classList.remove('hidden');
  const fileInput = document.getElementById('photo-input');
  if (fileInput) fileInput.value = '';
  const submitBtn = document.getElementById('photo-submit-btn');
  if (submitBtn) submitBtn.disabled = true;
}

function setupPhotoUpload() {
  const fileInput = document.getElementById('photo-input');
  const uploadArea = document.getElementById('upload-area');
  if (!fileInput || !uploadArea) return;

  fileInput.addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) handlePhotoSelected(file);
  });

  uploadArea.addEventListener('dragover', e => {
    e.preventDefault();
    uploadArea.classList.add('drag-over');
  });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('drag-over'));
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) handlePhotoSelected(file);
  });
}

function handlePhotoSelected(file) {
  if (!file.type.startsWith('image/')) {
    showToast('Please select an image file.', 'error');
    return;
  }
  selectedPhoto = file;
  const reader = new FileReader();
  reader.onload = e => {
    const preview = document.getElementById('photo-preview');
    const img = document.getElementById('preview-img');
    const uploadArea = document.getElementById('upload-area');
    if (preview && img) {
      img.src = e.target.result;
      preview.classList.remove('hidden');
      if (uploadArea) uploadArea.classList.add('hidden');
    }
    const submitBtn = document.getElementById('photo-submit-btn');
    if (submitBtn) submitBtn.disabled = false;
  };
  reader.readAsDataURL(file);
}

function removePhoto() {
  resetPhotoUpload();
}

async function submitTaskCompletion() {
  if (!selectedPhoto || !currentTaskId) return;

  showModalStep('step-verifying');

  const formData = new FormData();
  formData.append('photo', selectedPhoto);
  if (gpsCoords) {
    formData.append('lat', gpsCoords.lat);
    formData.append('lng', gpsCoords.lng);
  }

  try {
    const resp = await fetch(`/tasks/complete/${currentTaskId}`, {
      method: 'POST',
      body: formData
    });
    const data = await resp.json();

    showModalStep('step-result');

    if (data.success && data.verified) {
      document.getElementById('result-icon').textContent = '🎉';
      document.getElementById('result-title').textContent = 'Task Verified!';
      document.getElementById('result-subtitle').textContent = data.feedback;

      const pointsEl = document.getElementById('result-points');
      pointsEl.classList.remove('hidden');
      pointsEl.querySelector('.points-value').textContent = `+${data.points_awarded} pts`;

      if (data.new_badges && data.new_badges.length > 0) {
        const badgesEl = document.getElementById('new-badges');
        badgesEl.classList.remove('hidden');
        badgesEl.innerHTML = data.new_badges.map(b =>
          `<div class="new-badge-chip">${b.icon} ${b.name}</div>`
        ).join('');
      }

      // Update header points
      updatePointsDisplay(data.total_points);

      // Mark task card as completed
      const taskCard = document.querySelector(`[data-task-id="${currentTaskId}"]`);
      if (taskCard) {
        taskCard.classList.add('task-completed');
        const btn = taskCard.querySelector('.complete-btn');
        if (btn) {
          btn.outerHTML = `<div class="task-done-badge"><span>✅</span><span>Completed</span></div>`;
        }
        updateTasksProgress();
      }

    } else {
      document.getElementById('result-icon').textContent = '📸';
      document.getElementById('result-title').textContent = 'Try Again';
      document.getElementById('result-subtitle').textContent =
        data.feedback || data.message || 'Please upload a photo that shows your eco-action more clearly.';
      document.getElementById('result-points').classList.add('hidden');
      document.getElementById('new-badges').classList.add('hidden');
    }

  } catch (err) {
    showModalStep('step-result');
    document.getElementById('result-icon').textContent = '❌';
    document.getElementById('result-title').textContent = 'Upload Error';
    document.getElementById('result-subtitle').textContent = 'Something went wrong. Please try again.';
    document.getElementById('result-points').classList.add('hidden');
    document.getElementById('new-badges').classList.add('hidden');
  }
}

function updatePointsDisplay(newPoints) {
  const badges = document.querySelectorAll('.points-badge span:last-child');
  badges.forEach(b => {
    const start = parseInt(b.textContent) || 0;
    animateNumber(b, start, newPoints, 600);
  });
}

function animateNumber(el, from, to, duration) {
  const start = performance.now();
  function tick(now) {
    const t = Math.min((now - start) / duration, 1);
    const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    el.textContent = Math.round(from + (to - from) * ease);
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function updateTasksProgress() {
  const total = document.querySelectorAll('.task-card').length;
  const done = document.querySelectorAll('.task-card.task-completed').length;
  const progressFill = document.querySelector('.progress-fill');
  const progressText = document.querySelector('.tasks-progress-text');
  if (progressFill) progressFill.style.width = `${(done / total) * 100}%`;
  if (progressText) progressText.textContent = `${done} / ${total} completed`;
}

function closeTaskResult() {
  closeModal('task-modal');
}

// ── Prize Redemption ─────────────────────────────────────────────────────────
async function redeemPrize(prizeId, prizeTitle) {
  const btn = document.querySelector(`[data-prize-id="${prizeId}"] .prize-redeem-btn`);
  if (btn) { btn.disabled = true; btn.textContent = 'Processing…'; }

  try {
    const resp = await fetch(`/shop/redeem/${prizeId}`, { method: 'POST' });
    const data = await resp.json();

    if (data.success) {
      // Show code modal
      document.getElementById('code-prize-title').textContent = data.prize_title;
      document.getElementById('code-value').textContent = data.code;
      openModal('code-modal');

      // Update user points
      updatePointsDisplay(data.remaining_points);

      // Update balance display
      const balanceEl = document.querySelector('.shop-balance-points');
      if (balanceEl) balanceEl.textContent = data.remaining_points;

      // Change button
      if (btn) btn.outerHTML = `<button class="prize-redeemed-btn">✓ Redeemed</button>`;
    } else {
      showToast(data.message || 'Redemption failed.', 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Redeem'; }
    }
  } catch (err) {
    showToast('Something went wrong. Please try again.', 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'Redeem'; }
  }
}

function copyCode() {
  const code = document.getElementById('code-value').textContent;
  navigator.clipboard.writeText(code).then(() => {
    showToast('Code copied to clipboard!', 'success');
  });
}

// ── Admin tabs ───────────────────────────────────────────────────────────────
function switchAdminTab(tabName) {
  document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.admin-panel').forEach(p => p.classList.add('hidden'));
  document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
  document.getElementById(`panel-${tabName}`).classList.remove('hidden');
}

function editTask(id, titleEn, titleBg, descEn, descBg, points, location, lat, lng, taskDate) {
  document.getElementById('edit-task-id').value = id;
  document.getElementById('edit-title-en').value = titleEn;
  document.getElementById('edit-title-bg').value = titleBg;
  document.getElementById('edit-desc-en').value = descEn;
  document.getElementById('edit-desc-bg').value = descBg;
  document.getElementById('edit-points').value = points;
  document.getElementById('edit-location').value = location;
  document.getElementById('edit-lat').value = lat;
  document.getElementById('edit-lng').value = lng;
  document.getElementById('edit-date').value = taskDate;
  openModal('edit-task-modal');
}

function deleteTask(id) {
  if (!confirm('Delete this task? This cannot be undone.')) return;
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = `/admin/task/${id}/delete`;
  document.body.appendChild(form);
  form.submit();
}

// ── Flash auto-dismiss ───────────────────────────────────────────────────────
document.querySelectorAll('.flash').forEach(flash => {
  setTimeout(() => {
    flash.style.opacity = '0';
    flash.style.transition = 'opacity 0.5s';
    setTimeout(() => flash.remove(), 500);
  }, 4000);
});

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  setupPhotoUpload();
});
