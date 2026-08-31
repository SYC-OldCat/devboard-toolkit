import { reactive } from 'vue'

const STORAGE_KEY = 'devboard_user'

export function useUserStore() {
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  const state = reactive({
    token: saved.token || '',
    username: saved.username || '',
    displayName: saved.displayName || '',
  })

  function setAuth(data) {
    state.token = data.access_token
    state.username = data.username
    state.displayName = data.display_name
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...state }))
  }

  function logout() {
    state.token = ''
    state.username = ''
    state.displayName = ''
    localStorage.removeItem(STORAGE_KEY)
  }

  return { state, setAuth, logout }
}
