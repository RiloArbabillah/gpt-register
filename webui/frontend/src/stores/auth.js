import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getMe, login as loginRequest, logout as logoutRequest } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const loading = ref(false)

  async function load() {
    try {
      const result = await getMe()
      user.value = result.user || null
      return user.value
    } catch (_) {
      user.value = null
      return null
    }
  }

  async function login(username, password) {
    loading.value = true
    try {
      const result = await loginRequest({ username, password })
      user.value = result.user
      return user.value
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    try { await logoutRequest() } finally { user.value = null }
  }

  return { user, loading, load, login, logout }
})
