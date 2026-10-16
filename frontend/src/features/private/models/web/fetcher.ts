/**
 * The browser side of a model download: ranged fetches, real hashing, real free space.
 *
 * This is the only production implementation of the interfaces the download manager
 * is written against, and it lives in `models/web/` with the other runtime adapters
 * so that Private Mode's core (rules, crisis, session, store, crypto) contains no
 * network call at all — `private-session-no-network.test.ts` asserts that by reading
 * the source.
 *
 * Three details matter:
 *
 *   Range requests. A resume asks for `bytes=<stored>-`. A server that answers 200
 *   instead of 206 has ignored the range, and the manager is told (`resumed: false`)
 *   so it restarts the file rather than appending a second copy to the first.
 *   Streaming. Chunks are handed to the sink as they arrive, so a 26 MB classifier
 *   never sits in memory twice and progress is real rather than estimated.
 *   Hashing. Real `crypto.subtle` SHA-256 over the stored bytes. Nothing here trusts
 *   a `Content-MD5` header or a CDN's word.
 */

import type {
  FetchOptions,
  FetchOutcome,
  Hasher,
  ModelSink,
  RemoteFetcher,
} from "../downloadManager";

/** Production hasher: WebCrypto over the bytes actually stored. */
export class WebCryptoHasher implements Hasher {
  async sha256(bytes: Uint8Array): Promise<string> {
    const subtle = globalThis.crypto?.subtle;
    if (subtle === undefined) {
      throw new Error("this browser has no WebCrypto, so a download cannot be verified");
    }
    const digest = await subtle.digest("SHA-256", bytes as BufferSource);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }
}

export interface HttpFetcherOptions {
  fetchImpl?: typeof fetch;
  /** Chunk size handed to the sink. 4 MB matches the IndexedDB append granularity. */
  chunkBytes?: number;
}

/**
 * Fetch over HTTP(S) with resume support.
 *
 * `credentials: "omit"` and no cookies: a model download must not carry session
 * state to a third-party host, and the artifacts are public anyway.
 */
export class HttpFetcher implements RemoteFetcher {
  private readonly fetchImpl: typeof fetch;
  private readonly chunkBytes: number;

  constructor(options: HttpFetcherOptions = {}) {
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.chunkBytes = options.chunkBytes ?? 4 * 1024 * 1024;
  }

  async get(url: string, options: FetchOptions): Promise<FetchOutcome> {
    const headers: Record<string, string> = { accept: "*/*" };
    const start = options.rangeStart ?? 0;
    if (start > 0) {
      headers.range = `bytes=${start}-`;
    }
    const response = await this.fetchImpl(url, {
      headers,
      credentials: "omit",
      cache: "no-store",
      signal: options.signal,
    });
    if (!response.ok && response.status !== 206) {
      return {
        status: response.status,
        bytesReceived: 0,
        resumed: false,
        totalBytes: contentLength(response),
      };
    }
    const resumed = response.status === 206;
    const body = response.body;
    if (body === null) {
      // No streaming body (an older polyfill, or an opaque response): fall back to
      // one buffer, which is fine at classifier scale and still verified by hash.
      const buffer = new Uint8Array(await response.arrayBuffer());
      // A 200 after a range request means the whole file arrived: the stored prefix
      // must be discarded, which `resumed: false` tells the manager to do.
      await options.onChunk(buffer, resumed ? start : 0);
      return {
        status: response.status,
        bytesReceived: buffer.byteLength,
        resumed,
        totalBytes: contentLength(response),
      };
    }

    const reader = body.getReader();
    let cursor = resumed ? start : 0;
    let delivered = 0;
    let pending: Uint8Array = new Uint8Array(0);
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        if (options.signal?.aborted) {
          break;
        }
        const chunk = value === undefined ? new Uint8Array(0) : new Uint8Array(value);
        delivered += chunk.byteLength;
        // Hand the sink chunks it can write in one transaction, not one per packet.
        pending = concat(pending, chunk);
        if (pending.byteLength < this.chunkBytes) {
          continue;
        }
        await options.onChunk(pending, cursor);
        cursor += pending.byteLength;
        pending = new Uint8Array(0);
      }
      if (pending.byteLength > 0) {
        await options.onChunk(pending, cursor);
        cursor += pending.byteLength;
      }
    } finally {
      reader.releaseLock();
    }
    return {
      status: response.status,
      bytesReceived: delivered,
      resumed,
      totalBytes: contentLength(response),
    };
  }
}

function contentLength(response: Response): number | null {
  const header =
    response.headers?.get?.("content-range") ?? response.headers?.get?.("content-length");
  if (header === null || header === undefined) {
    return null;
  }
  // `bytes 1000-2000/26000` for a range response, a plain number otherwise.
  const total = header.includes("/") ? header.split("/")[1] : header;
  const parsed = Number.parseInt(total ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function concat(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (left.byteLength === 0) {
    return right;
  }
  const joined = new Uint8Array(left.byteLength + right.byteLength);
  joined.set(left, 0);
  joined.set(right, left.byteLength);
  return joined;
}

/** Free bytes for the capability check and the download manager's space guard. */
export async function freeStorageBytes(): Promise<number | null> {
  const estimate = globalThis.navigator?.storage?.estimate;
  if (typeof estimate !== "function") {
    return null;
  }
  try {
    const result = await estimate.call(globalThis.navigator.storage);
    if (typeof result.quota !== "number" || typeof result.usage !== "number") {
      return null;
    }
    return Math.max(0, result.quota - result.usage);
  } catch {
    return null;
  }
}

/** Bytes currently held for models, read back from the sink. */
export async function modelUsageBytes(sink: ModelSink): Promise<number> {
  return sink.usageBytes();
}
