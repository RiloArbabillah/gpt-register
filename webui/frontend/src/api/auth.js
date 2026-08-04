import http from './request'

export const login = (payload) => http.post('/api/auth/login', payload)
export const logout = () => http.post('/api/auth/logout')
export const getMe = () => http.get('/api/auth/me')
export const listUsers = () => http.get('/api/admin/users')
export const createUser = (payload) => http.post('/api/admin/users', payload)
export const updateUser = (id, payload) => http.patch(`/api/admin/users/${id}`, payload)
export const deleteUser = (id) => http.delete(`/api/admin/users/${id}`)
