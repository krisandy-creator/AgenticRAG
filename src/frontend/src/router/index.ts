import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import EnterpriseShell from '../pages/rag/EnterpriseShell.vue'
import LoginPage from '../pages/rag/LoginPage.vue'
import DocumentsPage from '../pages/rag/DocumentsPage.vue'
import ChatPage from '../pages/rag/ChatPage.vue'
import TracePage from '../pages/rag/TracePage.vue'
import NotFound from '../pages/notFound/index'
import { getMe } from '../apis/ragKnowledge'
import { clearAuthStorage } from '../utils/request'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: LoginPage,
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    name: 'index',
    component: EnterpriseShell,
    redirect: '/chat',
    meta: { requiresAuth: true },
    children: [
      {
        path: 'chat',
        name: 'chat',
        component: ChatPage
      },
      {
        path: 'documents',
        name: 'documents',
        component: DocumentsPage
      },
      {
        path: 'traces/:traceId?',
        name: 'traces',
        component: TracePage
      }
    ]
  },
  {
    path: '/:catchAll(.*)',
    name: 'not-found',
    component: NotFound
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

let validatedToken: string | null = null

async function hasValidSession(token: string) {
  if (validatedToken === token) {
    return true
  }

  try {
    await getMe()
    validatedToken = token
    return true
  } catch {
    validatedToken = null
    clearAuthStorage()
    return false
  }
}

router.beforeEach(async (to) => {
  const token = localStorage.getItem('token')

  if (!token) {
    return to.meta.requiresAuth ? '/login' : true
  }

  if (to.path === '/login') {
    return (await hasValidSession(token)) ? '/chat' : true
  }

  if (to.meta.requiresAuth && !(await hasValidSession(token))) {
    return '/login'
  }

  return true
})

export default router
