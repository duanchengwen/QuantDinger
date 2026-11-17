/** Keep current-password proof separate from email verification codes. */
export function submitPasswordChange (mode, values, api) {
  if (mode === 'current') {
    return api.current({ old_password: values.old_password, new_password: values.new_password })
  }
  if (mode === 'email') {
    return api.email({ code: values.code, new_password: values.new_password })
  }
  throw new Error('Unknown password verification method')
}

export function passwordErrorKey (message = '') {
  if (message === 'Old password incorrect' || message === 'Old password required') return 'profile.localPassword.currentError'
  if (message === 'Email service is not configured') return 'profile.localPassword.mailUnavailable'
  if (message === 'Email verification required to set initial password') return 'profile.localPassword.initialEmail'
  if (/Invalid verification code|expired|already used/i.test(message)) return 'profile.localPassword.codeError'
  if (/Too many|temporarily locked/i.test(message)) return 'profile.localPassword.rateLimit'
  return 'profile.localPassword.failed'
}
