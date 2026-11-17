import { test } from 'node:test'
import assert from 'node:assert/strict'
import { submitPasswordChange, passwordErrorKey } from '../src/views/profile/password-change.mjs'

test('local account submits current password without an email code', async () => {
  const request = await submitPasswordChange('current', { old_password: 'current', new_password: 'new', code: 'stale-code' }, {
    current: async data => data,
    email: () => assert.fail('Local password change must not require an email code')
  })
  assert.deepEqual(request, { old_password: 'current', new_password: 'new' })
})

test('email option preserves its separate proof and never sends current password', async () => {
  const request = await submitPasswordChange('email', { old_password: 'unused', new_password: 'new', code: '123456' }, {
    email: async data => data,
    current: () => assert.fail('Email proof must not call the current-password endpoint')
  })
  assert.deepEqual(request, { code: '123456', new_password: 'new' })
})

test('unknown proof mode fails before making any request', () => {
  assert.throws(() => submitPasswordChange('invalid', {}, {}), /Unknown/)
})

test('errors explain missing mail transport separately from invalid codes', () => {
  assert.equal(passwordErrorKey('Email service is not configured'), 'profile.localPassword.mailUnavailable')
  assert.equal(passwordErrorKey('Invalid verification code. 4 attempts remaining'), 'profile.localPassword.codeError')
  assert.equal(passwordErrorKey('Old password incorrect'), 'profile.localPassword.currentError')
  assert.equal(passwordErrorKey('Too many password attempts. Try again later'), 'profile.localPassword.rateLimit')
})
