import { strict as assert } from 'assert'
import { test } from 'node:test'
import { isSessionValid, cookieStringToStorageState } from '../scripts/auth.js'

test('isSessionValid: returns false for null', () => {
  assert.equal(isSessionValid(null), false)
})

test('isSessionValid: returns false for expired session', () => {
  const account = { session_expires_at: new Date(Date.now() - 1000).toISOString() }
  assert.equal(isSessionValid(account), false)
})

test('isSessionValid: returns true for valid session', () => {
  const account = { session_expires_at: new Date(Date.now() + 86400000 * 3).toISOString() }
  assert.equal(isSessionValid(account), true)
})

test('isSessionValid: returns false for missing session_expires_at', () => {
  const account = { name: 'test', storage_path: '~/.xhs-accounts/test/storage.json' }
  assert.equal(isSessionValid(account), false)
})

test('cookieStringToStorageState: web_session is httpOnly Lax', () => {
  const state = cookieStringToStorageState('a1=abc; web_session=xyz')
  const cookies = Object.fromEntries(state.cookies.map(c => [c.name, c]))
  assert.equal(cookies.web_session.httpOnly, true)
  assert.equal(cookies.web_session.sameSite, 'Lax')
})

test('cookieStringToStorageState: a1 stays non-httpOnly', () => {
  const state = cookieStringToStorageState('a1=abc; web_session=xyz')
  const cookies = Object.fromEntries(state.cookies.map(c => [c.name, c]))
  assert.equal(cookies.a1.httpOnly, false)
  assert.equal(cookies.a1.sameSite, 'None')
})

test('cookieStringToStorageState: unknown cookie uses safe defaults', () => {
  const state = cookieStringToStorageState('foo=bar')
  const foo = state.cookies.find(c => c.name === 'foo')
  assert.equal(foo.httpOnly, false)
  assert.equal(foo.sameSite, 'Lax')
})
