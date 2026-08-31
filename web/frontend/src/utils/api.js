import axios from 'axios'
import { useUserStore } from '../stores/user'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const { state } = useUserStore()
  if (state.token) {
    config.headers.Authorization = `Bearer ${state.token}`
  }
  return config
})

api.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response?.status === 401) {
      const { logout } = useUserStore()
      logout()
      window.location.hash = '#/login'
    }
    return Promise.reject(error)
  }
)

export default api
