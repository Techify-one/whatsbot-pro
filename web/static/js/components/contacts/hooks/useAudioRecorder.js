// @ts-check
//
// Audio-recorder hook (Plano 23 · D3) — extracted verbatim from ContactDetail.js.
// Owns the opus-recorder lifecycle (start/stop), the recording flag and the
// elapsed-seconds counter. On a non-empty recording it hands the produced
// OGG/Opus blob to `onRecorded` (the composer turns it into a pending audio
// media item).
//
// Behavior-preserving: same window.Recorder config (VOIP / 48kHz / mono), same
// guard messages, same timer interval, same cleanup.
import { useState, useRef } from 'preact/hooks';

/**
 * @param {Object} opts
 * @param {(item:{type:'audio', blob:Blob, filename:string, previewUrl:string})=>void} opts.onRecorded
 */
export function useAudioRecorder({ onRecorded }) {
  const [recording, setRecording] = useState(false);
  const [recordDuration, setRecordDuration] = useState(0);
  const mediaRecorderRef = useRef(null);
  const recordTimerRef = useRef(null);

  async function handleMicClick() {
    if (recording) {
      // Stop recording
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    // Start recording — uses opus-recorder to produce real OGG/Opus accepted by WhatsApp
    if (typeof window.Recorder !== 'function') {
      alert('Gravador de áudio indisponível: a biblioteca opus-recorder não foi carregada. Recarregue a página (Ctrl+F5) e tente novamente.');
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert('Seu navegador não permite acesso ao microfone neste contexto. Abra o WhatsBot-Pro via HTTPS (ou http://localhost) para gravar áudios.');
      return;
    }
    try {
      const recorder = new window.Recorder({
        encoderPath: '/static/vendor/opus-recorder/encoderWorker.min.js',
        encoderApplication: 2048, // VOIP
        encoderSampleRate: 48000,
        numberOfChannels: 1,
      });
      mediaRecorderRef.current = recorder;

      recorder.onstart = () => {
        setRecording(true);
        setRecordDuration(0);
        recordTimerRef.current = setInterval(() => setRecordDuration(d => d + 1), 1000);
      };

      recorder.ondataavailable = (blob) => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);

        if (!blob || blob.size === 0) return;

        const audioBlob = new Blob([blob], { type: 'audio/ogg' });
        const previewUrl = URL.createObjectURL(audioBlob);
        onRecorded({ type: 'audio', blob: audioBlob, filename: 'voice.ogg', previewUrl });
      };

      recorder.onstop = () => {
        setRecording(false);
        clearInterval(recordTimerRef.current);
        setRecordDuration(0);
      };

      await recorder.start();
    } catch (err) {
      console.error('Microphone access error:', err);
      setRecording(false);
      clearInterval(recordTimerRef.current);
      setRecordDuration(0);
      const msg = (err && err.name === 'NotAllowedError')
        ? 'Permissão para o microfone foi negada. Habilite o acesso nas configurações do navegador.'
        : `Não foi possível iniciar a gravação: ${err && err.message ? err.message : err}`;
      alert(msg);
    }
  }

  return { recording, recordDuration, handleMicClick };
}
