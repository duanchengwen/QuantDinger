export function ageSeconds (value, now) {
  return value ? (now - new Date(value).getTime()) / 1000 : Infinity
}

export function quoteIsFresh (quote, now) {
  return !!quote && [quote.exchange_time, quote.received_at].every(value => {
    const age = ageSeconds(value, now)
    return age >= 0 && age <= 15
  })
}

export function shanghaiDate (now) {
  const values = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date(now))
  const part = key => values.find(value => value.type === key).value
  return `${part('year')}-${part('month')}-${part('day')}`
}

export function ruleIsReady (rule, now) {
  return !!rule && rule.reviewed === true && rule.is_trading_day === true &&
    rule.trading_day === shanghaiDate(now) && rule.suspended === false && rule.st === false
}

export function roleIsViewer (role) {
  return role === 'viewer' || !!(role && typeof role === 'object' && role.id === 'viewer')
}
