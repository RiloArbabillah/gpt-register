import { createRouter, createWebHashHistory } from 'vue-router'
import NProgress from 'nprogress'

NProgress.configure({ showSpinner: false, trickleSpeed: 120, minimum: 0.15 })

// hash 路由：不依赖后端做 SPA 回退，FastAPI / 未来 Gin 都零配置可用。
const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { titleKey: 'dashboard', icon: 'Odometer', groupKey: 'overview' },
  },
  {
    path: '/import',
    name: 'import',
    component: () => import('@/views/Import.vue'),
    meta: { titleKey: 'import', icon: 'Upload', groupKey: 'registration', admin: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/Register.vue'),
    meta: { titleKey: 'register', icon: 'VideoPlay', groupKey: 'registration' },
  },
  {
    path: '/auto',
    name: 'auto',
    component: () => import('@/views/AutoLoop.vue'),
    meta: { titleKey: 'auto', icon: 'MagicStick', groupKey: 'registration' },
  },
  {
    path: '/proxy',
    name: 'proxy',
    component: () => import('@/views/ProxyPool.vue'),
    meta: { titleKey: 'proxy', icon: 'Connection', groupKey: 'registration' },
  },
  {
    path: '/pool',
    name: 'pool',
    component: () => import('@/views/Pool.vue'),
    meta: { titleKey: 'pool', icon: 'Files', groupKey: 'data', admin: true },
  },
  {
    path: '/imap-pool',
    name: 'imap-pool',
    component: () => import('@/views/ImapPool.vue'),
    meta: { titleKey: 'imapPool', icon: 'Message', groupKey: 'data', admin: true },
  },
  {
    path: '/registered',
    name: 'registered',
    component: () => import('@/views/Registered.vue'),
    meta: { titleKey: 'registered', icon: 'CircleCheck', groupKey: 'data' },
  },
  {
    path: '/runs',
    name: 'runs',
    component: () => import('@/views/Runs.vue'),
    meta: { titleKey: 'runs', icon: 'Document', groupKey: 'data' },
  },
  {
    path: '/settings/mail',
    name: 'mail',
    component: () => import('@/views/MailConfig.vue'),
    meta: { titleKey: 'mail', icon: 'Message', groupKey: 'settings', admin: true },
  },
  {
    path: '/settings/sms',
    name: 'sms',
    component: () => import('@/views/SmsConfig.vue'),
    meta: { titleKey: 'sms', icon: 'Iphone', groupKey: 'settings', admin: true },
  },
  {
    path: '/settings/export',
    name: 'export',
    component: () => import('@/views/ExportConfig.vue'),
    meta: { titleKey: 'export', icon: 'Share', groupKey: 'settings', admin: true },
  },
  {
    path: '/settings/users',
    name: 'users',
    component: () => import('@/views/Users.vue'),
    meta: { titleKey: 'users', icon: 'User', groupKey: 'settings', admin: true },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由切换顶部进度条
router.beforeEach(async (to, from, next) => {
  NProgress.start()
  if (to.meta?.public) return next()
  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore()
  if (!auth.user) await auth.load()
  if (!auth.user) return next({ name: 'login', query: { redirect: to.fullPath } })
  if (to.meta?.admin && auth.user.role !== 'admin') return next({ name: 'dashboard' })
  if (to.meta?.titleKey) document.title = 'Outlook Register'
  next()
})
router.afterEach(() => {
  NProgress.done()
})

export default router
