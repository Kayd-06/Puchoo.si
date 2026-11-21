import { afterEach, expect, it, vi } from 'vitest';

import { fetchApiBlob } from '../client';

afterEach(() => {
  vi.restoreAllMocks();
});

it('returns an authenticated WAV blob from the API', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('wav-bytes', {
      status: 200,
      headers: { 'content-type': 'audio/wav' },
    }),
  );

  const audio = await fetchApiBlob('/voice/synthesize', {
    method: 'POST',
    body: JSON.stringify({ text: 'Hello', language_code: 'en-IN' }),
  });

  expect(audio.type).toBe('audio/wav');
  expect(await audio.text()).toBe('wav-bytes');
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/voice/synthesize',
    expect.objectContaining({
      credentials: 'include',
      headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
    }),
  );
});

it('rejects a successful response that is not WAV audio', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('{"detail":"not audio"}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );

  await expect(fetchApiBlob('/voice/synthesize', { method: 'POST', body: '{}' })).rejects.toThrow(
    'Speech service returned an invalid audio response.',
  );
});

it('keeps the code from a structured error so the UI can ask to confirm', async () => {
  const { fetchApi } = await import('../client');
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(
      JSON.stringify({
        detail: { code: 'company_mismatch', message: 'This file looks like another company.' },
      }),
      { status: 409, headers: { 'content-type': 'application/json' } },
    ),
  );

  await expect(
    fetchApi('/workspaces/ws_1/files/sales/replace', { method: 'POST' }),
  ).rejects.toMatchObject({
    code: 'company_mismatch',
    status: 409,
    message: 'This file looks like another company.',
  });
});
