import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import EnterpriseShell from '../pages/rag/EnterpriseShell.vue'
import LoginPage from '../pages/rag/LoginPage.vue'
import DocumentsPage from '../pages/rag/DocumentsPage.vue'
import ChatPage from '../pages/rag/ChatPage.vue'
import TracePage from '../pages/rag/TracePage.vue'
import NotFound from '../pages/notFound/index'

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

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
    return
  }
  if (to.path === '/login' && token) {
    next('/chat')
    return
  }
  next()
})

export default router
