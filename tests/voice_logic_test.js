/* Node-run logic tests for the browser voice loop (issue B1).
 * Run automatically from tests/test_voice_logic.py via node, or manually:
 *   node tests/voice_logic_test.js
 */
'use strict';

const logic = require('../frontend/voice-logic.js');
let failures = 0;

function equal(actual, expected, label) {
  const pass = JSON.stringify(actual) === JSON.stringify(expected);
  if (!pass) {
    failures += 1;
    console.error(`FAIL ${label}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok - ${label}`);
  }
}

// --- Capability detection / fallback -------------------------------------

equal(
  logic.resolveCapability({
    recognitionSupported: false,
    synthesisSupported: false,
    microphoneDenied: false,
  }).mode,
  'text',
  'no speech APIs at all -> text mode');

equal(
  logic.resolveCapability({
    recognitionSupported: false,
    synthesisSupported: false,
    microphoneDenied: false,
  }).capable,
  false,
  'no speech APIs -> not voice capable');

equal(
  logic.resolveCapability({
    recognitionSupported: true,
    synthesisSupported: true,
    microphoneDenied: false,
  }).mode,
  'voice',
  'full support -> voice mode');

equal(
  logic.resolveCapability({
    recognitionSupported: true,
    synthesisSupported: true,
    microphoneDenied: true,
  }).mode,
  'text',
  'microphone denied -> text mode fallback');

const deniedStatus = logic.resolveCapability({
  recognitionSupported: true, synthesisSupported: true, microphoneDenied: true,
}).status;
if (!deniedStatus || !/allow the microphone/i.test(deniedStatus.detail)) {
  failures += 1;
  console.error('FAIL denied status must give actionable guidance');
} else {
  console.log('ok - microphone denial shows an actionable status');
}

// --- Stale-turn protection ------------------------------------------------

equal(logic.shouldSpeak(1, 1), true, 'current turn response may speak');
equal(logic.shouldSpeak(2, 1), false, 'late response must not speak over newer turn');
equal(logic.shouldSpeak(0, 0), true, 'first turn is the newest turn');

// --- State machine: recoverable outcomes ---------------------------------

equal(logic.nextVoiceState('listen/start', 'idle'), 'listening', 'idle -> listening');
equal(logic.nextVoiceState('listen/start', 'speaking'), 'listening', 'restart while speaking');
equal(logic.nextVoiceState('listen/recognized', 'listening'), 'transcribing', 'listening -> transcribing');
equal(logic.nextVoiceState('turn/submitted', 'transcribing'), 'thinking', 'transcribing -> thinking');
equal(logic.nextVoiceState('response/speaking', 'thinking'), 'speaking', 'thinking -> speaking');
equal(logic.nextVoiceState('speech/end', 'speaking'), 'idle', 'speaking -> idle');
for (const errorEvent of ['listen/error', 'synthesis/error', 'response/error',
                          'turn/timeout', 'control/stop']) {
  equal(logic.nextVoiceState(errorEvent, 'speaking'), 'idle',
        `${errorEvent} -> recoverable idle`);
  equal(logic.nextVoiceState(errorEvent, 'listening'), 'idle',
        `${errorEvent} while listening -> recoverable idle`);
}

// --- Error descriptions ---------------------------------------------------

for (const code of ['not-allowed', 'no-speech', 'network', 'audio-capture', 'unknown']) {
  const description = logic.describeError(code);
  if (typeof description !== 'string' || description.length < 10 ||
      !/text chat|try again|microphone/i.test(description)) {
    failures += 1;
    console.error(`FAIL describeError(${code}) must point to a usable fallback: ${description}`);
  } else {
    console.log(`ok - describeError(${code}) references the text/voice fallback`);
  }
}

// --- Unknown events never corrupt the state -------------------------------

equal(logic.nextVoiceState('nonsense', 'thinking'), 'thinking', 'unknown event is ignored');

process.exitCode = failures > 0 ? 1 : 0;
