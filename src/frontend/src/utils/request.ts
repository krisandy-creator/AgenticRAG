import axios from 'axios'

const request = axios.create({
  baseURL: '',
  timeout: 10000
})

export function clearAuthStorage() {
  localStorage.removeItem('token')
  localStorage.removeItem('userInfo')
}

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

request.interceptors.request.use(
  function (config) {
    const token = localStorage.getItem('token')
    const isLoginRequest = typeof config.url === 'string' && config.url.includes('/auth/login')
    if (token && !isLoginRequest) {
      config.headers = config.headers || {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  function (error) {
    console.error('请求拦截器错误:', error)
    return Promise.reject(error)
  }
)

request.interceptors.response.use(
  function (response) {
    return response
  },
  function (error) {
    console.error('响应错误:', error.response?.status, error.config?.url)
    console.error('错误详情:', error.response?.data || error.message)

    if (error.response?.status === 401) {
      clearAuthStorage()
      redirectToLogin()
    }
    return Promise.reject(error)
  }
)

export { request }
