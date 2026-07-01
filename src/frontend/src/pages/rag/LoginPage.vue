<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Lock, User } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { enterpriseLogin } from '../../apis/ragKnowledge'

const router = useRouter()
const loading = ref(false)
const form = reactive({
  user_name: 'Admin',
  user_password: 'admin123'
})

async function submit() {
  if (!form.user_name || !form.user_password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const response = await enterpriseLogin(form)
    localStorage.setItem('token', response.data.data.access_token)
    localStorage.setItem('userInfo', JSON.stringify(response.data.data.user))
    router.replace('/chat')
  } catch {
    ElMessage.error('登录失败，请检查账号或密码')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <section class="login-copy">
      <div class="product-kicker">Enterprise RAG Prototype</div>
      <h1>企业知识库</h1>
      <p>文档解析、权限检索、实时链路和 Trace 回放集中在一个工作台。</p>
      <div class="login-stats">
        <div><strong>4</strong><span>核心页面</span></div>
        <div><strong>SSE</strong><span>实时事件</span></div>
        <div><strong>Trace</strong><span>全量保存</span></div>
      </div>
    </section>

    <section class="login-form-panel">
      <div class="form-heading">
        <h2>登录</h2>
        <span>默认管理员：Admin / admin123</span>
      </div>
      <el-form @submit.prevent>
        <el-form-item>
          <el-input v-model="form.user_name" size="large" placeholder="用户名" :prefix-icon="User" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.user_password"
            size="large"
            placeholder="密码"
            type="password"
            show-password
            :prefix-icon="Lock"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button class="login-action" type="primary" size="large" :loading="loading" @click="submit">
          进入知识库
        </el-button>
      </el-form>
    </section>
  </div>
</template>

