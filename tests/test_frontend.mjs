import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML_PATH = path.resolve(__dirname, '..', 'index.html');

function extractClientScript() {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  const match = html.match(/<script>([\s\S]*?)<\/script>/i);
  assert.ok(match, 'Expected an inline <script> block in index.html');
  return match[1];
}

function createElementStub(id) {
  const attrs = new Map();
  return {
    id,
    textContent: '',
    className: '',
    hidden: false,
    disabled: false,
    style: { color: '' },
    listeners: {},
    setAttribute(name, value) {
      attrs.set(name, String(value));
    },
    getAttribute(name) {
      return attrs.has(name) ? attrs.get(name) : null;
    },
    removeAttribute(name) {
      attrs.delete(name);
    },
    addEventListener(event, handler) {
      this.listeners[event] = handler;
    },
  };
}

function createDomHarness({
  fetchImpl,
  playImpl,
  recorderChunks,
  mediaRecorderCtorError = null,
  mediaRecorderStartError = null,
} = {}) {
  const scriptSource = extractClientScript();
  const bodyAttributes = new Map([['data-state', 'idle']]);

  const elementIds = [
    'micBtn',
    'connectionBadge',
    'connectionLabel',
    'statusText',
    'statusTag',
    'transcriptText',
    'floorValue',
    'actionValue',
    'serviceValue',
    'entityValue',
    'responseMessage',
    'responseMeta',
    'replayBtn',
    'micHint',
    'audioPlayer',
  ];

  const elements = Object.fromEntries(
    elementIds.map((id) => [id, createElementStub(id)])
  );
  elements.connectionBadge.className = 'connection-badge';
  elements.connectionLabel.textContent = 'شبکه محلی فعال';
  elements.responseMeta.hidden = true;
  elements.replayBtn.hidden = true;
  elements.audioPlayer.hidden = true;
  elements.audioPlayer.src = '';
  elements.audioPlayer.onended = null;
  elements.audioPlayer.onerror = null;
  elements.audioPlayer.playCalls = 0;
  elements.audioPlayer.pauseCalls = 0;
  elements.audioPlayer.pause = function pause() {
    this.pauseCalls += 1;
  };
  elements.audioPlayer.play = async function play() {
    this.playCalls += 1;
    if (playImpl) {
      return playImpl();
    }
  };

  let nextTimerId = 1;
  const activeTimers = new Map();
  const loggedErrors = [];
  const loggedWarnings = [];
  const createdRecorders = [];
  const createdStreams = [];
  let getUserMediaCalls = 0;

  class MockMediaRecorder {
    constructor(stream, options) {
      if (mediaRecorderCtorError) {
        throw mediaRecorderCtorError;
      }
      this.stream = stream;
      this.options = options;
      this.state = 'inactive';
      this.ondataavailable = null;
      this.onstop = null;
      createdRecorders.push(this);
    }
    static isTypeSupported() {
      return true;
    }
    start() {
      if (mediaRecorderStartError) {
        throw mediaRecorderStartError;
      }
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
      if (this.ondataavailable && recorderChunks) {
        for (const chunk of recorderChunks) {
          this.ondataavailable({ data: chunk });
        }
      }
      if (this.onstop) {
        return this.onstop();
      }
    }
  }

  const windowListeners = {};
  const sandbox = {
    document: {
      getElementById(id) {
        return elements[id] || null;
      },
      body: {
        setAttribute(name, value) {
          bodyAttributes.set(name, String(value));
        },
        getAttribute(name) {
          return bodyAttributes.has(name) ? bodyAttributes.get(name) : null;
        },
      },
    },
    window: {
      listeners: windowListeners,
      addEventListener(event, handler) {
        windowListeners[event] = handler;
      },
    },
    navigator: {
      mediaDevices: {
        async getUserMedia() {
          getUserMediaCalls += 1;
          const stream = {
            stoppedTracks: 0,
            getTracks() {
              return [{ stop() { stream.stoppedTracks += 1; } }];
            },
          };
          createdStreams.push(stream);
          return stream;
        },
      },
    },
    MediaRecorder: MockMediaRecorder,
    FormData: globalThis.FormData,
    Blob: globalThis.Blob,
    fetch: fetchImpl || (async () => {
      throw new Error('fetch mock not configured');
    }),
    setTimeout(fn, delay) {
      const id = nextTimerId++;
      activeTimers.set(id, { fn, delay });
      return id;
    },
    clearTimeout(id) {
      activeTimers.delete(id);
    },
    console: {
      log: () => {},
      warn: (...args) => loggedWarnings.push(args.join(' ')),
      error: (...args) => loggedErrors.push(args.join(' ')),
    },
    Date,
    Error,
    TypeError,
    Promise,
  };

  const context = vm.createContext(sandbox);
  const compiled = new vm.Script(scriptSource, { filename: 'index.html' });
  compiled.runInContext(context);

  return {
    context,
    elements,
    bodyAttributes,
    activeTimers,
    loggedErrors,
    loggedWarnings,
    createdRecorders,
    createdStreams,
    windowListeners,
    getUserMediaCalls: () => getUserMediaCalls,
    sendAudioToBackend(blob = new Blob(['FAKE_AUDIO'], { type: 'audio/webm' })) {
      return context.sendAudioToBackend(blob);
    },
  };
}

function makeTextResponse(status, textBody) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return textBody;
    },
    async json() {
      return JSON.parse(textBody);
    },
  };
}

function makeJsonResponse(status, obj) {
  return makeTextResponse(status, JSON.stringify(obj));
}

test('index.html inline script has valid JavaScript syntax and exposes helpers', () => {
  const harness = createDomHarness();
  assert.equal(typeof harness.context.sendAudioToBackend, 'function');
  assert.equal(typeof harness.context.validateAudioUrl, 'function');
  assert.equal(harness.bodyAttributes.get('data-state'), 'idle');
});

test('validateAudioUrl allows only local /play-response paths and blocks external or javascript URLs', () => {
  const harness = createDomHarness();
  const { validateAudioUrl } = harness.context;

  assert.equal(validateAudioUrl('/play-response'), '/play-response');
  assert.equal(validateAudioUrl('  /play-response?id=a1b2c3d4e5  '), '/play-response?id=a1b2c3d4e5');

  assert.equal(validateAudioUrl('https://evil.example/a.mp3'), null);
  assert.equal(validateAudioUrl('http://localhost:8000/play-response'), null);
  assert.equal(validateAudioUrl('//evil.example/a.mp3'), null);
  assert.equal(validateAudioUrl('javascript:alert(1)'), null);
  assert.equal(validateAudioUrl('/play-response?id=../../etc/passwd'), null);
  assert.equal(validateAudioUrl('/static/other.mp3'), null);
  assert.equal(validateAudioUrl(null), null);
});

test('HTTP 500 non-JSON "Internal Server Error" renders clean Persian message and puts snippet in responseMeta', async () => {
  const harness = createDomHarness({
    fetchImpl: async () => makeTextResponse(500, 'Internal Server Error'),
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
  assert.equal(harness.elements.statusTag.textContent, 'ERROR');
  assert.equal(harness.elements.responseMessage.className, 'response-message is-error');
  assert.equal(harness.elements.responseMessage.style.color, '');
  assert.equal(harness.elements.responseMessage.getAttribute('role'), 'alert');
  assert.equal(harness.elements.responseMessage.getAttribute('aria-live'), 'assertive');
  assert.doesNotMatch(harness.elements.responseMessage.textContent, /Unexpected token/i);
  assert.doesNotMatch(harness.elements.responseMessage.textContent, /Internal Server Error/);
  assert.doesNotMatch(harness.elements.responseMessage.textContent, /JSON/);
  assert.equal(harness.elements.responseMeta.hidden, false);
  assert.equal(harness.elements.responseMeta.className, 'response-meta ltr is-error');
  assert.equal(
    harness.elements.responseMeta.textContent,
    'HTTP 500 · NON_JSON_RESPONSE · Internal Server Error'
  );
  assert.equal(harness.elements.audioPlayer.playCalls, 0);
  assert.equal(harness.activeTimers.size, 0);
});

test('HTTP 503 OLLAMA_UNREACHABLE displays Ollama offline message and metadata', async () => {
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(503, {
        status: 'error',
        stage: 'llm',
        error_code: 'OLLAMA_UNREACHABLE',
        ollama_running: false,
        requested_model: 'qwen2.5:7b',
        available_models: [],
        detail: 'Ollama service is unreachable.',
      }),
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
  assert.equal(harness.elements.responseMessage.className, 'response-message is-error');
  assert.match(harness.elements.responseMessage.textContent, /Ollama/);
  assert.equal(harness.elements.responseMeta.hidden, false);
  assert.match(harness.elements.responseMeta.className, /is-error/);
  assert.match(harness.elements.responseMeta.textContent, /HTTP 503/);
  assert.match(harness.elements.responseMeta.textContent, /OLLAMA_UNREACHABLE/);
  assert.match(harness.elements.responseMeta.textContent, /ollama:offline/);
  assert.equal(harness.activeTimers.size, 0);
});

test('HTTP 404 MODEL_NOT_FOUND displays requested model and available models', async () => {
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(404, {
        status: 'error',
        stage: 'llm',
        error_code: 'MODEL_NOT_FOUND',
        ollama_running: true,
        requested_model: 'qwen2.5:7b',
        available_models: ['qwen3.5:4b', 'llama3.2:3b'],
        detail: "model 'qwen2.5:7b' not found",
      }),
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
  assert.match(harness.elements.responseMessage.textContent, /qwen2\.5:7b/);
  assert.match(harness.elements.responseMessage.textContent, /qwen3\.5:4b/);
  assert.match(harness.elements.responseMessage.textContent, /llama3\.2:3b/);
  assert.equal(harness.elements.responseMeta.hidden, false);
  assert.match(harness.elements.responseMeta.textContent, /HTTP 404/);
  assert.match(harness.elements.responseMeta.textContent, /MODEL_NOT_FOUND/);
  assert.match(harness.elements.responseMeta.textContent, /model:qwen2\.5:7b/);
  assert.match(
    harness.elements.responseMeta.textContent,
    /available:\[qwen3\.5:4b, llama3\.2:3b\]/
  );
  assert.equal(harness.activeTimers.size, 0);
});

test('all aligned backend error_codes map to specific Persian user messages without raw internals', async () => {
  const cases = [
    { status: 400, stage: 'upload', error_code: 'EMPTY_AUDIO_FILE', expected: /فایل صوتی ارسالی خالی است/ },
    { status: 500, stage: 'upload', error_code: 'UPLOAD_STORAGE_ERROR', expected: /ذخیره‌سازی موقت فایل صوتی در سرور با خطا مواجه شد/ },
    { status: 413, stage: 'upload', error_code: 'PAYLOAD_TOO_LARGE', expected: /حجم فایل صوتی ارسالی بیش از حد مجاز است/ },
    { status: 401, stage: 'auth', error_code: 'UNAUTHORIZED', expected: /احراز هویت با سرور انجام نشد/ },
    { status: 422, stage: 'stt', error_code: 'EMPTY_TRANSCRIPT', expected: /صدایی تشخیص داده نشد/ },
    { status: 500, stage: 'stt', error_code: 'STT_TRANSCRIPTION_FAILED', expected: /تبدیل گفتار به متن با خطا مواجه شد/ },
    { status: 502, stage: 'llm', error_code: 'OLLAMA_CHAT_ERROR', expected: /تحلیل فرمان توسط مدل هوش مصنوعی با خطا مواجه شد/ },
    { status: 502, stage: 'llm', error_code: 'LLM_INVALID_JSON', expected: /نتوانست فرمان را به شکل معتبر تحلیل کند/ },
    { status: 502, stage: 'ha', error_code: 'HA_EXECUTION_ERROR', expected: /اجرای فرمان در سیستم خانه هوشمند/ },
    { status: 422, stage: 'upload', error_code: 'VALIDATION_ERROR', expected: /اطلاعات ارسالی به سرور معتبر نبود/ },
  ];

  for (const tc of cases) {
    const harness = createDomHarness({
      fetchImpl: async () =>
        makeJsonResponse(tc.status, {
          status: 'error',
          stage: tc.stage,
          error_code: tc.error_code,
          detail: 'raw detail',
        }),
    });

    await harness.sendAudioToBackend();
    assert.equal(harness.bodyAttributes.get('data-state'), 'error', tc.error_code);
    assert.equal(harness.elements.responseMessage.className, 'response-message is-error', tc.error_code);
    assert.match(harness.elements.responseMessage.textContent, tc.expected, tc.error_code);
    assert.match(harness.elements.responseMeta.textContent, new RegExp(tc.error_code), tc.error_code);
  }
});

test('HTTP 200 with tts_status.ok=false and audio_url=null uses data-state="warning", keeps telemetry without port, and skips play()', async () => {
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ آشپزخانه طبقه دوم را روشن کن',
        parsed_command: {
          floor: 2,
          domain: 'light',
          service: 'turn_on',
          entity_id: 'light.kitchen',
          service_data: {},
          ack_message: 'در حال انجام...',
          success_message: 'چراغ آشپزخانه طبقه دوم روشن شد.',
        },
        ha_execution: { ok: true, status_code: 200, simulated: false },
        audio_url: null,
        tts_status: {
          ok: false,
          voice: 'fa-IR-DilaraNeural',
          fallback_message: 'پاسخ متنی آماده است؛ سرویس صوتی در دسترس نبود.',
          error: 'Edge-TTS request timed out',
        },
      }),
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.elements.audioPlayer.playCalls, 0);
  assert.equal(harness.elements.audioPlayer.src, '');
  assert.equal(harness.elements.transcriptText.textContent, 'چراغ آشپزخانه طبقه دوم را روشن کن');
  assert.equal(harness.elements.floorValue.textContent, 'طبقه 2');
  assert.doesNotMatch(harness.elements.floorValue.textContent, /پورت|8501|8502/);
  assert.equal(harness.elements.serviceValue.textContent, 'light.turn_on');
  assert.equal(harness.elements.entityValue.textContent, 'light.kitchen');
  assert.equal(harness.elements.actionValue.textContent, 'اجرا شد (200)');
  assert.equal(harness.elements.actionValue.className, 'metric-value is-success');
  assert.equal(harness.elements.responseMessage.className, 'response-message is-success');
  assert.equal(harness.elements.responseMessage.style.color, '');
  assert.match(harness.elements.responseMessage.textContent, /چراغ آشپزخانه طبقه دوم روشن شد\./);
  assert.match(
    harness.elements.responseMessage.textContent,
    /پاسخ متنی آماده است؛ سرویس صوتی در دسترس نبود\./
  );
  assert.equal(harness.elements.responseMeta.hidden, false);
  assert.equal(harness.elements.responseMeta.className, 'response-meta ltr is-warning');
  assert.match(harness.elements.responseMeta.textContent, /TTS FALLBACK · Edge-TTS request timed out/);
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
  assert.equal(harness.elements.statusTag.textContent, 'TTS FALLBACK');
  assert.equal(harness.elements.replayBtn.hidden, true);
  assert.equal(harness.activeTimers.size, 0);
});

test('unsafe audio_url from server is rejected by validateAudioUrl and enters warning TTS fallback', async () => {
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ را روشن کن',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_on',
          entity_id: 'light.living_room',
          success_message: 'روشن شد.',
        },
        ha_execution: { ok: true, status_code: 200 },
        audio_url: 'https://evil.example/a.mp3',
        tts_status: { ok: true },
      }),
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.elements.audioPlayer.playCalls, 0);
  assert.equal(harness.elements.audioPlayer.src, '');
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
  assert.equal(harness.elements.statusTag.textContent, 'TTS FALLBACK');
  assert.equal(harness.elements.responseMeta.textContent, 'TTS FALLBACK · INVALID_AUDIO_URL');
});

test('subsequent HTTP 200 success after error resets CSS classes/aria attributes and plays audio, and error resets stale telemetry', async () => {
  let callIndex = 0;
  const harness = createDomHarness({
    fetchImpl: async () => {
      callIndex += 1;
      if (callIndex === 1) {
        return makeJsonResponse(200, {
          status: 'success',
          transcript: 'چراغ سالن را روشن کن',
          parsed_command: {
            floor: 2,
            domain: 'light',
            service: 'turn_on',
            entity_id: 'light.living_room',
            success_message: 'چراغ سالن روشن شد.',
          },
          ha_execution: { ok: false, simulated: true },
          audio_url: '/play-response?id=abc123def0',
          tts_status: { ok: true },
        });
      }
      if (callIndex === 2) {
        return makeTextResponse(500, 'Internal Server Error');
      }
      return makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ راهرو را روشن کن',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_on',
          entity_id: 'light.hallway',
          success_message: 'چراغ راهرو طبقه اول روشن شد.',
        },
        ha_execution: { ok: true, status_code: 200, simulated: false },
        audio_url: '/play-response?id=1122334455',
        tts_status: { ok: true },
      });
    },
  });

  // 1st call populates telemetry (simulated HA warning badge)
  await harness.sendAudioToBackend();
  assert.equal(harness.elements.transcriptText.textContent, 'چراغ سالن را روشن کن');
  assert.equal(harness.elements.floorValue.textContent, 'طبقه 2');
  assert.equal(harness.elements.actionValue.className, 'metric-value is-warning');

  // 2nd call fails -> stale telemetry must be cleared and error CSS/ARIA applied
  await harness.sendAudioToBackend();
  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
  assert.equal(harness.elements.responseMessage.className, 'response-message is-error');
  assert.equal(harness.elements.responseMessage.getAttribute('role'), 'alert');
  assert.equal(harness.elements.transcriptText.textContent, '—');
  assert.equal(harness.elements.floorValue.textContent, '—');
  assert.equal(harness.elements.actionValue.textContent, '—');
  assert.equal(harness.elements.actionValue.className, 'metric-value');
  assert.equal(harness.elements.serviceValue.textContent, '—');
  assert.equal(harness.elements.entityValue.textContent, '—');

  // 3rd call succeeds -> classes reset to is-success, role removed, responseMeta hidden, audio plays
  await harness.sendAudioToBackend();
  assert.equal(harness.elements.responseMessage.className, 'response-message is-success');
  assert.equal(harness.elements.responseMessage.getAttribute('role'), null);
  assert.equal(harness.elements.responseMessage.getAttribute('aria-live'), 'polite');
  assert.equal(harness.elements.responseMessage.textContent, 'چراغ راهرو طبقه اول روشن شد.');
  assert.equal(harness.elements.actionValue.className, 'metric-value is-success');
  assert.equal(harness.elements.responseMeta.hidden, true);
  assert.equal(harness.elements.responseMeta.textContent, '');
  assert.equal(harness.bodyAttributes.get('data-state'), 'playing');
  assert.equal(harness.elements.statusTag.textContent, 'PLAYING RESPONSE');
  assert.equal(harness.elements.audioPlayer.playCalls, 2);
  assert.match(harness.elements.audioPlayer.src, /^\/play-response\?id=1122334455&t=\d+$/);

  harness.elements.audioPlayer.onended();
  assert.equal(harness.bodyAttributes.get('data-state'), 'idle');
  assert.equal(harness.elements.statusTag.textContent, 'IDLE');
});

test('NotAllowedError autoplay block sets data-state="warning", reveals #replayBtn, and replay plays audio', async () => {
  let blockPlay = true;
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ را خاموش کن',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_off',
          entity_id: 'light.living_room',
          success_message: 'چراغ خاموش شد.',
        },
        ha_execution: { ok: true, status_code: 200 },
        audio_url: '/play-response?id=1122334455',
        tts_status: { ok: true },
      }),
    playImpl: async () => {
      if (blockPlay) {
        const err = new Error('play() failed because user did not interact');
        err.name = 'NotAllowedError';
        throw err;
      }
    },
  });

  await harness.sendAudioToBackend();

  assert.equal(harness.elements.audioPlayer.playCalls, 1);
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
  assert.equal(harness.elements.statusTag.textContent, 'AUTOPLAY BLOCKED');
  assert.equal(harness.elements.responseMessage.className, 'response-message is-warning');
  assert.equal(harness.elements.responseMeta.hidden, false);
  assert.equal(harness.elements.responseMeta.className, 'response-meta ltr is-warning');
  assert.equal(harness.elements.responseMeta.textContent, 'AUTOPLAY BLOCKED');
  assert.equal(harness.elements.replayBtn.hidden, false);

  // Clicking #replayBtn when user gesture permits playback plays audio and returns to idle
  blockPlay = false;
  await harness.elements.replayBtn.listeners.click();
  assert.equal(harness.elements.audioPlayer.playCalls, 2);
  assert.equal(harness.elements.replayBtn.hidden, true);
  assert.equal(harness.elements.responseMessage.className, 'response-message is-success');
  assert.equal(harness.elements.responseMessage.textContent, 'چراغ خاموش شد.');
  assert.equal(harness.bodyAttributes.get('data-state'), 'playing');

  harness.elements.audioPlayer.onended();
  assert.equal(harness.bodyAttributes.get('data-state'), 'idle');
});

test('audioPlayer.onerror transitions to data-state="warning" AUDIO ERROR and reveals #replayBtn', async () => {
  const harness = createDomHarness({
    fetchImpl: async () =>
      makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ را خاموش کن',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_off',
          entity_id: 'light.living_room',
          success_message: 'چراغ خاموش شد.',
        },
        ha_execution: { ok: true, status_code: 200 },
        audio_url: '/play-response?id=aabbccdd11',
        tts_status: { ok: true },
      }),
  });

  await harness.sendAudioToBackend();
  assert.equal(harness.bodyAttributes.get('data-state'), 'playing');

  harness.elements.audioPlayer.onerror();
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
  assert.equal(harness.elements.statusTag.textContent, 'AUDIO ERROR');
  assert.equal(harness.elements.responseMeta.textContent, 'AUDIO_LOAD_ERROR');
  assert.equal(harness.elements.replayBtn.hidden, false);
});

test('isProcessing disables #micBtn while request is in flight, blocks re-entry, and re-enables in finally', async () => {
  let resolveFetch;
  let fetchCalls = 0;
  const harness = createDomHarness({
    fetchImpl: () => {
      fetchCalls += 1;
      return new Promise((resolve) => {
        resolveFetch = resolve;
      });
    },
  });

  const firstCall = harness.sendAudioToBackend();
  assert.equal(harness.elements.micBtn.disabled, true);
  assert.equal(fetchCalls, 1);

  // Concurrent call while processing must be ignored
  await harness.sendAudioToBackend();
  assert.equal(fetchCalls, 1);

  resolveFetch(makeTextResponse(500, 'Internal Server Error'));
  await firstCall;

  assert.equal(harness.elements.micBtn.disabled, false);
  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
});

test('keyboard Space/Enter handlers start and stop recording and blur stops active recording', async () => {
  const bigChunk = new Blob([new Uint8Array(900)], { type: 'audio/webm' });
  let fetchCalls = 0;
  const harness = createDomHarness({
    recorderChunks: [bigChunk],
    fetchImpl: async () => {
      fetchCalls += 1;
      return makeJsonResponse(200, {
        status: 'success',
        transcript: 'فرمان صوتی',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_on',
          entity_id: 'light.living_room',
          success_message: 'روشن شد.',
        },
        ha_execution: { ok: true, status_code: 200 },
        audio_url: null,
        tts_status: { ok: false, fallback_message: 'بدون صدا' },
      });
    },
  });

  const makeKeyEvent = (key, repeat = false) => ({
    key,
    repeat,
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  });

  const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve));

  // Key repeat must be ignored
  const repeatEvt = makeKeyEvent(' ', true);
  harness.elements.micBtn.listeners.keydown(repeatEvt);
  await flushMicrotasks();
  assert.equal(repeatEvt.prevented, true);
  assert.equal(harness.getUserMediaCalls(), 0);

  // Space keydown starts recording
  const downSpace = makeKeyEvent(' ');
  harness.elements.micBtn.listeners.keydown(downSpace);
  await flushMicrotasks();
  assert.deepEqual(harness.loggedErrors, []);
  assert.equal(harness.getUserMediaCalls(), 1);
  assert.equal(harness.bodyAttributes.get('data-state'), 'listening');
  assert.equal(harness.elements.micBtn.getAttribute('aria-pressed'), 'true');

  // Space keyup stops recording and submits audio
  const upSpace = makeKeyEvent(' ');
  harness.elements.micBtn.listeners.keyup(upSpace);
  await flushMicrotasks();
  assert.equal(harness.elements.micBtn.getAttribute('aria-pressed'), 'false');
  assert.equal(fetchCalls, 1);

  // Enter keydown starts recording, then blur stops it safely
  const downEnter = makeKeyEvent('Enter');
  harness.elements.micBtn.listeners.keydown(downEnter);
  await flushMicrotasks();
  assert.equal(harness.getUserMediaCalls(), 2);
  assert.equal(harness.bodyAttributes.get('data-state'), 'listening');

  harness.elements.micBtn.listeners.blur();
  await flushMicrotasks();
  assert.equal(harness.elements.micBtn.getAttribute('aria-pressed'), 'false');
  assert.equal(fetchCalls, 2);
});

test('network TypeError sets connectionBadge offline and recovers on next response', async () => {
  let failNetwork = true;
  const harness = createDomHarness({
    fetchImpl: async () => {
      if (failNetwork) {
        throw new TypeError('Failed to fetch');
      }
      return makeJsonResponse(200, {
        status: 'success',
        transcript: 'چراغ روشن',
        parsed_command: {
          floor: 1,
          domain: 'light',
          service: 'turn_on',
          entity_id: 'light.living_room',
          success_message: 'روشن شد.',
        },
        ha_execution: { ok: true, status_code: 200 },
        audio_url: null,
        tts_status: { ok: false, fallback_message: 'بدون صدا' },
      });
    },
  });

  await harness.sendAudioToBackend();
  assert.equal(harness.bodyAttributes.get('data-state'), 'error');
  assert.equal(harness.elements.connectionBadge.className, 'connection-badge is-offline');
  assert.match(harness.elements.responseMeta.textContent, /NETWORK_ERROR/);

  failNetwork = false;
  await harness.sendAudioToBackend();
  assert.equal(harness.elements.connectionBadge.className, 'connection-badge');
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
});

test('stage timers progress transcribing -> reasoning -> executing while pending and clear on resolve', async () => {
  let resolveFetch;
  const harness = createDomHarness({
    fetchImpl: () =>
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
  });

  const pendingCall = harness.sendAudioToBackend();

  assert.equal(harness.bodyAttributes.get('data-state'), 'transcribing');
  assert.equal(harness.elements.statusTag.textContent, 'TRANSCRIBING');
  assert.equal(harness.activeTimers.size, 2);

  const timers = Array.from(harness.activeTimers.values());
  timers[0].fn();
  assert.equal(harness.bodyAttributes.get('data-state'), 'reasoning');
  assert.equal(harness.elements.statusTag.textContent, 'REASONING');

  timers[1].fn();
  assert.equal(harness.bodyAttributes.get('data-state'), 'executing');
  assert.equal(harness.elements.statusTag.textContent, 'EXECUTING');

  resolveFetch(
    makeJsonResponse(200, {
      status: 'success',
      transcript: 'تست',
      parsed_command: {
        floor: 1,
        domain: 'light',
        service: 'turn_on',
        entity_id: 'light.living_room',
        success_message: 'روشن شد.',
      },
      ha_execution: { ok: true, status_code: 200 },
      audio_url: null,
      tts_status: { ok: false, fallback_message: 'بدون صدا' },
    })
  );
  await pendingCall;

  assert.equal(harness.activeTimers.size, 0);
  assert.equal(harness.bodyAttributes.get('data-state'), 'warning');
});

test('startRecording cleans up activeStream tracks if MediaRecorder constructor or start() throws', async () => {
  for (const [label, opts] of [
    ['constructor throws', { mediaRecorderCtorError: new Error('NotSupportedError: mime unsupported') }],
    ['start() throws', { mediaRecorderStartError: new Error('InvalidStateError: failed to start') }],
  ]) {
    const harness = createDomHarness(opts);
    await harness.context.startRecording();

    assert.equal(harness.createdStreams.length, 1, label);
    assert.ok(harness.createdStreams[0].stoppedTracks >= 1, label);
    assert.equal(harness.bodyAttributes.get('data-state'), 'error', label);
    assert.equal(harness.elements.statusTag.textContent, 'MIC ERROR', label);
    assert.equal(harness.elements.responseMeta.textContent, 'MIC_ACCESS_ERROR', label);
    assert.equal(harness.elements.micBtn.getAttribute('aria-pressed'), 'false', label);
  }
});
