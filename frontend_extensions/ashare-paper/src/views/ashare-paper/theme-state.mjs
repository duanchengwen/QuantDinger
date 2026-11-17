const PAGE_SIZE = 100

export function createThemeState () {
  return {
    themes: [],
    catalogStatus: null,
    catalogLoading: false,
    catalogError: '',
    initialized: false,
    selectedBoard: '',
    snapshot: null,
    offset: 0,
    snapshotLoading: false,
    snapshotError: ''
  }
}

export function themeMembers (state) {
  return state.snapshot && state.snapshot.status === 'observed' ? state.snapshot.members : []
}

export function themeHasNext (state) {
  return !!state.snapshot && state.snapshot.status === 'observed' &&
    Number.isInteger(state.snapshot.member_count) && state.offset + PAGE_SIZE < state.snapshot.member_count
}

export function themePageRange (state) {
  const members = themeMembers(state)
  return { start: members.length ? state.offset + 1 : 0, end: members.length ? state.offset + members.length : 0, total: state.snapshot ? state.snapshot.member_count : 0 }
}

export function createThemeBrowser (state, request, errorMessage) {
  let catalogVersion = 0
  let snapshotVersion = 0
  let stopped = false
  const isCurrent = version => !stopped && version === snapshotVersion
  const resetSnapshot = () => {
    snapshotVersion += 1
    state.snapshot = null
    state.snapshotError = ''
    state.snapshotLoading = false
  }

  async function loadSnapshot () {
    resetSnapshot()
    const version = snapshotVersion
    const board = state.selectedBoard
    const offset = state.offset
    if (stopped || !board || !state.themes.some(theme => theme.board_code === board)) return
    state.snapshotLoading = true
    try {
      const result = await request(`/themes/${encodeURIComponent(board)}?limit=${PAGE_SIZE}&offset=${offset}`)
      if (!isCurrent(version)) return
      if (!result || !['observed', 'not_observed'].includes(result.status) ||
        (result.status === 'observed' && (result.board_code !== board || !Array.isArray(result.members) ||
          !Number.isInteger(result.member_count) || result.member_count < 0))) throw new Error('invalid_theme_response')
      state.snapshot = result
    } catch (error) {
      if (isCurrent(version)) state.snapshotError = errorMessage(error)
    } finally {
      if (isCurrent(version)) state.snapshotLoading = false
    }
  }

  async function loadCatalog () {
    if (stopped) return
    const version = ++catalogVersion
    resetSnapshot()
    state.offset = 0
    state.initialized = true
    state.catalogStatus = null
    state.catalogError = ''
    state.catalogLoading = true
    state.themes = []
    try {
      const result = await request('/themes')
      if (stopped || version !== catalogVersion) return
      if (!result || !['observed', 'not_observed'].includes(result.status) || !Array.isArray(result.themes)) throw new Error('invalid_theme_response')
      state.catalogStatus = result.status
      state.themes = result.status === 'observed' ? result.themes : []
      if (!state.themes.some(theme => theme.board_code === state.selectedBoard)) state.selectedBoard = state.themes.length ? state.themes[0].board_code : ''
      state.catalogLoading = false
      await loadSnapshot()
    } catch (error) {
      if (!stopped && version === catalogVersion) {
        state.selectedBoard = ''
        state.catalogError = errorMessage(error)
      }
    } finally {
      if (!stopped && version === catalogVersion) state.catalogLoading = false
    }
  }

  return {
    ensureLoaded () { if (!state.initialized) return loadCatalog() },
    refresh: loadCatalog,
    select (board) {
      if (stopped) return
      state.selectedBoard = state.themes.some(theme => theme.board_code === board) ? board : ''
      state.offset = 0
      return loadSnapshot()
    },
    page (direction) {
      if (stopped || state.snapshotLoading || state.catalogLoading || ![-1, 1].includes(direction)) return
      if ((direction === -1 && state.offset === 0) || (direction === 1 && !themeHasNext(state))) return
      state.offset += direction * PAGE_SIZE
      return loadSnapshot()
    },
    dispose () { stopped = true; catalogVersion += 1; snapshotVersion += 1 }
  }
}
