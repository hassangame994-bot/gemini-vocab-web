'use strict';

const state = { rows: [], toastTimer: null };
const $ = (id) => document.getElementById(id);

const ui = {
  apiKey: $('apiKey'), toggleKey: $('toggleKey'), skipCommon: $('skipCommon'),
  imageFiles: $('imageFiles'), selectedFiles: $('selectedFiles'), dropzone: $('dropzone'),
  tabImages: $('tabImages'), tabManual: $('tabManual'), imageSource: $('imageSource'), manualSource: $('manualSource'),
  wordsInput: $('wordsInput'), extractButton: $('extractButton'), addWordsButton: $('addWordsButton'),
  cardsPanel: $('cardsPanel'), mediaPanel: $('mediaPanel'), exportPanel: $('exportPanel'),
  wordCount: $('wordCount'), rowsContainer: $('rowsContainer'), detailsButton: $('detailsButton'), clearButton: $('clearButton'),
  extraWord: $('extraWord'), extraWordButton: $('extraWordButton'), mediaButton: $('mediaButton'), accent: $('accent'),
  mediaProgressWrap: $('mediaProgressWrap'), mediaProgressText: $('mediaProgressText'), mediaProgressNumber: $('mediaProgressNumber'), mediaProgressBar: $('mediaProgressBar'),
  deckName: $('deckName'), exportButton: $('exportButton'), csvButton: $('csvButton'), toast: $('toast'),
};

function showToast(message, isError = false, duration = 4300) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle('error', isError);
  ui.toast.classList.add('show');
  state.toastTimer = setTimeout(() => ui.toast.classList.remove('show'), duration);
}

function setBusy(button, busy, busyText = 'جاري التنفيذ...') {
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new Error('تعذّر الاتصال بالخادم. تأكد أن الموقع يعمل ثم جرّب مرة أخرى.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || data.message || `حدث خطأ (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function getApiKey() {
  const key = ui.apiKey.value.trim();
  if (!key) {
    ui.apiKey.focus();
    throw new Error('أدخل مفتاح Gemini API أولًا.');
  }
  return key;
}

function cleanWordList(text) {
  const seen = new Set();
  const words = [];
  for (const part of text.split(/[\n,;،؛]+/)) {
    const word = part.replace(/\s+/g, ' ').trim();
    const key = word.toLocaleLowerCase();
    if (word && /[A-Za-z]/.test(word) && !seen.has(key)) {
      seen.add(key);
      words.push(word);
    }
  }
  return words.slice(0, 80);
}

function emptyRow(word) {
  return {
    word, level: '', meaning: '', example: '', image_query: word, form: '',
    image_data: '', audio_data: '', image_source: '', image_title: '', image_error: '', audio_error: '',
  };
}

function mergeRows(newRows, keepExistingMedia = false) {
  const existing = new Map(state.rows.map((row) => [String(row.word).toLowerCase(), row]));
  state.rows = newRows.map((row) => {
    const old = existing.get(String(row.word || '').toLowerCase());
    if (keepExistingMedia && old) {
      return { ...row, image_data: old.image_data || '', audio_data: old.audio_data || '', image_source: old.image_source || '', image_title: old.image_title || '', image_error: old.image_error || '', audio_error: old.audio_error || '' };
    }
    return { ...emptyRow(String(row.word || '').trim()), ...row };
  }).slice(0, 80);
  renderRows();
  revealWorkPanels();
}

function revealWorkPanels() {
  const hasRows = state.rows.length > 0;
  ui.cardsPanel.classList.toggle('hidden', !hasRows);
  ui.mediaPanel.classList.toggle('hidden', !hasRows);
  ui.exportPanel.classList.toggle('hidden', !hasRows);
  if (hasRows) {
    ui.cardsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function countLabel(n) {
  return `${n} ${n === 1 ? 'كلمة' : 'كلمات'}`;
}

function fieldElement(labelText, key, value, multiline = false, direction = 'auto') {
  const label = document.createElement('label');
  const span = document.createElement('span');
  span.textContent = labelText;
  const field = document.createElement(multiline ? 'textarea' : 'input');
  if (!multiline) field.type = 'text';
  field.value = value || '';
  field.dataset.field = key;
  field.dataset.key = key;
  field.dir = direction;
  field.setAttribute('aria-label', labelText);
  if (key === 'example') field.rows = 2;
  if (key === 'meaning') field.rows = 2;
  label.append(span, field);
  return label;
}

function renderRows() {
  ui.rowsContainer.replaceChildren();
  ui.wordCount.textContent = countLabel(state.rows.length);

  state.rows.forEach((row, index) => {
    const card = document.createElement('article');
    card.className = 'word-card';
    card.dataset.index = String(index);

    const head = document.createElement('div');
    head.className = 'word-card-head';
    const title = document.createElement('div');
    title.className = 'word-card-title';
    const number = document.createElement('span');
    number.className = 'word-index';
    number.textContent = String(index + 1).padStart(2, '0');
    const word = document.createElement('input');
    word.type = 'text';
    word.value = row.word || '';
    word.dataset.field = 'word';
    word.dir = 'ltr';
    word.autocomplete = 'off';
    word.setAttribute('aria-label', 'English word');
    title.append(number, word);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'remove-row';
    remove.textContent = '×';
    remove.title = 'حذف الكلمة';
    remove.dataset.action = 'remove';
    remove.dataset.index = String(index);
    head.append(title, remove);

    const fields = document.createElement('div');
    fields.className = 'row-fields';
    fields.append(
      fieldElement('مستوى الكلمة', 'level', row.level, false, 'ltr'),
      fieldElement('المعنى بالإنجليزية', 'meaning', row.meaning, true, 'auto'),
      fieldElement('جملة المثال', 'example', row.example, true, 'ltr'),
      fieldElement('بحث الصورة', 'image_query', row.image_query, false, 'ltr'),
    );

    const media = document.createElement('div');
    media.className = 'row-media';
    const thumb = document.createElement('div');
    thumb.className = 'thumb-wrap';
    if (row.image_data) {
      const image = document.createElement('img');
      image.src = row.image_data;
      image.alt = `Image for ${row.word || 'vocabulary word'}`;
      image.loading = 'lazy';
      thumb.append(image);
    } else {
      thumb.textContent = 'لا توجد صورة';
    }
    const statuses = document.createElement('div');
    statuses.className = 'media-statuses';
    const imageStatus = document.createElement('span');
    imageStatus.textContent = row.image_data ? '✓ تم تجهيز الصورة' : (row.image_error || 'الصورة لم تُجهّز بعد');
    imageStatus.className = row.image_data ? 'media-ok' : (row.image_error ? 'media-fail' : '');
    const audioStatus = document.createElement('span');
    audioStatus.textContent = row.audio_data ? '✓ تم تجهيز النطق' : (row.audio_error || 'النطق لم يُجهّز بعد');
    audioStatus.className = row.audio_data ? 'media-ok' : (row.audio_error ? 'media-fail' : '');
    statuses.append(imageStatus, audioStatus);
    media.append(thumb, statuses);
    if (row.audio_data) {
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'none';
      audio.src = row.audio_data;
      audio.setAttribute('aria-label', `Pronunciation of ${row.word}`);
      audio.style.width = 'min(190px, 44%)';
      audio.style.height = '34px';
      media.append(audio);
    }

    card.append(head, fields, media);
    ui.rowsContainer.append(card);
  });
}

function revealFilePanel(tab) {
  const imagesActive = tab === 'images';
  ui.tabImages.classList.toggle('active', imagesActive);
  ui.tabManual.classList.toggle('active', !imagesActive);
  ui.tabImages.setAttribute('aria-selected', String(imagesActive));
  ui.tabManual.setAttribute('aria-selected', String(!imagesActive));
  ui.imageSource.classList.toggle('hidden', !imagesActive);
  ui.manualSource.classList.toggle('hidden', imagesActive);
}

ui.toggleKey.addEventListener('click', () => {
  const nowText = ui.apiKey.type === 'password';
  ui.apiKey.type = nowText ? 'text' : 'password';
  ui.toggleKey.textContent = nowText ? 'إخفاء' : 'إظهار';
});
ui.tabImages.addEventListener('click', () => revealFilePanel('images'));
ui.tabManual.addEventListener('click', () => revealFilePanel('manual'));

ui.imageFiles.addEventListener('change', () => {
  const files = Array.from(ui.imageFiles.files || []);
  ui.selectedFiles.textContent = files.length ? `${files.length} ملف: ${files.map((f) => f.name).join('، ')}` : 'لم يتم اختيار صور بعد';
});

ui.dropzone.addEventListener('dragover', (event) => { event.preventDefault(); ui.dropzone.classList.add('dragging'); });
ui.dropzone.addEventListener('dragleave', () => ui.dropzone.classList.remove('dragging'));
ui.dropzone.addEventListener('drop', (event) => {
  event.preventDefault();
  ui.dropzone.classList.remove('dragging');
  const files = Array.from(event.dataTransfer?.files || []).filter((f) => f.type.startsWith('image/'));
  if (files.length) {
    const transfer = new DataTransfer();
    files.slice(0, 8).forEach((file) => transfer.items.add(file));
    ui.imageFiles.files = transfer.files;
    ui.imageFiles.dispatchEvent(new Event('change'));
  }
});

ui.extractButton.addEventListener('click', async () => {
  try {
    const key = getApiKey();
    const files = Array.from(ui.imageFiles.files || []);
    if (!files.length) throw new Error('اختر صورة واحدة على الأقل أولًا.');
    if (files.reduce((sum, file) => sum + file.size, 0) > 8 * 1024 * 1024) throw new Error('حجم الصور الكلي أكبر من 8 ميجابايت.');
    const form = new FormData();
    form.append('api_key', key);
    form.append('skip_common', ui.skipCommon.checked ? 'true' : 'false');
    files.forEach((file) => form.append('files', file));
    setBusy(ui.extractButton, true, 'Gemini يقرأ الصور...');
    const result = await requestJson('/api/extract', { method: 'POST', body: form });
    if (!result.rows?.length) throw new Error('لم يتم العثور على كلمات واضحة. جرّب صورة أوضح أو اكتب الكلمات يدويًا.');
    mergeRows(result.rows);
    showToast(`تم استخراج ${result.rows.length} كلمة. راجعها ثم أكمل تجهيز المحتوى.`);
  } catch (error) {
    showToast(error.message || 'تعذّر استخراج الكلمات', true, 6000);
  } finally {
    setBusy(ui.extractButton, false);
  }
});

function addManualWords(text) {
  const words = cleanWordList(text);
  if (!words.length) throw new Error('اكتب كلمة إنجليزية واحدة على الأقل.');
  const existing = new Set(state.rows.map((row) => String(row.word).toLowerCase()));
  const fresh = words.filter((word) => !existing.has(word.toLowerCase())).map(emptyRow);
  if (state.rows.length + fresh.length > 80) throw new Error('الحد الأقصى 80 كلمة في الملف الواحد.');
  if (!fresh.length) throw new Error('هذه الكلمات موجودة بالفعل في القائمة.');
  state.rows.push(...fresh);
  renderRows();
  revealWorkPanels();
  showToast(`تمت إضافة ${fresh.length} كلمة. اضغط زر توليد المحتوى لإنشاء المعاني والأمثلة.`);
}

ui.addWordsButton.addEventListener('click', () => {
  try { addManualWords(ui.wordsInput.value); ui.wordsInput.value = ''; }
  catch (error) { showToast(error.message, true); }
});
ui.extraWordButton.addEventListener('click', () => {
  try { addManualWords(ui.extraWord.value); ui.extraWord.value = ''; }
  catch (error) { showToast(error.message, true); }
});
ui.extraWord.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') { event.preventDefault(); ui.extraWordButton.click(); }
});

ui.rowsContainer.addEventListener('input', (event) => {
  const field = event.target.dataset.field;
  if (!field) return;
  const card = event.target.closest('.word-card');
  if (!card) return;
  const index = Number(card.dataset.index);
  if (!Number.isInteger(index) || !state.rows[index]) return;
  state.rows[index][field] = event.target.value;
  if (field === 'word') {
    const num = card.querySelector('.word-index');
    if (num) num.textContent = String(index + 1).padStart(2, '0');
  }
});
ui.rowsContainer.addEventListener('click', (event) => {
  const remove = event.target.closest('[data-action="remove"]');
  if (!remove) return;
  const index = Number(remove.dataset.index);
  state.rows.splice(index, 1);
  renderRows();
  revealWorkPanels();
  showToast('تم حذف الكلمة من القائمة.');
});

ui.detailsButton.addEventListener('click', async () => {
  try {
    const key = getApiKey();
    const words = state.rows.map((row) => String(row.word || '').trim()).filter(Boolean);
    if (!words.length) throw new Error('أضف كلمات أولًا.');
    if (words.length > 80) throw new Error('الحد الأقصى 80 كلمة.');
    setBusy(ui.detailsButton, true, 'Gemini يكتب المحتوى...');
    const result = await requestJson('/api/details', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key, words, skip_common: ui.skipCommon.checked }),
    });
    mergeRows(result.rows, true);
    showToast('تم تجهيز مستويات الكلمات والمعاني والأمثلة. راجعها قبل إنشاء الملف.');
  } catch (error) {
    showToast(error.message || 'تعذّر توليد المحتوى', true, 6500);
  } finally {
    setBusy(ui.detailsButton, false);
  }
});

ui.clearButton.addEventListener('click', () => {
  if (!state.rows.length) return;
  state.rows = [];
  renderRows();
  revealWorkPanels();
  ui.mediaProgressWrap.classList.add('hidden');
  showToast('تم مسح القائمة.');
});

function setMediaProgress(done, total, label) {
  const pct = total ? Math.round(done / total * 100) : 0;
  ui.mediaProgressText.textContent = label || `تم تجهيز ${done} من ${total}`;
  ui.mediaProgressNumber.textContent = `${pct}%`;
  ui.mediaProgressBar.style.width = `${pct}%`;
}

ui.mediaButton.addEventListener('click', async () => {
  if (!state.rows.length) return showToast('أضف كلمات أولًا.', true);
  const needs = state.rows.filter((row) => !row.image_data || !row.audio_data);
  if (!needs.length) return showToast('الصور والنطق جاهزان لكل الكلمات بالفعل.');
  setBusy(ui.mediaButton, true, 'جاري تجهيز الوسائط...');
  ui.mediaProgressWrap.classList.remove('hidden');
  setMediaProgress(0, needs.length, 'بدء البحث عن الصور والنطق...');
  let done = 0;
  let failed = 0;
  try {
    for (const row of needs) {
      const index = state.rows.indexOf(row);
      setMediaProgress(done, needs.length, `تجهيز ${row.word} (${done + 1} من ${needs.length})`);
      try {
        const result = await requestJson('/api/media-one', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ row: { word: row.word, image_query: row.image_query }, accent: ui.accent.value }),
        });
        row.image_data = row.image_data || result.image_data || '';
        row.audio_data = row.audio_data || result.audio_data || '';
        row.image_source = row.image_source || result.image_source || '';
        row.image_title = row.image_title || result.image_title || '';
        row.image_error = row.image_data ? '' : (result.image_error || 'تعذّر تجهيز الصورة');
        row.audio_error = row.audio_data ? '' : (result.audio_error || 'تعذّر تجهيز الصوت');
      } catch (error) {
        row.image_error = row.image_error || error.message;
        row.audio_error = row.audio_error || error.message;
        failed += 1;
      }
      done += 1;
      setMediaProgress(done, needs.length, `تم تجهيز ${done} من ${needs.length}`);
      // Rerender the single card so progress and audio previews remain responsive on phones.
      renderRows();
    }
    const imageCount = state.rows.filter((row) => row.image_data).length;
    const audioCount = state.rows.filter((row) => row.audio_data).length;
    showToast(`انتهى التجهيز: صور ${imageCount}/${state.rows.length}، صوت ${audioCount}/${state.rows.length}. يمكنك تنزيل الملف حتى لو فشل بعض الوسائط.`, imageCount === 0 && audioCount === 0, 7000);
  } finally {
    setBusy(ui.mediaButton, false);
  }
});

ui.exportButton.addEventListener('click', async () => {
  const validRows = state.rows.filter((row) => String(row.word || '').trim() && /[A-Za-z]/.test(row.word));
  if (!validRows.length) return showToast('لا توجد كلمات صالحة للتصدير.', true);
  setBusy(ui.exportButton, true, 'جاري إنشاء ملف Anki...');
  try {
    const response = await fetch('/api/export-apkg', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deck_name: ui.deckName.value.trim() || 'English Vocabulary', rows: validRows }),
    });
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `تعذّر إنشاء الملف (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'english_vocabulary.apkg';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    showToast('تم إنشاء ملف .apkg. افتحه من الهاتف باستخدام AnkiDroid أو AnkiMobile.', false, 6000);
  } catch (error) {
    showToast(error.message || 'تعذّر إنشاء ملف Anki', true, 7000);
  } finally {
    setBusy(ui.exportButton, false);
  }
});

function toCsvCell(value) {
  return `"${String(value ?? '').replace(/"/g, '""')}"`;
}
ui.csvButton.addEventListener('click', () => {
  const rows = state.rows.filter((row) => String(row.word || '').trim());
  if (!rows.length) return showToast('لا توجد كلمات للتصدير.', true);
  const headers = ['Word', 'CEFR Level', 'English Definition', 'Example Sentence', 'Image Search Query'];
  const lines = [headers, ...rows.map((r) => [r.word, r.level, r.meaning, r.example, r.image_query])]
    .map((line) => line.map(toCsvCell).join(','));
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'english_vocabulary.csv';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  showToast('تم تنزيل CSV للنصوص فقط. الصور والصوت موجودان في APKG فقط.');
});
