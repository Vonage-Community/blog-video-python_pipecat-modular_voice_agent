// Browser side of the demo.
//
// 0. The waiting room: pick how the agent listens, thinks, speaks, and takes turns.
//    The server checks every choice before it uses it.
// 1. Get microphone (and optional camera) access with the Vonage Video Web SDK.
// 2. Ask our server for a session with those choices. It returns the browser's token only.
// 3. Join the routed Vonage Video session and publish.
// 4. Subscribe to the AI agent's stream, recognised by the role in its
//    connection data, and to nothing else.
// 5. Once its audio can play, tell the server so the agent greets you once.
// 6. Show the transcript signals the server sends: what Deepgram heard, and what
//    the agent said, plus how long each stage took before each reply.
'use strict';

const $ = (id) => document.getElementById(id);
const ui = {
  setup: $('setup'),
  start: $('start'),
  useCamera: $('use-camera'),
  call: $('call'),
  publisher: $('publisher'),
  youPlaceholder: $('you-placeholder'),
  youLevel: $('you-level'),
  mutedBadge: $('muted-badge'),
  agentMedia: $('agent-media'),
  agentLevel: $('agent-level'),
  agentHearing: $('agent-hearing'),
  enableAudio: $('enable-audio'),
  mute: $('mute'),
  camera: $('camera'),
  leave: $('leave'),
  timeLeft: $('time-left'),
  stages: $('stages'),
  echoNote: $('echo-note'),
  language: $('opt-language'),
  keytermTags: $('keyterm-tags'),
  keytermInput: $('keyterm-input'),
  model: $('opt-model'),
  instructions: $('opt-instructions'),
  instructionsCount: $('instructions-count'),
  voice: $('opt-voice'),
  customVoiceField: $('custom-voice-field'),
  voiceId: $('opt-voice-id'),
  voicesNote: $('voices-note'),
  speed: $('opt-speed'),
  speedValue: $('speed-value'),
  pause: $('opt-pause'),
  words: $('opt-words'),
  wordsValue: $('words-value'),
  resetOptions: $('reset-options'),
  callSummary: $('call-summary'),
  transcript: $('transcript'),
  transcriptLines: $('transcript-lines'),
  status: $('status'),
  error: $('error'),
};

const END_MESSAGES = {
  left: 'You left the conversation.',
  user_stream_left: 'Your stream left the Vonage session, so the agent stopped.',
  max_duration: 'The session reached its maximum length.',
  idle_timeout: 'The session ended after a long silence.',
  ready_timeout: 'The session ended because the agent and browser never connected.',
  server_shutdown: 'The server shut down.',
  agent_exited: 'The AI agent left the call.',
  error: 'The AI agent stopped because of an error.',
};

let call = null; // the conversation in progress, if any

// --- Waiting room ------------------------------------------------------------------

const CUSTOM_VOICE = '__custom__';
const STORAGE_KEY = 'modular-voice-agent-options';
const PAUSE_LABELS = { quick: 'Quick', relaxed: 'Relaxed', patient: 'Patient' };
const room = { catalog: null, keyterms: [] };

async function loadWaitingRoom() {
  let catalog;
  try {
    catalog = await api('/api/options');
  } catch (error) {
    // Not configured yet: Start shows the list of problems, as before.
    ui.stages.hidden = true;
    return;
  }
  room.catalog = catalog;
  if (catalog.agentMode === 'echo') {
    ui.stages.hidden = true;
    ui.echoNote.hidden = false;
    return;
  }
  fillSelect(ui.language, catalog.languages.map((l) => [l.code, l.name]));
  fillSelect(ui.model, catalog.openaiModels.map((m) => [m, m]));
  fillVoices(catalog.voices);
  const pauses = catalog.limits.replyPauses;
  ui.pause.replaceChildren(
    ...Object.entries(pauses).map(([value, secs]) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      Object.assign(input, { type: 'radio', name: 'pause', value });
      label.append(input, ` ${PAUSE_LABELS[value] || value} `);
      const small = document.createElement('small');
      small.textContent = `${secs}s`;
      label.append(small);
      return label;
    }),
  );
  ui.instructions.maxLength = catalog.limits.maxInstructionsChars;
  applyOptions({ ...catalog.defaults, ...savedOptions() });
}

function fillSelect(select, entries) {
  select.replaceChildren(
    ...entries.map(([value, text]) => Object.assign(document.createElement('option'), { value, text })),
  );
}

function fillVoices({ recommended, yours, yoursStatus }) {
  const group = (label, voices) => {
    const optgroup = Object.assign(document.createElement('optgroup'), { label });
    for (const v of voices) {
      // Keep the dropdown short; the accent and gender show on hover.
      optgroup.append(
        Object.assign(document.createElement('option'), { value: v.id, text: v.name, title: v.description }),
      );
    }
    return optgroup;
  };
  const groups = [group('Recommended voices', recommended)];
  const recommendedIds = new Set(recommended.map((v) => v.id));
  const own = yours.filter((v) => !recommendedIds.has(v.id));
  if (own.length) groups.push(group('Your ElevenLabs voices', own));
  const other = group('Other', []);
  other.append(Object.assign(document.createElement('option'), { value: CUSTOM_VOICE, text: 'Paste a voice ID…' }));
  groups.push(other);
  ui.voice.replaceChildren(...groups);
  if (yoursStatus === 'missing_permission') {
    ui.voicesNote.textContent =
      'To list your own ElevenLabs voices here, give the API key the Voices: Read permission.';
    ui.voicesNote.hidden = false;
  }
}

function applyOptions(o) {
  ui.language.value = o.language;
  room.keyterms = [...(o.keyterms || [])];
  renderKeyterms();
  ui.model.value = o.openaiModel;
  if (!ui.model.value) ui.model.selectedIndex = 0;
  ui.instructions.value = o.instructions || '';
  const known = [...ui.voice.options].some((opt) => opt.value === o.voiceId);
  ui.voice.value = known ? o.voiceId : CUSTOM_VOICE;
  ui.voiceId.value = known ? '' : o.voiceId || '';
  ui.speed.value = o.voiceSpeed;
  ui.words.value = o.interruptMinWords;
  const pause = ui.pause.querySelector(`input[value="${o.replyPause}"]`) || ui.pause.querySelector('input');
  if (pause) pause.checked = true;
  renderOptionLabels();
}

function currentOptions() {
  return {
    language: ui.language.value,
    keyterms: room.keyterms,
    openaiModel: ui.model.value,
    instructions: ui.instructions.value.trim(),
    voiceId: ui.voice.value === CUSTOM_VOICE ? ui.voiceId.value.trim() : ui.voice.value,
    voiceSpeed: Number(ui.speed.value),
    interruptMinWords: Number(ui.words.value),
    replyPause: ui.pause.querySelector('input:checked')?.value || 'quick',
  };
}

function renderOptionLabels() {
  ui.speedValue.textContent = `${Number(ui.speed.value).toFixed(2).replace(/0$/, '')}×`;
  ui.wordsValue.textContent = ui.words.value;
  ui.customVoiceField.hidden = ui.voice.value !== CUSTOM_VOICE;
  const max = room.catalog?.limits.maxInstructionsChars || 500;
  ui.instructionsCount.textContent = `${ui.instructions.value.length}/${max}`;
}

function renderKeyterms() {
  for (const tag of ui.keytermTags.querySelectorAll('.tag')) tag.remove();
  for (const term of room.keyterms) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = term;
    const remove = Object.assign(document.createElement('button'), {
      type: 'button',
      textContent: '×',
      title: `Remove ${term}`,
    });
    remove.setAttribute('aria-label', `Remove ${term}`);
    remove.addEventListener('click', () => {
      room.keyterms = room.keyterms.filter((t) => t !== term);
      renderKeyterms();
      saveOptions();
    });
    tag.append(remove);
    ui.keytermTags.insertBefore(tag, ui.keytermInput);
  }
}

function addKeyterm() {
  const max = room.catalog?.limits.maxKeyterms || 20;
  const words = ui.keytermInput.value.split(',').map((w) => w.trim()).filter(Boolean);
  for (const word of words) {
    if (!room.keyterms.includes(word) && room.keyterms.length < max) room.keyterms.push(word);
  }
  ui.keytermInput.value = '';
  renderKeyterms();
  saveOptions();
}

function savedOptions() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveOptions() {
  if (!room.catalog || room.catalog.agentMode === 'echo') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(currentOptions()));
  } catch {}
}

// The settings this call actually uses (as the server accepted them), shown in the room.
function renderCallSummary(options) {
  ui.callSummary.replaceChildren();
  if (!options || call?.agentMode === 'echo') return;
  const language = room.catalog?.languages.find((l) => l.code === options.language)?.name || options.language;
  const voice = [...ui.voice.options].find((o) => o.value === options.voiceId)?.text.split(' (')[0] || options.voiceId;
  const terms = options.keyterms.length ? `, ${options.keyterms.length} keyterm${options.keyterms.length > 1 ? 's' : ''}` : '';
  const items = [
    ['listen', 'Deepgram', `${language}${terms}`],
    ['think', 'OpenAI', `${options.openaiModel}${options.instructions ? ', custom instructions' : ''}`],
    ['speak', 'ElevenLabs', `${voice}, ${options.voiceSpeed}×`],
    ['turns', 'Turn-taking', `${PAUSE_LABELS[options.replyPause]} pause, ${options.interruptMinWords} words to interrupt`],
  ];
  for (const [kind, name, value] of items) {
    const item = document.createElement('li');
    item.className = kind;
    const label = document.createElement('strong');
    label.textContent = name;
    item.append(label, ` ${value}`);
    ui.callSummary.append(item);
  }
}

for (const el of [ui.language, ui.model, ui.voice, ui.voiceId, ui.speed, ui.words, ui.instructions]) {
  el.addEventListener('input', () => {
    renderOptionLabels();
    saveOptions();
  });
}
ui.pause.addEventListener('change', saveOptions);
ui.keytermInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ',') {
    event.preventDefault();
    addKeyterm();
  } else if (event.key === 'Backspace' && !ui.keytermInput.value && room.keyterms.length) {
    room.keyterms.pop();
    renderKeyterms();
    saveOptions();
  }
});
ui.keytermInput.addEventListener('blur', () => ui.keytermInput.value.trim() && addKeyterm());
ui.keytermTags.addEventListener('click', (event) => {
  if (event.target === ui.keytermTags) ui.keytermInput.focus();
});
ui.resetOptions.addEventListener('click', () => {
  localStorage.removeItem(STORAGE_KEY);
  if (room.catalog) applyOptions(room.catalog.defaults);
});

// --- Small helpers ---------------------------------------------------------------

function setStatus(text) {
  ui.status.textContent = text;
  console.info('[demo]', text);
}

function showError(message, problems = []) {
  ui.error.replaceChildren(document.createTextNode(message));
  if (problems.length) {
    const list = document.createElement('ul');
    for (const problem of problems) {
      const item = document.createElement('li');
      item.textContent = problem;
      list.append(item);
    }
    ui.error.append(list);
  }
  ui.error.hidden = false;
}

function clearError() {
  ui.error.hidden = true;
  ui.error.replaceChildren();
}

class ApiError extends Error {
  constructor(status, message, problems = []) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

async function api(path, { method = 'GET', key, body, keepalive = false } = {}) {
  const headers = {};
  if (key) headers.Authorization = `Bearer ${key}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      keepalive,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the demo server. Is `docker compose up` still running?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message =
      typeof detail === 'string' ? detail : detail?.message || `Request failed (HTTP ${response.status})`;
    throw new ApiError(response.status, message, detail?.problems || []);
  }
  return data;
}

// Our server puts {"role": "user"} or {"role": "agent"} in each token's connection data.
function roleOf(connection) {
  try {
    return JSON.parse(connection.data || '{}').role ?? null;
  } catch {
    return null;
  }
}

// Turns the SDK's raw audio levels into a meter value and a "speaking" flag.
function levelTracker(meter) {
  let average = 0;
  let lastLoud = 0;
  return {
    update(level) {
      average = level > average ? level : 0.7 * average + 0.3 * level;
      const scaled = Math.min(1, Math.max(0, Math.log10(average) / 1.5 + 1));
      meter.value = scaled;
      if (scaled > 0.35) lastLoud = performance.now();
    },
    get speaking() {
      return performance.now() - lastLoud < 500;
    },
    reset() {
      average = 0;
      lastLoud = 0;
      meter.value = 0;
    },
  };
}

const youLevel = levelTracker(ui.youLevel);
const agentLevel = levelTracker(ui.agentLevel);

function preflightProblem() {
  if (!window.isSecureContext) {
    return 'Browsers only allow microphone access on secure pages. Open http://localhost:8000 on this computer, or serve the app over HTTPS.';
  }
  if (typeof OT === 'undefined') {
    return "The Vonage Video Web SDK didn't load from unpkg.com. Check your network or content blocker, then reload.";
  }
  if (OT.checkSystemRequirements() !== 1) {
    return "This browser isn't supported by the Vonage Video Web SDK. Try a current Chrome, Edge, Firefox, or Safari.";
  }
  return null;
}

function mediaErrorMessage(error) {
  switch (error.name) {
    case 'OT_USER_MEDIA_ACCESS_DENIED':
      return 'Microphone access is blocked. Allow it in your browser\'s site settings (the icon in the address bar), then try again.';
    case 'OT_NO_DEVICES_FOUND':
    case 'OT_NO_VALID_CONSTRAINTS':
      return 'No microphone was found. Connect one and try again.';
    case 'OT_HARDWARE_UNAVAILABLE':
      return 'Your microphone or camera is being used by another app. Close it and try again.';
    default:
      return `Could not start your microphone: ${error.message}`;
  }
}

// --- Call flow -------------------------------------------------------------------

function createPublisher(useCamera) {
  return new Promise((resolve, reject) => {
    const publisher = OT.initPublisher(
      ui.publisher,
      {
        insertMode: 'append',
        width: '100%',
        height: '100%',
        name: 'You',
        videoSource: useCamera ? undefined : null, // null = audio only
        publishVideo: useCamera,
        style: {
          buttonDisplayMode: 'off',
          nameDisplayMode: 'off',
          audioLevelDisplayMode: 'off',
          archiveStatusDisplayMode: 'off',
        },
      },
      (error) => (error ? reject(new Error(mediaErrorMessage(error))) : resolve(publisher)),
    );
    publisher.on('accessDialogOpened', () =>
      setStatus('Allow microphone access in your browser\'s prompt…'),
    );
    publisher.on('audioLevelUpdated', (event) => youLevel.update(event.audioLevel));
    publisher.on('mediaStopped', () => {
      if (call && !call.ending) endCall('Your microphone stopped (was it unplugged?).', true);
    });
  });
}

function joinSession({ applicationId, vonageSessionId, token }) {
  const session = OT.initSession(applicationId, vonageSessionId);
  call.session = session;
  session.on({
    streamCreated: (event) => onStreamCreated(event.stream),
    streamDestroyed: (event) => {
      if (roleOf(event.stream.connection) === 'agent' && call && !call.ending) {
        setStatus('The AI agent left the call.');
      }
    },
    'signal:transcript': onTranscript,
    'signal:latency': onLatency,
    sessionReconnecting: () => setStatus('Network hiccup: reconnecting to Vonage Video…'),
    sessionReconnected: () => setStatus('Reconnected.'),
    sessionDisconnected: (event) => {
      if (call && !call.ending) {
        endCall(`Disconnected from Vonage Video (${event.reason}).`, event.reason !== 'clientDisconnected');
      }
    },
  });

  setStatus('Connecting to Vonage Video…');
  return new Promise((resolve, reject) => {
    session.connect(token, (connectError) => {
      if (connectError) {
        reject(new Error(`Could not connect to Vonage Video: ${connectError.message}`));
        return;
      }
      if (!call.agentSubscriber) setStatus('Connected. Waiting for the AI agent to join…');
      session.publish(call.publisher, (publishError) =>
        publishError
          ? reject(new Error(`Could not publish your microphone: ${publishError.message}`))
          : resolve(),
      );
    });
  });
}

// The server sends one signal per turn. Only accept signals from the server
// (event.from is null), not ones another participant could send.
function onTranscript(event) {
  if (event.from) return;
  let line;
  try {
    line = JSON.parse(event.data);
  } catch {
    return;
  }
  if (typeof line?.text !== 'string') return;
  const item = document.createElement('li');
  item.className = line.role === 'user' ? 'heard' : 'said';
  const label = document.createElement('strong');
  label.textContent = line.role === 'user' ? 'Deepgram heard' : 'Agent said';
  const text = document.createElement('span');
  text.textContent = line.text;
  item.append(label, text);
  if (line.interrupted) {
    const note = document.createElement('em');
    note.textContent = ' (interrupted: only the words you heard)';
    item.append(note);
  }
  ui.transcriptLines.append(item);
  ui.transcript.hidden = false;
  item.scrollIntoView({ block: 'nearest' });
}

// One per reply, sent when the agent starts speaking: how long you waited and where
// the time went. Same rule as transcripts: only from the server.
function onLatency(event) {
  if (event.from) return;
  let timing;
  try {
    timing = JSON.parse(event.data);
  } catch {
    return;
  }
  if (!Array.isArray(timing?.stages) || typeof timing.total !== 'number') return;
  const item = document.createElement('li');
  item.className = 'timing';
  const total = document.createElement('strong');
  total.textContent = `Replied after ${timing.total.toFixed(2)} s`;
  const bar = document.createElement('span');
  bar.className = 'bar';
  bar.setAttribute('aria-hidden', 'true');
  const parts = document.createElement('span');
  parts.className = 'parts';
  for (const { stage, secs } of timing.stages) {
    const kind = stage.toLowerCase().replace(/[^a-z]/g, '');
    const segment = document.createElement('span');
    segment.className = `seg ${kind}`;
    segment.style.flexGrow = String(Math.max(secs, 0.001));
    bar.append(segment);
    const part = document.createElement('span');
    part.className = `part ${kind}`;
    part.textContent = `${stage} ${secs.toFixed(2)}`;
    parts.append(part);
  }
  item.append(total, bar, parts);
  ui.transcriptLines.append(item);
  ui.transcript.hidden = false;
  item.scrollIntoView({ block: 'nearest' });
}

function onStreamCreated(stream) {
  const role = roleOf(stream.connection);
  if (role !== 'agent') {
    console.info(`[demo] Ignoring a stream from a "${role}" participant`);
    return;
  }
  if (!call || call.agentSubscriber) return;

  setStatus('The AI agent joined. Connecting to its audio…');
  const subscriber = call.session.subscribe(
    stream,
    ui.agentMedia,
    {
      insertMode: 'append',
      width: 1,
      height: 1,
      subscribeToVideo: false, // the agent publishes audio only
      style: {
        audioBlockedDisplayMode: 'off', // we show our own "enable audio" button
        buttonDisplayMode: 'off',
        nameDisplayMode: 'off',
        audioLevelDisplayMode: 'off',
        videoDisabledDisplayMode: 'off',
      },
    },
    (error) => {
      if (error) {
        endCall(`Could not subscribe to the AI agent: ${error.message}`, true);
      } else if (subscriber.isAudioBlocked()) {
        offerAudioUnblock();
      } else {
        signalReady();
      }
    },
  );
  call.agentSubscriber = subscriber;
  subscriber.on('audioBlocked', offerAudioUnblock);
  subscriber.on('audioUnblocked', () => {
    ui.enableAudio.hidden = true;
    signalReady();
  });
  subscriber.on('audioLevelUpdated', (event) => agentLevel.update(event.audioLevel));
}

function offerAudioUnblock() {
  ui.enableAudio.hidden = false;
  setStatus('Your browser blocked audio playback. Click "Enable audio" to hear the agent.');
}

// Tell the server the agent's audio can play here. The agent greets you only after this.
async function signalReady() {
  if (!call || call.ready || call.ending) return;
  call.ready = true;
  setStatus('Starting the conversation…');
  try {
    await api(`/api/sessions/${call.id}/ready`, { method: 'POST', key: call.key });
    call.live = true;
    call.statusTimer = setInterval(renderLiveStatus, 250);
    renderLiveStatus();
  } catch (error) {
    endCall(error.message, true);
  }
}

// Status while talking, derived from the real audio levels of both streams.
function renderLiveStatus() {
  if (!call?.live || call.ending) return;
  if (call.agentMode === 'echo') {
    setStatusQuietly('Echo diagnostic: you should have heard a beep. Now speak and you will hear yourself.');
  } else if (agentLevel.speaking) {
    setStatusQuietly('The AI agent is speaking. Talk over it to interrupt.');
  } else if (call.muted) {
    setStatusQuietly("You're muted, so the agent can't hear you.");
  } else if (youLevel.speaking) {
    setStatusQuietly('Listening…');
  } else {
    setStatusQuietly('Your turn: say something.');
  }
}

function setStatusQuietly(text) {
  if (ui.status.textContent !== text) ui.status.textContent = text;
}

function startPolling() {
  const current = call;
  current.poll = setInterval(async () => {
    if (current.ending) return;
    let status;
    try {
      status = await api(`/api/sessions/${current.id}`, { key: current.key });
    } catch (error) {
      if (current.ending) return;
      if (error.status === 404) {
        endCall('The server no longer knows this session (did it restart?).', true, { serverEnded: true });
      } else if (++current.pollFailures >= 3) {
        endCall('Lost contact with the demo server.', true, { serverEnded: true });
      }
      return;
    }
    if (current.ending) return; // the user left while this request was in flight
    current.pollFailures = 0;
    ui.agentHearing.hidden = !status.agent.hearingUser;
    current.deadline = Date.now() + status.secondsRemaining * 1000;
    if (status.state === 'ended' || status.state === 'failed') {
      const message = END_MESSAGES[status.endReason] || 'The session ended.';
      const detail = status.error ? ` ${status.error}` : '';
      endCall(message + detail, status.state === 'failed', { serverEnded: true });
    }
  }, 2000);
}

function startCountdown() {
  const current = call;
  const tick = () => {
    const seconds = Math.max(0, Math.round((current.deadline - Date.now()) / 1000));
    ui.timeLeft.textContent = `Time left ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };
  tick();
  current.countdown = setInterval(tick, 1000);
}

function showCallUi() {
  ui.setup.hidden = true;
  ui.call.hidden = false;
  ui.publisher.hidden = !call.useCamera;
  ui.youPlaceholder.hidden = call.useCamera;
  ui.camera.hidden = !call.useCamera;
  ui.camera.textContent = 'Turn camera off';
  ui.mute.textContent = 'Mute';
  ui.mute.setAttribute('aria-pressed', 'false');
  ui.mutedBadge.hidden = true;
  ui.agentHearing.hidden = true;
}

async function start() {
  clearError();
  const problem = preflightProblem();
  if (problem) {
    showError(problem);
    return;
  }
  ui.start.disabled = true;
  call = {
    useCamera: ui.useCamera.checked,
    muted: false,
    ready: false,
    live: false,
    ending: false,
    pollFailures: 0,
  };
  youLevel.reset();
  agentLevel.reset();
  ui.transcriptLines.replaceChildren();
  ui.transcript.hidden = true;

  try {
    setStatus('Asking for microphone access…');
    call.publisher = await createPublisher(call.useCamera);

    setStatus('Starting a session…');
    const options = room.catalog && room.catalog.agentMode !== 'echo' ? currentOptions() : undefined;
    const created = await api('/api/sessions', { method: 'POST', body: options ? { options } : {} });
    Object.assign(call, { id: created.id, key: created.sessionKey, agentMode: created.agentMode });
    call.deadline = Date.now() + created.maxSessionSeconds * 1000;

    showCallUi();
    renderCallSummary(created.options);
    startPolling();
    startCountdown();
    await joinSession(created);
  } catch (error) {
    await endCall(error.message, true, { problems: error.problems || [] });
  }
}

async function endCall(message, isError = false, { serverEnded = false, problems = [] } = {}) {
  if (!call || call.ending) return;
  const ending = call;
  ending.ending = true;
  clearInterval(ending.poll);
  clearInterval(ending.countdown);
  clearInterval(ending.statusTimer);

  // Leave the Vonage session right away; stopping the agent can take a few seconds.
  try {
    ending.session?.disconnect();
  } catch {}
  try {
    ending.publisher?.destroy();
  } catch {}
  if (ending.id && !serverEnded) {
    setStatus('Ending the session…');
    try {
      await api(`/api/sessions/${ending.id}/end`, { method: 'POST', key: ending.key });
    } catch (error) {
      console.warn('[demo] Could not end the session cleanly:', error.message);
    }
  }

  call = null;
  ui.call.hidden = true;
  ui.setup.hidden = false;
  ui.start.disabled = false;
  ui.enableAudio.hidden = true;
  ui.timeLeft.textContent = '';
  setStatus(message);
  if (isError) showError(message, problems);
}

// --- Controls --------------------------------------------------------------------

ui.start.addEventListener('click', start);

ui.leave.addEventListener('click', () => endCall('You left the conversation.'));

ui.mute.addEventListener('click', () => {
  if (!call?.publisher) return;
  call.muted = !call.muted;
  call.publisher.publishAudio(!call.muted);
  ui.mute.textContent = call.muted ? 'Unmute' : 'Mute';
  ui.mute.setAttribute('aria-pressed', String(call.muted));
  ui.mutedBadge.hidden = !call.muted;
  renderLiveStatus();
});

ui.camera.addEventListener('click', () => {
  if (!call?.publisher) return;
  const on = ui.camera.textContent === 'Turn camera on';
  call.publisher.publishVideo(on);
  ui.camera.textContent = on ? 'Turn camera off' : 'Turn camera on';
});

ui.enableAudio.addEventListener('click', async () => {
  try {
    await OT.unblockAudio(); // must run inside a click handler
  } catch (error) {
    showError(`Could not enable audio: ${error.message}`);
  }
});

// Best effort: stop the agent if the tab closes. No navigation, no prompts.
window.addEventListener('pagehide', () => {
  if (call?.id && !call.ending) {
    api(`/api/sessions/${call.id}/end`, { method: 'POST', key: call.key, keepalive: true }).catch(
      () => {},
    );
  }
});

loadWaitingRoom();
