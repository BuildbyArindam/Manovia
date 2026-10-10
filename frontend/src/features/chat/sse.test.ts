/**
 * The SSE parser: the part of the streaming path where a chunk boundary can
 * fall anywhere, including inside a field name.
 */

import { describe, expect, it } from "vitest";

import { createSseParser } from "./sse";

describe("createSseParser", () => {
  it("dispatches a frame only on the blank line", () => {
    const parser = createSseParser();

    // The newline that *ends* the data line is not the blank line that ends the
    // frame; a proxy may well deliver them separately.
    expect(parser.push('event: token\ndata: {"text":"hi"}\n')).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "token", data: '{"text":"hi"}' }]);
  });

  it("parses several frames delivered in one chunk", () => {
    const parser = createSseParser();
    const frames = parser.push(
      'event: token\ndata: {"text":"a"}\n\nevent: token\ndata: {"text":"b"}\n\n',
    );

    expect(frames).toEqual([
      { event: "token", data: '{"text":"a"}' },
      { event: "token", data: '{"text":"b"}' },
    ]);
  });

  it("rejoins a frame split across chunks mid-field-name", () => {
    const parser = createSseParser();

    expect(parser.push("ev")).toEqual([]);
    expect(parser.push("ent: to")).toEqual([]);
    expect(parser.push("ken\nda")).toEqual([]);
    expect(parser.push('ta: {"text":"he')).toEqual([]);

    expect(parser.push('llo"}\n\n')).toEqual([{ event: "token", data: '{"text":"hello"}' }]);
  });

  it("defaults the event name to message", () => {
    const parser = createSseParser();
    expect(parser.push("data: plain\n\n")).toEqual([{ event: "message", data: "plain" }]);
  });

  it("joins multi-line data with a newline", () => {
    const parser = createSseParser();
    expect(parser.push("data: line one\ndata: line two\n\n")).toEqual([
      { event: "message", data: "line one\nline two" },
    ]);
  });

  it("strips exactly one leading space after the colon", () => {
    const parser = createSseParser();
    expect(parser.push("data:  two spaces\n\n")).toEqual([
      { event: "message", data: " two spaces" },
    ]);
  });

  it("ignores comments and unknown fields", () => {
    const parser = createSseParser();
    const frames = parser.push(": keep-alive\nid: 42\nretry: 1000\nevent: token\ndata: x\n\n");

    expect(frames).toEqual([{ event: "token", data: "x" }]);
  });

  it("handles CRLF and a lone CR as line breaks", () => {
    const parser = createSseParser();

    expect(parser.push("event: token\r\ndata: a\r\n\r\n")).toEqual([{ event: "token", data: "a" }]);
    expect(parser.push("event: token\rdata: b\r\r")).toEqual([{ event: "token", data: "b" }]);
  });

  it("delivers a trailing frame on flush when the socket died first", () => {
    const parser = createSseParser();
    parser.push('event: token\ndata: {"text":"partial"}');

    expect(parser.flush()).toEqual([{ event: "token", data: '{"text":"partial"}' }]);
    // And nothing is delivered twice.
    expect(parser.flush()).toEqual([]);
  });

  it("flushes nothing when the stream ended cleanly", () => {
    const parser = createSseParser();
    parser.push("event: token\ndata: x\n\n");

    expect(parser.flush()).toEqual([]);
  });

  it("keeps unicode intact across a chunk boundary", () => {
    const parser = createSseParser();
    const encoder = new TextEncoder();
    const decoder = new TextDecoder();
    const bytes = encoder.encode('event: token\ndata: {"text":"मैं ठीक हूँ"}\n\n');
    const cut = bytes.length - 8;

    expect(parser.push(decoder.decode(bytes.subarray(0, cut), { stream: true }))).toEqual([]);
    expect(parser.push(decoder.decode(bytes.subarray(cut), { stream: true }))).toEqual([
      { event: "token", data: '{"text":"मैं ठीक हूँ"}' },
    ]);
  });
});
