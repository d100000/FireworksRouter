import api from './client'

export const systemApi = {
  info: () => api.get('/system/info'),
}

export const authApi = {
  login: (data) => api.post('/admin/auth/login', data),
  logout: () => api.post('/admin/auth/logout'),
}

export const upstreamApi = {
  list: () => api.get('/admin/upstream-keys'),
  create: (data) => api.post('/admin/upstream-keys', data),
  batchCreate: (data) => api.post('/admin/upstream-keys/batch', data),
  update: (id, data) => api.patch(`/admin/upstream-keys/${id}`, data),
  delete: (id) => api.delete(`/admin/upstream-keys/${id}`),
  probe: (id) => api.post(`/admin/upstream-keys/${id}/probe`),
  probeAll: () => api.post('/admin/probe-now'),
  metrics: (id, params) => api.get(`/admin/upstream-keys/${id}/metrics`, { params }),
  errorBreakdown: (id, params) => api.get(`/admin/upstream-keys/${id}/error-breakdown`, { params }),
  modelStates: (id) => api.get(`/admin/upstream-keys/${id}/model-states`),
}

export const apiKeysApi = {
  list: () => api.get('/admin/api-keys'),
  create: (data) => api.post('/admin/api-keys', data),
  update: (id, data) => api.patch(`/admin/api-keys/${id}`, data),
  delete: (id) => api.delete(`/admin/api-keys/${id}`),
  rotate: (id) => api.post(`/admin/api-keys/${id}/rotate`),
  reveal: (id) => api.get(`/admin/api-keys/${id}/reveal`),
}

export const modelApi = {
  list: () => api.get('/admin/models'),
  create: (data) => api.post('/admin/models', data),
  update: (id, data) => api.patch(`/admin/models/${id}`, data),
  delete: (id) => api.delete(`/admin/models/${id}`),
  sync: () => api.post('/admin/models/sync'),
  batchStatus: (ids, status) => api.post('/admin/models/batch-status', { ids, status }),
}

export const logsApi = {
  requests: (params) => api.get('/admin/logs/requests', { params }),
  probes: (params) => api.get('/admin/logs/probes', { params }),
}

export const statsApi = {
  overview: () => api.get('/admin/stats/overview'),
  today: () => api.get('/admin/stats/today'),
  top: (params) => api.get('/admin/stats/top', { params }),
  timeseries: (params) => api.get('/admin/stats/timeseries', { params }),
  keysHealth: () => api.get('/admin/stats/keys-health'),
  requestTrace: (params) => api.get('/admin/stats/request-trace', { params }),
  flowSankey: (params) => api.get('/admin/stats/flow-sankey', { params }),
}

export const settingsApi = {
  get: () => api.get('/admin/settings'),
  patch: (items) => api.patch('/admin/settings', { items }),
}
