import argparse
import json
import jiwer
from faster_whisper import WhisperModel

def main():
    parser = argparse.ArgumentParser(description="Evaluate STT Word Error Rate")
    parser.add_argument("--dataset", required=True, help="Path to JSONL dataset with audio_path and reference_text")
    parser.add_argument("--model-size", default="tiny", help="Whisper model size")
    args = parser.parse_args()

    model = WhisperModel(args.model_size, device="cpu", compute_type="int8")
    
    predictions = []
    references = []

    with open(args.dataset, "r") as f:
        for line in f:
            item = json.loads(line)
            audio_path = item["audio_path"]
            reference = item["reference_text"]

            segments, _ = model.transcribe(audio_path, beam_size=5)
            text = " ".join([segment.text for segment in segments]).strip()
            
            predictions.append(text.lower())
            references.append(reference.lower())

    wer = jiwer.wer(references, predictions)
    print(f"Evaluated {len(references)} utterances.")
    print(f"Word Error Rate (WER) using {args.model_size} model: {wer:.4f}")

if __name__ == "__main__":
    main()
