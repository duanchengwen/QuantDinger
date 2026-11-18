const RUN_STATES = ['queued', 'started', 'completed', 'failed', 'needs_model_configuration', 'no_evidence']
const isPending = run => !!run && ['queued', 'started'].includes(run.status)
const validRun = run => !!run && typeof run.id === 'string' && RUN_STATES.includes(run.status)

export function researchEvidenceKey (scope) {
  return ({ issuer_announcement_title: 'researchIssuerTitle', issuer_announcement_excerpt: 'researchIssuerExcerpt', direct_mention: 'researchDirectMention', theme_context: 'researchThemeContext' })[scope] || 'researchEvidenceUnknown'
}

export function researchReasonKey (code) {
  return ({ no_supported_topics: 'researchNoSupportedTopics', no_verified_theme_members: 'researchNoThemeEvidence', no_news_evidence: 'researchNoFreshIssuerEvidence', model_not_supported: 'researchModelUnsupported', model_not_configured: 'researchState.needs_model_configuration', model_endpoint_not_allowed: 'researchEndpointUnsupported', model_timeout: 'researchModelTimeout', outcome_unknown: 'researchOutcomeUnknown', evidence_unavailable: 'researchEvidenceUnavailable', news_excerpt_source_mismatch: 'researchEvidenceUnavailable' })[code] || 'researchReasonUnavailable'
}

export function createResearchState () {
  return { status: null, news: [], runs: [], loading: false, error: '', selectedId: '', detail: null, detailLoading: false, detailError: '', submitting: false, submitError: '', retryPending: false, pollExhausted: false }
}

export function safeNewsUrl (value) {
  try { const url = new URL(value); return url.protocol === 'https:' && ['www.cninfo.com.cn', 'static.cninfo.com.cn'].includes(url.hostname) && !url.username && !url.password ? url.href : '' } catch (_) { return '' }
}

export function researchCitations (run, candidate) {
  if (!run || run.status !== 'completed' || !Array.isArray(candidate.evidence_news_ids)) return []
  const snapshot = run.input_snapshot && run.input_snapshot.news
  return [...new Set(candidate.evidence_news_ids)].map(id => {
    const matches = Array.isArray(snapshot) ? snapshot.filter(item => item && item.id === id) : []
    const item = matches.length === 1 ? matches[0] : null
    const verified = item && item.source === 'cninfo' && item.content_kind === 'announcement_title' &&
      Array.isArray(item.verified_issuer_symbols) && item.verified_issuer_symbols.includes(candidate.symbol)
    return { id, article: verified ? item : null }
  })
}

export function researchCanGenerate (state) {
  return !!state.status && state.status.model_configured === true && !state.status.blocked_reason && !state.loading && !state.error && !state.submitting && !state.runs.some(isPending)
}

export function createResearchBrowser (state, request, errorMessage, options = {}) {
  const schedule = options.schedule || ((callback, delay) => setTimeout(callback, delay))
  const cancel = options.cancel || (timer => clearTimeout(timer))
  const makeKey = options.makeKey || (() => 'research-' + (globalThis.crypto && globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2)))
  const maxPolls = options.maxPolls == null ? 60 : options.maxPolls
  let active = false
  let stopped = false
  let listVersion = 0
  let detailVersion = 0
  let timer = null
  let polls = 0
  let submitKey = ''
  const clearTimer = () => { if (timer != null) cancel(timer); timer = null }
  const current = version => active && !stopped && version === listVersion
  const resetDetail = () => { detailVersion += 1; state.detail = null; state.detailError = ''; state.detailLoading = false }
  const armPoll = () => {
    clearTimer()
    if (!active || stopped || state.error || !state.runs.some(isPending)) return
    if (polls >= maxPolls) { state.pollExhausted = true; return }
    timer = schedule(() => { timer = null; polls += 1; return load(false) }, 5000)
  }

  async function select (id) {
    if (!active || stopped) return
    resetDetail()
    state.selectedId = id
    if (!id || !state.runs.some(run => run.id === id)) return
    const version = detailVersion
    state.detailLoading = true
    try {
      const result = await request('/research/runs/' + encodeURIComponent(id))
      if (!active || stopped || version !== detailVersion) return
      if (!validRun(result) || result.id !== id || (result.status === 'completed' && (!Array.isArray(result.candidates) || !Array.isArray(result.risks)))) throw new Error('invalid_research_response')
      state.detail = result
    } catch (error) {
      if (active && !stopped && version === detailVersion) state.detailError = errorMessage(error)
    } finally {
      if (active && !stopped && version === detailVersion) state.detailLoading = false
    }
  }

  async function load (manual = true) {
    if (!active || stopped) return
    clearTimer()
    if (manual) { polls = 0; state.pollExhausted = false }
    const version = ++listVersion
    resetDetail()
    state.status = null
    state.news = []
    state.runs = []
    state.error = ''
    state.loading = true
    try {
      const results = await Promise.allSettled([request('/research/status'), request('/news?limit=20'), request('/research/runs?limit=10')])
      if (!current(version)) return
      const failed = results.find(result => result.status === 'rejected')
      if (failed) throw failed.reason
      const [status, news, runs] = results.map(result => result.value)
      if (!status || typeof status.model_configured !== 'boolean' || !news || !Array.isArray(news.items) || !runs || !Array.isArray(runs.items) || !runs.items.every(validRun)) throw new Error('invalid_research_response')
      state.status = status
      state.news = news.items
      state.runs = runs.items
      const selected = runs.items.find(run => run.id === state.selectedId) || runs.items[0]
      state.loading = false
      await select(selected ? selected.id : '')
    } catch (error) {
      if (current(version)) { state.error = errorMessage(error); state.selectedId = '' }
    } finally {
      if (current(version)) { state.loading = false; armPoll() }
    }
  }

  return {
    setActive (value) {
      if (stopped || active === value) return
      active = value
      if (active) return load()
      clearTimer(); listVersion += 1; resetDetail(); state.loading = false
    },
    refresh: load,
    select,
    async generate () {
      if (!active || stopped || !researchCanGenerate(state)) return
      state.submitting = true
      state.submitError = ''
      if (!submitKey) submitKey = makeKey()
      try {
        const result = await request('/research/runs', 'post', { idempotency_key: submitKey })
        if (stopped) return
        if (!validRun(result)) throw new Error('invalid_research_response')
        submitKey = ''
        state.retryPending = false
        state.selectedId = result.id
        if (active) await load()
      } catch (error) {
        if (!stopped) { state.submitError = errorMessage(error); state.retryPending = true }
      } finally {
        if (!stopped) state.submitting = false
      }
    },
    dispose () { stopped = true; active = false; clearTimer(); listVersion += 1; resetDetail() }
  }
}
