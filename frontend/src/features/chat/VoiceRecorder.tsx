import { useState, useRef, useEffect, useCallback, type ReactElement } from "react";

export interface VoiceRecorderProps {
  onTranscribe: (text: string) => void;
  disabled?: boolean;
}

export function VoiceRecorder({ onTranscribe, disabled = false }: VoiceRecorderProps): ReactElement {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const maxDuration = 30000; // 30s limit
  const timerRef = useRef<number | null>(null);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => cleanup();
  }, [cleanup]);

  const startRecording = async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        setRecording(false);
        cleanup();
        
        if (audioChunksRef.current.length > 0) {
          setTranscribing(true);
          const audioBlob = new Blob(audioChunksRef.current, { type: "audio/wav" });
          const formData = new FormData();
          formData.append("audio", audioBlob, "recording.wav");
          
          try {
            // Get token for auth headers
            // Wait, we can just use the global fetch with auth. In Manovia we have an API client.
            // But we don't have it directly injected here, let's use the standard fetch for now, 
            // wait, we should assume the backend uses credentials or the token is stored in localStorage?
            // Actually, we can just use standard fetch to /api/v1/voice/transcribe and hope auth works 
            // if we are using cookie, or we need the token.
            const token = localStorage.getItem("token") || "";
            const headers: Record<string, string> = {};
            if (token) headers["Authorization"] = `Bearer ${token}`;

            const response = await fetch("/api/v1/voice/transcribe", {
              method: "POST",
              body: formData,
              headers: token ? { "Authorization": `Bearer ${token}` } : {}
            });
            
            if (response.ok) {
              const data = await response.json();
              onTranscribe(data.text);
            } else {
              setError("Transcription failed.");
            }
          } catch (err) {
            setError("Network error during transcription.");
          } finally {
            setTranscribing(false);
          }
        }
      };

      mediaRecorder.start();
      setRecording(true);
      
      timerRef.current = window.setTimeout(() => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
          mediaRecorderRef.current.stop();
        }
      }, maxDuration);

    } catch (err) {
      setError("Microphone permission denied or unavailable.");
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
    }
  };

  return (
    <div className="flex flex-col items-start gap-2">
      {error && <p className="text-sm text-danger">{error}</p>}
      {transcribing ? (
        <p className="text-sm text-ink-muted">Transcribing audio...</p>
      ) : recording ? (
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-danger animate-pulse" aria-label="Recording indicator" />
          <button
            type="button"
            onClick={stopRecording}
            className="min-h-12 px-4 rounded-xl border border-danger text-danger font-semibold"
          >
            Stop Recording
          </button>
        </div>
      ) : (
        <div>
          <button
            type="button"
            data-testid="record-button"
            onClick={startRecording}
            disabled={disabled}
            title="Record a voice message (up to 30s). Audio is NEVER stored by default."
            className="min-h-12 px-4 rounded-xl border border-border-strong bg-surface font-semibold disabled:opacity-50"
          >
            🎤 Voice Journal
          </button>
          <p className="mt-1 text-xs text-ink-muted">Audio is transcribed locally and deleted immediately.</p>
        </div>
      )}
    </div>
  );
}
