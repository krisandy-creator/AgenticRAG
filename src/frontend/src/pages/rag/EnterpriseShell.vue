<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { ChatDotRound, DataLine, Document, SwitchButton } from '@element-plus/icons-vue'
import { getMe, type EnterpriseUser } from '../../apis/ragKnowledge'

const router = useRouter()
const route = useRoute()
const user = ref<EnterpriseUser | null>(null)

const navItems = [
  { path: '/chat', label: '问答', icon: ChatDotRound },
  { path: '/documents', label: '文档', icon: Document },
  { path: '/traces', label: 'Trace', icon: DataLine }
]

const activePath = computed(() => `/${String(route.path).split('/')[1] || 'chat'}`)

onMounted(async () => {
  const cached = localStorage.getItem('userInfo')
  if (cached) {
    user.value = JSON.parse(cached)
  }
  try {
    const response = await getMe()
    user.value = response.data.data
    localStorage.setItem('userInfo', JSON.stringify(response.data.data))
  } catch {
    localStorage.removeItem('token')
    router.replace('/login')
  }
})

function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('userInfo')
  router.replace('/login')
}
</script>

<template>
  <div class="rag-shell">
    <aside class="rag-sidebar">
      <div class="brand-block">
        <div class="brand-mark">RAG</div>
        <div>
          <div class="brand-title">企业知识库</div>
          <div class="brand-subtitle">内部检索与溯源</div>
        </div>
      </div>

      <nav class="shell-nav">
        <button
          v-for="item in navItems"
          :key="item.path"
          class="nav-item"
          :class="{ active: activePath === item.path }"
          @click="router.push(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="user-panel">
        <div class="user-meta">
          <strong>{{ user?.user_name || '未登录' }}</strong>
          <span>{{ user?.role_name || '-' }} · L{{ user?.access_level || 0 }}</span>
        </div>
        <el-tooltip content="退出登录" placement="top">
          <button class="icon-button" @click="logout">
            <el-icon><SwitchButton /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </aside>

    <main class="rag-main">
      <RouterView />
    </main>
  </div>
</template>

