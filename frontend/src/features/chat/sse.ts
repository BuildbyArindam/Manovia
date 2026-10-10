/**
 * A Server-Sent Events parser, one frame at a time.
 *
 * The browser cannot use `EventSource` here: the stream is a `POST` with an
 * `Authorization` header, and `EventSource` can do neither. So the response
 * body is read as a `ReadableStream` and fed to this parser in whatever chunks
 * the network happens to deliver. That is the whole reason it exists in this
 * shape — a chunk boundary can fall **anywhere**, including in the middle of
 * `event:`, and a proxy is free to batch twelve frames into one read.
 *
 * Implements the parts of the SSE grammar this backend uses: `event:` and
 * `data:` fields, multi-line `data` (joined with `\n`), `:` comments, and the
 * blank line that dispatches a frame. `id:` and `retry:` are accepted and
 * ignored — the client does not resume a dropped stream, it says so and offers
 * a retry instead (a half-delivered safety reply must not be silently resumed).
 */

/** One dispatched frame: the event name and its (possibly multi-line) data. */
export interface SseFrame {
  event: string;
  data: string;
}

export interface SseParser {
  /** Feed one decoded chunk; returns every frame it completed. */
  push(chunk: string): SseFrame[];
  /**
   * Return a trailing frame with no final blank line, if any.
   *
   * A server that closes cleanly always ends with a blank line, so this returns
   * `[]`. It matters for the dropped-connection case: whatever was fully
   * written before the socket died is still delivered rather than discarded.
   */
  flush(): SseFrame[];
}

const DEFAULT_EVENT = "message";

export function createSseParser(): SseParser {
  let buffer = "";
  let eventName: string | null = null;
  const dataLines: string[] = [];

  function takeFrame(): SseFrame | null {
    if (dataLines.length === 0 && eventName === null) {
      return null;
    }
    const frame: SseFrame = {
      event: eventName ?? DEFAULT_EVENT,
      data: dataLines.join("\n"),
    };
    eventName = null;
    dataLines.length = 0;
    return frame;
  }

  function handleLine(line: string): SseFrame | null {
    if (line === "") {
      return takeFrame();
    }
    // A comment (keep-alive). Never part of a frame.
    if (line.startsWith(":")) {
      return null;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    // Exactly one leading space after the colon is part of the syntax.
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    switch (field) {
      case "event":
        eventName = value;
        return null;
      case "data":
        dataLines.push(value);
        return null;
      default:
        // `id`, `retry`, or anything a future server adds: accept and ignore.
        return null;
    }
  }

  return {
    push(chunk: string): SseFrame[] {
      buffer += chunk;
      const frames: SseFrame[] = [];
      let lineBreak = nextLineBreak(buffer);
      // Split on \n, \r\n and a lone \r, per the grammar.
      while (lineBreak !== null) {
        const line = buffer.slice(0, lineBreak.position);
        buffer = buffer.slice(lineBreak.position + lineBreak.length);
        const frame = handleLine(line);
        if (frame !== null) {
          frames.push(frame);
        }
        lineBreak = nextLineBreak(buffer);
      }
      return frames;
    },

    flush(): SseFrame[] {
      const frames: SseFrame[] = [];
      if (buffer !== "") {
        const frame = handleLine(buffer);
        buffer = "";
        if (frame !== null) {
          frames.push(frame);
        }
      }
      const last = takeFrame();
      if (last !== null) {
        frames.push(last);
      }
      return frames;
    },
  };
}

interface LineBreak {
  position: number;
  length: number;
}

function nextLineBreak(text: string): LineBreak | null {
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "\n") {
      return { position: index, length: 1 };
    }
    if (char === "\r") {
      return { position: index, length: text[index + 1] === "\n" ? 2 : 1 };
    }
  }
  return null;
}
