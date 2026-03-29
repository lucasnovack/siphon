import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { authApi, setAccessToken, type User } from '@/lib/api'

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const initialized = useRef(false)

  const fetchMe = useCallback(async () => {
    try {
      const res = await authApi.me()
      setUser(res.data)
    } catch {
      setUser(null)
    }
  }, [])

  // On mount: try to refresh (httpOnly cookie) → get new access token → fetch me
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true

    authApi
      .refresh()
      .then((res) => {
        setAccessToken(res.data.access_token)
        return fetchMe()
      })
      .catch(() => {
        setAccessToken(null)
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [fetchMe])

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login(email, password)
    setAccessToken(res.data.access_token)
    await fetchMe()
  }, [fetchMe])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setAccessToken(null)
      setUser(null)
    }
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
