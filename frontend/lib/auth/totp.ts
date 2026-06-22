/**
 * TOTP utility functions for frontend 2FA support.
 * Uses Web Crypto API (no extra dependencies).
 */

/**
 * Generate a cryptographically random TOTP secret.
 * Returns a base32-encoded string (16 bytes → 32 chars with padding).
 */
export function generateTOTPSecret(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return base32Encode(bytes);
}

/**
 * Base32 encode (RFC 4648 with padding).
 */
function base32Encode(bytes: Uint8Array): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let result = '';
  let buffer = 0;
  let bits = 0;

  for (const byte of bytes) {
    buffer = (buffer << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      result += alphabet[(buffer >> bits) & 0x1f];
    }
  }
  if (bits > 0) {
    result += alphabet[(buffer << (5 - bits)) & 0x1f];
  }

  // Pad to multiple of 8
  while (result.length % 8 !== 0) {
    result += '=';
  }
  return result;
}

/**
 * Generate `count` unique 8-digit backup codes.
 * Uses crypto.getRandomValues for randomness.
 */
export function generateBackupCodes(count: number): string[] {
  if (count <= 0) {
    return [];
  }

  const codes: string[] = [];
  const seen = new Set<string>();

  while (seen.size < count) {
    const code = generateSingleCode();
    if (!seen.has(code)) {
      seen.add(code);
      codes.push(code);
    }
  }

  return codes;
}

function generateSingleCode(): string {
  const bytes = new Uint32Array(1);
  crypto.getRandomValues(bytes);
  const value = bytes[0];
  const code = 10_000_000 + (value % 90_000_000);
  return code.toString().padStart(8, '0');
}

/**
 * Base32 decode (RFC 4648).
 * Strips padding, decodes back to bytes.
 */
function base32Decode(input: string): Uint8Array {
  const cleaned = input.replace(/=+$/, '').toUpperCase();
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const output: number[] = [];
  let buffer = 0;
  let bits = 0;

  for (const ch of cleaned) {
    const idx = alphabet.indexOf(ch);
    if (idx === -1) {
      throw new Error('Invalid base32 character');
    }
    buffer = (buffer << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      output.push((buffer >> bits) & 0xff);
    }
  }

  return new Uint8Array(output);
}

/**
 * Validate a TOTP code for the given base32-encoded secret.
 * Checks the current 30-second time window and +/- 1 adjacent window.
 * Uses constant-time comparison to prevent timing attacks.
 *
 * Returns false for empty secret, non-6-digit codes, or invalid codes.
 */
export async function validateTOTP(secret: string, code: string): Promise<boolean> {
  // Reject empty secret
  if (!secret || secret.trim() === '') {
    return false;
  }

  // Code must be exactly 6 digits
  if (!/^\d{6}$/.test(code)) {
    return false;
  }

  // Decode base32 secret
  let decodedSecret: Uint8Array;
  try {
    decodedSecret = base32Decode(secret);
  } catch {
    return false;
  }

  if (decodedSecret.length === 0) {
    return false;
  }

  // Get current time in seconds
  const now = Math.floor(Date.now() / 1000);
  const timeStep = 30;
  const counter = Math.floor(now / timeStep);

  // Check current time window and +/- 1 adjacent window
  for (let offset = -1; offset <= 1; offset++) {
    const expectedCode = await generateTOTPCode(decodedSecret, counter + offset);
    if (constantTimeEqual(code, expectedCode)) {
      return true;
    }
  }

  return false;
}

/**
 * Generate a TOTP code for a given secret and counter value per RFC 4226.
 */
async function generateTOTPCode(secret: Uint8Array, counter: number): Promise<string> {
  // Pack counter into 8 bytes big-endian
  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setBigUint64(0, BigInt(counter), false);

  // Import key for HMAC-SHA1
  const key = await crypto.subtle.importKey(
    'raw',
    secret as BufferSource,
    { name: 'HMAC', hash: 'SHA-1' },
    false,
    ['sign'],
  );

  const signature = await crypto.subtle.sign('HMAC', key, buf);
  const hmac = new Uint8Array(signature);

  // Dynamic truncation per RFC 4226 Section 5.3
  const offset = hmac[hmac.length - 1] & 0x0f;
  const binaryCode =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff);

  const totpValue = binaryCode % 1000000;
  return totpValue.toString().padStart(6, '0');
}

/**
 * Constant-time string comparison to prevent timing attacks.
 * Processes all characters regardless of match to avoid early exit.
 */
function constantTimeEqual(a: string, b: string): boolean {
  let result = 0;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    result |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return result === 0;
}
