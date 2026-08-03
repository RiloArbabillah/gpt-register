import { createRouter, createWebHashHistory } from 'vue-router'
import NProgress from 'nprogress'

NProgress.configure({ showSpinner: false, trickleSpeed: 120, minimum: 0.15 })

// hash 路由：不依赖后端做 SPA 回退，FastAPI / 未来 Gin 都零配置可用。
const routes = [
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
    meta: { titleKey: 'import', icon: 'Upload', groupKey: 'registration' },
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
    meta: { titleKey: 'pool', icon: 'Files', groupKey: 'data' },
  },
  {
    path: '/imap-pool',
    name: 'imap-pool',
    component: () => import('@/views/ImapPool.vue'),
    meta: { titleKey: 'imapPool', icon: 'Message', groupKey: 'data' },
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
    meta: { titleKey: 'mail', icon: 'Message', groupKey: 'settings' },
  },
  {
    path: '/settings/sms',
    name: 'sms',
    component: () => import('@/views/SmsConfig.vue'),
    meta: { titleKey: 'sms', icon: 'Iphone', groupKey: 'settings' },
  },
  {
    path: '/settings/export',
    name: 'export',
    component: () => import('@/views/ExportConfig.vue'),
    meta: { titleKey: 'export', icon: 'Share', groupKey: 'settings' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由切换顶部进度条
router.beforeEach((to, from, next) => {
  NProgress.start()
  if (to.meta?.titleKey) document.title = 'Outlook Register'
  next()
})
router.afterEach(() => {
  NProgress.done()
})

export default router
