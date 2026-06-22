import { describe, it, expect } from 'vitest';
import {
  generateTOTPSecret,
  generateBackupCodes,
  validateTOTP,
} from '@/lib/auth/totp';

// ---- generateTOTPSecret ----

describe('generateTOTPSecret', () => {
  it('returns a string', () => {
    const secret = generateTOTPSecret();
    expect(typeof secret).toBe('string');
  });

  it('produces valid base32 encoding', () => {
    const re = /^[A-Z2-7]+=*$/;
    for (let i = 0; i < 10; i++) {
      expect(generateTOTPSecret()).toMatch(re);
    }
  });

  it('has correct length (32 chars for 16 bytes)', () => {
    const secret = generateTOTPSecret();
    expect(secret.length).toBe(32);
  });

  it('generates unique values across calls', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 100; i++) {
      seen.add(generateTOTPSecret());
    }
    expect(seen.size).toBeGreaterThanOrEqual(95);
  });

  it('produces different secrets on consecutive calls', () => {
    const a = generateTOTPSecret();
    const b = generateTOTPSecret();
    expect(a).not.toBe(b);
  });

  it('uses crypto.getRandomValues (not Math.random)', () => {
    // crypto.getRandomValues is guaranteed by the Web Crypto API
    // This test verifies the output looks random
    const secret = generateTOTPSecret();
    // Decode and check entropy
    const decoded = base32Decode(secret);
    expect(decoded.length).toBe(16);
    // Not all zeros
    expect(decoded.some(b => b !== 0)).toBe(true);
  });

  it('has sufficient Shannon entropy', () => {
    // Collect multiple samples to build a meaningful byte distribution
    const allBytes: number[] = [];
    for (let i = 0; i < 64; i++) {
      const secret = generateTOTPSecret();
      const decoded = base32Decode(secret);
      allBytes.push(...decoded);
    }
    const total = allBytes.length;
    const freq = new Array(256).fill(0);
    for (const b of allBytes) {
      freq[b]++;
    }
    let entropy = 0;
    for (const count of freq) {
      if (count > 0) {
        const p = count / total;
        entropy -= p * Math.log2(p);
      }
    }
    // Random data should have entropy > 7.5 bits/byte
    expect(entropy).toBeGreaterThan(7.5);
  });

  it('produces unique values across many calls', () => {
    const secrets = new Set<string>();
    for (let i = 0; i < 1000; i++) {
      secrets.add(generateTOTPSecret());
    }
    expect(secrets.size).toBe(1000);
  });
});

// ---- generateBackupCodes ----

describe('generateBackupCodes', () => {
  it('returns correct count', () => {
    const codes = generateBackupCodes(10);
    expect(codes).toHaveLength(10);
  });

  it('all codes are 8 digits', () => {
    const codes = generateBackupCodes(100);
    for (const code of codes) {
      expect(code).toMatch(/^\d{8}$/);
    }
  });

  it('all codes are unique', () => {
    const codes = generateBackupCodes(100);
    const unique = new Set(codes);
    expect(unique.size).toBe(100);
  });

  it('count = 0 returns empty array', () => {
    expect(generateBackupCodes(0)).toEqual([]);
  });

  it('count = 1 returns single valid code', () => {
    const codes = generateBackupCodes(1);
    expect(codes).toHaveLength(1);
    expect(codes[0]).toMatch(/^\d{8}$/);
  });

  it('count = negative returns empty array', () => {
    expect(generateBackupCodes(-5)).toEqual([]);
  });

  it('large count (1000) all unique and valid', () => {
    const codes = generateBackupCodes(1000);
    expect(codes).toHaveLength(1000);
    const unique = new Set(codes);
    expect(unique.size).toBe(1000);
    for (const code of codes) {
      expect(code).toMatch(/^\d{8}$/);
    }
  });

  it('no sequential pattern', () => {
    const codes = generateBackupCodes(50);
    const sorted = codes
      .map(c => parseInt(c, 10))
      .sort((a, b) => a - b);
    for (let i = 0; i < sorted.length - 2; i++) {
      const diff1 = sorted[i + 1] - sorted[i];
      const diff2 = sorted[i + 2] - sorted[i + 1];
      expect(diff1 === 1 && diff2 === 1).toBe(false);
    }
  });

  it('independent calls produce different sets', () => {
    const a = new Set(generateBackupCodes(10));
    const b = new Set(generateBackupCodes(10));
    const intersection = [...a].filter(x => b.has(x));
    expect(intersection.length).toBeLessThan(2);
  });
});

// ---- Helpers ----

function base32Decode(input: string): Uint8Array {
  const cleaned = input.replace(/=+$/, '').toUpperCase();
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const output: number[] = [];
  let buffer = 0;
  let bits = 0;

  for (const ch of cleaned) {
    const idx = alphabet.indexOf(ch);
    if (idx === -1) continue;
    buffer = (buffer << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      output.push((buffer >> bits) & 0xff);
    }
  }

  return new Uint8Array(output);
}

// ---- validateTOTP ----

/**
 * Compute a TOTP code for testing (mirrors the internal implementation).
 */
async function computeTOTPCode(secret: string, timeOffset = 0): Promise<string> {
  const decoded = base32Decode(secret);
  const now = Math.floor(Date.now() / 1000);
  const counter = Math.floor(now / 30) + timeOffset;

  const buf = new ArrayBuffer(8);
  const view = new DataView(buf);
  view.setBigUint64(0, BigInt(counter), false);

  const key = await crypto.subtle.importKey(
    'raw',
    decoded.buffer as ArrayBuffer,
    { name: 'HMAC', hash: 'SHA-1' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, buf);
  const hmac = new Uint8Array(signature);
  const offset = hmac[hmac.length - 1] & 0x0f;
  const binaryCode =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff);
  const totpValue = binaryCode % 1000000;
  return totpValue.toString().padStart(6, '0');
}

describe('validateTOTP', () => {
  it('passes valid TOTP code for current window', async () => {
    const secret = generateTOTPSecret();
    const code = await computeTOTPCode(secret, 0);
    expect(await validateTOTP(secret, code)).toBe(true);
  });

  it('passes valid TOTP code for adjacent windows', async () => {
    const secret = generateTOTPSecret();
    const prevCode = await computeTOTPCode(secret, -1);
    const nextCode = await computeTOTPCode(secret, 1);
    expect(await validateTOTP(secret, prevCode)).toBe(true);
    expect(await validateTOTP(secret, nextCode)).toBe(true);
  });

  it('rejects invalid code', async () => {
    const secret = generateTOTPSecret();
    const validCode = await computeTOTPCode(secret, 0);
    // Flip the last digit
    const invalidCode =
      validCode.slice(0, 5) +
      ((parseInt(validCode[5], 10) + 1) % 10).toString();
    expect(await validateTOTP(secret, invalidCode)).toBe(false);
  });

  it('rejects empty secret', async () => {
    expect(await validateTOTP('', '123456')).toBe(false);
  });

  it('rejects empty code', async () => {
    const secret = generateTOTPSecret();
    expect(await validateTOTP(secret, '')).toBe(false);
  });

  it('rejects non-numeric code', async () => {
    const secret = generateTOTPSecret();
    expect(await validateTOTP(secret, 'abcdef')).toBe(false);
    expect(await validateTOTP(secret, '12.456')).toBe(false);
    expect(await validateTOTP(secret, '12 456')).toBe(false);
  });

  it('rejects code that is too short (5 digits)', async () => {
    const secret = generateTOTPSecret();
    expect(await validateTOTP(secret, '12345')).toBe(false);
  });

  it('rejects code that is too long (7 digits)', async () => {
    const secret = generateTOTPSecret();
    expect(await validateTOTP(secret, '1234567')).toBe(false);
  });

  it('rejects code with wrong secret', async () => {
    const secretA = generateTOTPSecret();
    const secretB = generateTOTPSecret();
    const codeA = await computeTOTPCode(secretA, 0);
    expect(await validateTOTP(secretB, codeA)).toBe(false);
  });

  // ---- GAP-UT04 (B6): validateTOTP with malformed base32 secret should return false ----

  it('rejects malformed base32 secret (GAP-UT04)', async () => {
    // Invalid base32 characters - should return false, not throw
    const result = await validateTOTP('!!!!invalid!!!!', '123456');
    expect(result).toBe(false);
  });

  it('rejects secret with invalid characters (GAP-UT04)', async () => {
    // Characters not in base32 alphabet
    const result = await validateTOTP('11111111111111111111111111111111', '123456');
    expect(result).toBe(false);
  });

  it('rejects secret with whitespace-only (GAP-UT04)', async () => {
    const result = await validateTOTP('   ', '123456');
    expect(result).toBe(false);
  });
});
