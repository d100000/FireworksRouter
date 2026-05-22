import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/login', component: () => import('@/views/Login.vue') },
  {
    path: '/',
    component: () => import('@/views/AppLayout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '概览' } },
      { path: 'upstream-keys', component: () => import('@/views/UpstreamKeys.vue'), meta: { title: '上游 Key 池' } },
      { path: 'models', component: () => import('@/views/Models.vue'), meta: { title: '模型管理' } },
      { path: 'api-keys', component: () => import('@/views/ApiKeys.vue'), meta: { title: 'API Keys' } },
      { path: 'request-trace', component: () => import('@/views/RequestTrace.vue'), meta: { title: '调度轨迹' } },
      { path: 'request-logs', component: () => import('@/views/RequestLogs.vue'), meta: { title: '调用日志' } },
      { path: 'probe-logs', component: () => import('@/views/ProbeLogs.vue'), meta: { title: '探针历史' } },
      { path: 'settings', component: () => import('@/views/Settings.vue'), meta: { title: '系统设置' } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.path === '/login') return true
  if (!auth.isLoggedIn) return '/login'
  return true
})

export default router
