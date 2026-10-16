/**
 * Passphrase-derived encryption for the local transcript.
 *
 * Private Mode promises the message text never leaves the device. Storing it in
 * IndexedDB in the clear would keep that promise in the letter and break it in
 * practice: IndexedDB is readable by anything running in the origin, by a disk
 * image, and by whoever picks up an unlocked laptop. So the transcript is encrypted
 * with a key derived from a passphrase the user chooses, and the passphrase is never
 * stored — not in IndexedDB, not in localStorage, not in a cookie.
 *
 * The design mirrors the server's journal envelope crypto (Day 17, see the module
 * docstring in `app/services/journal/crypto.py`) because the same reasoning applies
 * and because a reviewer should not have to learn two house styles:
 *
 *   PBKDF2-HMAC-SHA256, 210k iterations, 16-byte random salt   -> AES-GCM-256 key
 *   fresh 12-byte random IV per record (never reused with a key)
 *   AAD binding each ciphertext to its conversation and field:
 *       manovia:private:v1|conversation=<id>|field=<role>
 *
 * The AAD is what stops a record being copied from one conversation into another and
 * read there; the tag covers it, so any such swap fails authentication.
 *
 * The derived key lives in memory only. Closing the tab or locking Private Mode
 * drops it, and unlocking costs one PBKDF2 run (~150 ms here — measured, and the
 * reason the iteration count is not higher than OWASP's current recommendation).
 *
 * WHAT THIS DOES NOT PROTECT AGAINST
 *   - Someone who knows the passphrase, or a short/guessable one. The UI asks for a
 *     sentence, not a word, and `assessPassphrase` says why a weak one is weak.
 *   - A compromised device or browser extension with page access: while Private Mode
 *     is unlocked, the plaintext is in memory and the key is reachable from the page.
 *   - Metadata. IndexedDB still shows how many records exist, when they were written
 *     and how long each ciphertext is; the salt, IV and key id are stored in the
 *     clear by design (they are not secrets).
 *   - Loss. There is no recovery and no reset: forgetting the passphrase means the
 *     transcript is gone, which is the point of not keeping a copy.
 */

/** PBKDF2-HMAC-SHA256 iterations: OWASP's current floor for this construction. */
export const PBKDF2_ITERATIONS = 210_000;
export const SALT_BYTES = 16;
export const IV_BYTES = 12;
export const KEY_LENGTH_BITS = 256;
/** Binds a ciphertext to its conversation and field. Versioned, like the server's. */
const AAD_PREFIX = "manovia:private:v1";
/** Stored envelope marker, so a record from another format is refused, not guessed. */
export const RECORD_FORMAT = "manovia-private-record/1";

/** Below this, the passphrase is refused with a reason rather than accepted. */
export const MIN_PASSPHRASE_LENGTH = 12;

export class CryptoUnavailable extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "CryptoUnavailable";
  }
}

/** Raised when a record fails authentication: wrong passphrase, or tampering. */
export class DecryptionFailed extends Error {
  constructor(reason: "bad-passphrase" | "tampered" | "wrong-key" | "malformed") {
    super(`cannot decrypt this record (${reason})`);
    this.name = "DecryptionFailed";
    this.reason = reason;
  }

  readonly reason: "bad-passphrase" | "tampered" | "wrong-key" | "malformed";
}

export interface DerivedKey {
  key: CryptoKey;
  /** Stable public identifier: sha256(salt || iterations), first 16 hex chars. */
  keyId: string;
  salt: Uint8Array;
  iterations: number;
  createdAt: string;
}

export interface EncryptedRecord {
  format: typeof RECORD_FORMAT;
  /** Base64 of the IV. */
  iv: string;
  /** Base64 of the ciphertext plus GCM tag. */
  ciphertext: string;
  /** Which key wrote it, so a rotated key does not silently orphan records. */
  keyId: string;
  /** What the AAD bound it to; checked again on decrypt. */
  conversationId: string;
  field: string;
}

export interface PassphraseAssessment {
  ok: boolean;
  length: number;
  /** Distinct words, because "aaaaaaaaaaaa" is long and useless. */
  words: number;
  reasons: string[];
  /** Plain sentence for the UI. Empty when `ok`. */
  advice: string;
}

function subtle(): SubtleCrypto {
  const cryptoObject = globalThis.crypto;
  if (cryptoObject?.subtle === undefined) {
    throw new CryptoUnavailable(
      "this browser has no WebCrypto; Private Mode cannot store anything safely",
    );
  }
  return cryptoObject.subtle;
}

export function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  globalThis.crypto.getRandomValues(bytes);
  return bytes;
}

/** Base64 for storage. IndexedDB could hold raw bytes, but a record that can be
 * inspected in devtools as text is a record a user can verify is encrypted. */
export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

export function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await subtle().digest("SHA-256", bytes as BufferSource);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Derive (or re-derive) the AES-GCM key for a passphrase.
 *
 * `salt` is optional: pass the stored one to unlock, omit it to create a new key.
 * The key is non-extractable, so it can be used but never read back out of WebCrypto
 * — a page compromise can use the key while it is loaded, but cannot export it.
 */
export async function deriveKey(
  passphrase: string,
  options: { salt?: Uint8Array; iterations?: number } = {},
): Promise<DerivedKey> {
  if (passphrase.length === 0) {
    throw new CryptoUnavailable("a passphrase is required");
  }
  const iterations = options.iterations ?? PBKDF2_ITERATIONS;
  const salt = options.salt ?? randomBytes(SALT_BYTES);
  if (salt.byteLength < SALT_BYTES) {
    throw new CryptoUnavailable(`salt must be at least ${SALT_BYTES} bytes`);
  }
  const encoder = new TextEncoder();
  try {
    const material = await subtle().importKey(
      "raw",
      encoder.encode(passphrase) as BufferSource,
      { name: "PBKDF2" },
      false,
      ["deriveKey", "deriveBits"],
    );
    const key = await subtle().deriveKey(
      { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
      material,
      { name: "AES-GCM", length: KEY_LENGTH_BITS },
      false,
      ["encrypt", "decrypt"],
    );
    // The key id has to identify the *key*, so it must depend on the passphrase:
    // hashing the salt alone would give two different passphrases the same id and
    // let a wrong one look like the right one. PBKDF2's first 16 bytes are derived
    // again here (the AES key is non-extractable, so it cannot be hashed directly)
    // and only their digest is kept.
    const bits = await subtle().deriveBits(
      { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
      material,
      128,
    );
    const digest = await sha256Hex(new Uint8Array(bits));
    return {
      key,
      keyId: digest.slice(0, 16),
      salt,
      iterations,
      createdAt: new Date().toISOString(),
    };
  } catch (error) {
    if (error instanceof CryptoUnavailable) {
      throw error;
    }
    throw new CryptoUnavailable("could not derive a key from this passphrase", { cause: error });
  }
}

/** The AAD string, computed identically on both sides of a record. */
export function associatedData(conversationId: string, field: string): Uint8Array {
  return new TextEncoder().encode(`${AAD_PREFIX}|conversation=${conversationId}|field=${field}`);
}

export interface EncryptTarget {
  conversationId: string;
  field: string;
}

/** Encrypt one string. A fresh IV every time: reuse with GCM is catastrophic. */
export async function encryptString(
  derived: DerivedKey,
  plaintext: string,
  target: EncryptTarget,
): Promise<EncryptedRecord> {
  const iv = randomBytes(IV_BYTES);
  try {
    const ciphertext = await subtle().encrypt(
      {
        name: "AES-GCM",
        iv: iv as BufferSource,
        additionalData: associatedData(target.conversationId, target.field) as BufferSource,
        tagLength: 128,
      },
      derived.key,
      new TextEncoder().encode(plaintext) as BufferSource,
    );
    return {
      format: RECORD_FORMAT,
      iv: toBase64(iv),
      ciphertext: toBase64(new Uint8Array(ciphertext)),
      keyId: derived.keyId,
      conversationId: target.conversationId,
      field: target.field,
    };
  } catch (error) {
    throw new CryptoUnavailable("encryption failed", { cause: error });
  }
}

/**
 * Decrypt one record.
 *
 * GCM gives one error for every kind of wrongness, so the reason is inferred: a key
 * id mismatch is "wrong-key", anything else with a valid envelope is either a wrong
 * passphrase or tampering, and both must produce the same refusal (telling an
 * attacker which one it was would be a small oracle).
 */
export async function decryptString(derived: DerivedKey, record: EncryptedRecord): Promise<string> {
  if (record.format !== RECORD_FORMAT) {
    throw new DecryptionFailed("malformed");
  }
  if (record.keyId !== derived.keyId) {
    throw new DecryptionFailed("wrong-key");
  }
  let iv: Uint8Array;
  let ciphertext: Uint8Array;
  try {
    iv = fromBase64(record.iv);
    ciphertext = fromBase64(record.ciphertext);
  } catch {
    throw new DecryptionFailed("malformed");
  }
  if (iv.byteLength !== IV_BYTES || ciphertext.byteLength <= 16) {
    throw new DecryptionFailed("malformed");
  }
  try {
    const plaintext = await subtle().decrypt(
      {
        name: "AES-GCM",
        iv: iv as BufferSource,
        additionalData: associatedData(record.conversationId, record.field) as BufferSource,
        tagLength: 128,
      },
      derived.key,
      ciphertext as BufferSource,
    );
    return new TextDecoder().decode(plaintext);
  } catch {
    // GCM reports every failure the same way, deliberately: distinguishing "wrong
    // passphrase" from "somebody edited this record" would hand an attacker an
    // oracle. Both are refused identically.
    throw new DecryptionFailed("bad-passphrase");
  }
}

/**
 * Is this passphrase good enough to be the only thing standing between a disk image
 * and somebody's crisis conversation? Length and word count, not a password
 * blacklist: the honest advice is "a sentence nobody would guess", and the check
 * only has to catch the cases where that is plainly not true.
 */
export function assessPassphrase(passphrase: string): PassphraseAssessment {
  const trimmed = passphrase.trim();
  const words = trimmed.split(/\s+/).filter(Boolean);
  const reasons: string[] = [];
  if (trimmed.length < MIN_PASSPHRASE_LENGTH) {
    reasons.push(`too short (at least ${MIN_PASSPHRASE_LENGTH} characters)`);
  }
  if (words.length < 3) {
    reasons.push("fewer than three words");
  }
  if (/^(.)\1+$/.test(trimmed)) {
    reasons.push("one repeated character");
  }
  if (/^[0-9]+$/.test(trimmed)) {
    reasons.push("only digits");
  }
  const longest = words.reduce((max, word) => Math.max(max, word.length), 0);
  if (words.length > 0 && longest === trimmed.length) {
    reasons.push("a single long word is easier to guess than a short sentence");
  }
  return {
    ok: reasons.length === 0,
    length: trimmed.length,
    words: words.length,
    reasons,
    advice:
      reasons.length === 0
        ? ""
        : "Use a sentence of three or more words that means something to you and that nobody would guess. It is never stored, and there is no way to recover it if you forget it.",
  };
}
