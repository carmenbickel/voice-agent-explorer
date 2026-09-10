/* Pure voice-loop decision logic, shared between the browser and node tests:
 *      browser:  window.VoiceLogic
 *      node:     module.exports
 * Kept framework-free so the stale-turn and fallback rules can be
 * unit-tested without a browser.
 */
(function (exports) {
  'use strict';

  var VOICE_STATES = [
    'idle',         // nothing happening; text chat always usable here
    'listening',    // microphone open, waiting for the user to speak
    'transcribing', // speech recognized, message being handed to the chat
    'thinking',     // awaiting the model response
    'speaking',     // response is being read aloud
  ];

  function isVoiceCapable(api) {
    return Boolean(api.recognitionSupported && api.synthesisSupported);
  }

  /**
   * Decide the voice mode from feature detection and permission state.
   * Returns { mode, statusTitle, statusDetail } where mode is
   * "voice" | "text". In text mode, text chat stays fully usable and the
   * status explains how to regain voice.
   */
  function resolveCapability(api) {
    var status = null;
    var mode = 'voice';
    if (!api.recognitionSupported && !api.synthesisSupported) {
      mode = 'text';
      status = {
        title: 'Text chat',
        detail: 'This browser has no speech recognition or speech synthesis. You can still chat with text.',
      };
    } else if (!api.recognitionSupported) {
      mode = 'text';
      status = {
        title: 'Text chat',
        detail: 'This browser has no speech recognition. You can still chat with text and responses will be spoken.',
      };
    } else if (api.microphoneDenied) {
      mode = 'text';
      status = {
        title: 'Microphone blocked',
        detail: 'Microphone access is denied. Allow the microphone for this site to use voice, or keep using text chat.',
      };
    } else if (!api.synthesisSupported) {
      mode = 'text';
      status = {
        title: 'Text chat',
        detail: 'This browser cannot speak responses, so voice turns are unavailable. Text chat is ready.',
      };
    }
    return { mode: mode, status: status, capable: mode === 'voice' };
  }

  /**
   * Stale-turn protection: a response is only spoken when its turn is still
   * the newest turn the user started. Late responses never speak over a
   * newer turn; they are recorded in the transcript without speech.
   */
  function shouldSpeak(turnId, latestTurnId) {
    return turnId === latestTurnId;
  }

  /**
   * Voice UI state machine. Transitions are explicit; every error event
   * leads back to "idle" so the UI is always recoverable.
   */
  function nextVoiceState(event, state) {
    switch (event) {
      case 'listen/start':
        if (state === 'idle' || state === 'speaking') return 'listening';
        return state;
      case 'listen/recognized':
        if (state === 'listening') return 'transcribing';
        return state;
      case 'turn/submitted':
        if (state === 'transcribing' || state === 'idle') return 'thinking';
        return state;
      case 'response/speaking':
        if (state === 'thinking') return 'speaking';
        return state;
      // Any failure or explicit stop returns to a recoverable state.
      case 'listen/error':
      case 'synthesis/error':
      case 'response/error':
      case 'turn/timeout':
      case 'control/stop':
        return 'idle';
      case 'speech/end':
        if (state === 'speaking') return 'idle';
        return state;
      default:
        return state;
    }
  }

  /**
   * Mapping of recognition/error events to user-facing status text.
   * Text chat stays usable for every one of these outcomes.
   */
  function describeError(error) {
    switch (error) {
      case 'not-allowed':
      case 'service-not-allowed':
        return 'Microphone access was denied. Allow the microphone for this site to use voice, or keep using text chat.';
      case 'no-speech':
        return 'Nothing was heard. Check your microphone and try again, or type your question.';
      case 'network':
        return 'Speech recognition could not reach its service. You can keep using text chat.';
      case 'language-not-supported':
        return 'Speech recognition is not available for your language. Text chat is ready.';
      case 'aborted':
      case 'audio-capture':
        return 'Microphone capture stopped. You can try again or use text chat.';
      default:
        return 'Voice input failed. You can keep using text chat.';
    }
  }

  var exportsHere = {
    VOICE_STATES: VOICE_STATES,
    isVoiceCapable: isVoiceCapable,
    resolveCapability: resolveCapability,
    shouldSpeak: shouldSpeak,
    nextVoiceState: nextVoiceState,
    describeError: describeError,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exportsHere;
  } else {
    exports.voiceLogic = exportsHere;
  }
})(typeof module !== 'undefined' && typeof module.exports !== 'undefined'
  ? module : window);
