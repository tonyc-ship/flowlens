import { strict as assert } from 'assert'
import { test } from 'node:test'
import {
  NAVIGATOR_INIT_SCRIPT,
  WARMUP_PATHS,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
} from '../scripts/browser-fingerprint.js'

test('launchOptions: uses chrome channel by default', () => {
  const opts = launchOptions(false)
  assert.equal(opts.channel, 'chrome')
  assert.equal(opts.headless, false)
  assert.ok(opts.args.includes('--disable-blink-features=AutomationControlled'))
})

test('launchOptionsFallback: works without channel', () => {
  const opts = launchOptionsFallback(true)
  assert.equal(opts.headless, true)
  assert.equal('channel' in opts, false)
})

test('contextOptions: keeps real UA and injects locale headers', () => {
  const opts = contextOptions('/tmp/storage.json')
  assert.equal(opts.storageState, '/tmp/storage.json')
  assert.equal('userAgent' in opts, false)
  assert.ok(opts.extraHTTPHeaders['Accept-Language'].includes('zh-CN'))
})

test('navigator init script: hides webdriver', () => {
  assert.ok(NAVIGATOR_INIT_SCRIPT.includes('webdriver'))
  assert.ok(NAVIGATOR_INIT_SCRIPT.includes('undefined'))
})

test('warmup paths: include required bootstrap APIs', () => {
  assert.ok(WARMUP_PATHS.includes('/api/sns/web/v2/user/me'))
  assert.ok(WARMUP_PATHS.includes('/api/sns/web/unread_count'))
  assert.ok(WARMUP_PATHS.includes('/api/sns/web/v1/system/config'))
})
