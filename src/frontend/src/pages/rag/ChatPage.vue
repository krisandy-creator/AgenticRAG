<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { ArrowLeft, ArrowRight, Delete, Link, Plus, Promotion, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createChatSession,
  deleteChatSession,
  getDocumentDownloadUrl,
  getTraceEvents,
  listChatMessages,
  listChatSessions,
  streamChat,
  type ChatMessage,
  type ChatSession,
  type Citation,
  type TraceEvent
} from '../../apis/ragKnowledge'

const sessions = ref<ChatSession[]>([])
const messages = ref<ChatMessage[]>([])
const events = ref<TraceEvent[]>([])
const activeSessionId = ref('')
const activeAssistantId = ref('')
const latestEvent = ref<TraceEvent | null>(null)
const question = ref('')
const mode = ref<'normal' | 'deep_analysis'>('normal')
const streaming = ref(false)
const traceCollapsed = ref(false)
const messagesRef = ref<HTMLElement>()

const visibleEvents = computed(() => events.value.filter((event) => event.event_type !== 'answer_delta'))

onMounted(async () => {
  await loadSessions()
  if (!activeSessionId.value) {
    await newSession()
  }
})

async function loadSessions() {
  const response = await listChatSessions()
  sessions.value = response.data.data
  if (sessions.value.length && !activeSessionId.value) {
    activeSessionId.value = sessions.value[0].session_id
    await loadMessages(activeSessionId.value)
  }
}

async function newSession() {
  const response = await createChatSession()
  sessions.value.unshift(response.data.data)
  activeSessionId.value = response.data.data.session_id
  messages.value = []
  events.value = []
  latestEvent.value = null
  activeAssistantId.value = ''
}

async function switchSession(sessionId: string) {
  if (sessionId === activeSessionId.value) return
  activeSessionId.value = sessionId
  events.value = []
  latestEvent.value = null
  activeAssistantId.value = ''
  await loadMessages(sessionId)
}

async function removeSession(sessionId: string) {
  if (streaming.value && sessionId === activeSessionId.value) {
    ElMessage.warning('当前会话正在生成回答，完成后再删除')
    return
  }

  try {
    await ElMessageBox.confirm('删除后会清除该会话的消息和链路记录，是否继续？', '删除会话', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
      distinguishCancelAndClose: true
    })
  } catch {
    return
  }

  try {
    await deleteChatSession(sessionId)
    const deletedActiveSession = sessionId === activeSessionId.value
    const remainingSessions = sessions.value.filter((session) => session.session_id !== sessionId)
    sessions.value = remainingSessions

    if (deletedActiveSession) {
      messages.value = []
      events.value = []
      latestEvent.value = null
      activeAssistantId.value = ''
      activeSessionId.value = ''
      if (remainingSessions.length) {
        await switchSession(remainingSessions[0].session_id)
      } else {
        await newSession()
      }
    }

    ElMessage.success('会话已删除')
  } catch {
    ElMessage.error('会话删除失败，请稍后重试')
  }
}

function toggleTraceRail() {
  traceCollapsed.value = !traceCollapsed.value
}

async function loadMessages(sessionId: string) {
  if (!sessionId) return
  const response = await listChatMessages(sessionId)
  if (sessionId !== activeSessionId.value) return

  const loadedMessages = response.data.data
  messages.value = loadedMessages
  await restoreTraceState(sessionId, loadedMessages)
  if (sessionId !== activeSessionId.value) return

  messages.value = [...loadedMessages]
  await scrollToBottom()
}

async function restoreTraceState(sessionId: string, loadedMessages: ChatMessage[]) {
  const tracedMessages = loadedMessages.filter(
    (message): message is ChatMessage & { trace_id: string } => message.role === 'assistant' && Boolean(message.trace_id)
  )
  if (!tracedMessages.length) {
    events.value = []
    latestEvent.value = null
    return
  }

  const traceGroups = await Promise.all(
    tracedMessages.map(async (message) => ({
      message,
      traceEvents: await loadTraceEvents(message.trace_id)
    }))
  )

  if (sessionId !== activeSessionId.value) return

  traceGroups.forEach(({ message, traceEvents }) => {
    message.citations = citationsFromEvents(traceEvents)
  })

  const latestTrace = [...traceGroups].reverse().find(({ traceEvents }) => traceEvents.length)
  events.value = latestTrace?.traceEvents || []
  latestEvent.value = getLatestVisibleEvent(events.value)
}

async function loadTraceEvents(traceId: string) {
  try {
    const response = await getTraceEvents(traceId)
    return response.data.data
  } catch {
    return []
  }
}

function citationsFromEvents(traceEvents: TraceEvent[]) {
  const citations: Citation[] = []
  traceEvents.forEach((event) => {
    if (event.event_type !== 'citation') return
    const citation = toCitation(event.payload)
    if (citation && !citations.some((item) => item.chunk_id === citation.chunk_id)) {
      citations.push(citation)
    }
  })
  return citations
}

function getLatestVisibleEvent(traceEvents: TraceEvent[]) {
  const visibleTraceEvents = traceEvents.filter((event) => event.event_type !== 'answer_delta')
  return visibleTraceEvents.length ? visibleTraceEvents[visibleTraceEvents.length - 1] : null
}

async function sendQuestion() {
  const content = question.value.trim()
  if (!content || streaming.value) return
  if (!activeSessionId.value) {
    await newSession()
  }

  streaming.value = true
  events.value = []
  latestEvent.value = null

  messages.value.push({
    message_id: `local_${Date.now()}`,
    session_id: activeSessionId.value,
    role: 'user',
    content,
    created_at: new Date().toISOString()
  })

  const assistantMessage: ChatMessage = {
    message_id: `assistant_${Date.now()}`,
    session_id: activeSessionId.value,
    role: 'assistant',
    content: '',
    citations: [],
    created_at: new Date().toISOString()
  }
  messages.value.push(assistantMessage)
  activeAssistantId.value = assistantMessage.message_id
  question.value = ''
  await scrollToBottom()

  try {
    await streamChat({ session_id: activeSessionId.value, question: content, mode: mode.value }, (event) => {
      handleStreamEvent(event, assistantMessage)
    })
    await refreshPersistedMessages(assistantMessage)
    await loadSessions()
  } catch {
    ElMessage.error('问答流中断，请稍后重试')
  } finally {
    streaming.value = false
    activeAssistantId.value = ''
  }
}

function handleStreamEvent(event: TraceEvent, assistantMessage: ChatMessage) {
  if (event.event_type === 'answer_delta') {
    assistantMessage.content += readString(event.payload, 'delta')
    void scrollToBottom()
    return
  }

  if (event.event_type === 'citation') {
    const citation = toCitation(event.payload)
    if (citation && !assistantMessage.citations?.some((item) => item.chunk_id === citation.chunk_id)) {
      assistantMessage.citations = [...(assistantMessage.citations || []), citation]
    }
  }

  if (event.event_type === 'trace_finished') {
    assistantMessage.trace_id = event.trace_id
  }

  events.value.push(event)
  latestEvent.value = event
  void scrollToBottom()
}

async function refreshPersistedMessages(streamedMessage: ChatMessage) {
  const traceId = streamedMessage.trace_id
  const citations = streamedMessage.citations || []
  await loadMessages(activeSessionId.value)
  if (!traceId) return
  const persisted = [...messages.value].reverse().find((message) => message.trace_id === traceId)
  if (persisted) {
    persisted.citations = citations
  }
}

async function openCitation(citation: Citation) {
  try {
    const response = await getDocumentDownloadUrl(citation.document_id)
    const url = response.data.data.url
    if (url.startsWith('http')) {
      window.open(url, '_blank')
      return
    }
    ElMessage.info('当前来源没有可打开的外部链接')
  } catch {
    ElMessage.error('来源链接获取失败')
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messagesRef.value) {
    messagesRef.value.scrollTop = messagesRef.value.scrollHeight
  }
}

function eventName(type: string) {
  const names: Record<string, string> = {
    trace_started: 'Trace 开始',
    mode_selected: '模式',
    query_rewrite_started: '问题改写',
    query_rewrite_finished: '改写完成',
    retrieval_started: '检索开始',
    retrieval_debug: '检索调试',
    retrieval_hit: '检索命中',
    retrieval_finished: '检索完成',
    rerank_started: '重排开始',
    rerank_finished: '重排完成',
    excel_analysis_started: 'Excel 分析',
    excel_analysis_finished: 'Excel 结果',
    prompt_built: '回答准备',
    citation: '引用来源',
    trace_finished: '完成',
    error: '错误'
  }
  return names[type] || type
}

function eventSummary(event: TraceEvent) {
  const payload = event.payload
  switch (event.event_type) {
    case 'query_rewrite_finished':
      return `检索问题：${readString(payload, 'rewritten_query')}（${readArray(payload, 'expanded_queries').length || 1} 条 query）`
    case 'retrieval_started':
      return `正在召回 ${readNumber(payload, 'candidate_k') || readNumber(payload, 'top_k') || '-'} 条候选证据`
    case 'retrieval_debug':
      return `扫描 ${readNumber(payload, 'scanned_chunks') || 0} 个 chunk，返回 ${readNumber(payload, 'returned_candidates') || 0} 条候选`
    case 'retrieval_hit':
      return `${rankPrefix(payload)}${readString(payload, 'file_name')}${pageSuffix(readNumber(payload, 'page_no'))}，相关度 ${readNumber(payload, 'score') || '-'}${scoreBreakdown(payload)}`
    case 'retrieval_finished':
      return `检索到 ${readNumber(payload, 'hit_count') || 0} 条候选证据`
    case 'rerank_started':
      return `准备重排 ${readNumber(payload, 'candidate_count') || 0} 条候选证据`
    case 'rerank_finished':
      return `保留 ${readNumber(payload, 'hit_count') || 0} 条关键证据`
    case 'prompt_built':
      return readString(payload, 'instruction') || '已完成回答准备'
    case 'citation':
      return readString(payload, 'source_label') || readString(payload, 'file_name')
    case 'trace_finished':
      return `回答完成，引用来源 ${readNumber(payload, 'citation_count') || 0} 个`
    case 'error':
      return readString(payload, 'message') || '链路执行失败'
    default:
      return eventName(event.event_type)
  }
}

function eventDetail(event: TraceEvent) {
  const payload = event.payload
  if (event.event_type === 'retrieval_hit') {
    return readString(payload, 'content') || readString(payload, 'snippet')
  }
  if (event.event_type === 'query_rewrite_finished') {
    const queries = readArray(payload, 'expanded_queries').filter((item): item is string => typeof item === 'string')
    const structuredIntent = readObject(payload, 'structured_intent')
    const provider = readString(payload, 'provider')
    return [
      `扩展来源：${provider || '-'}`,
      queries.length ? `检索 query：\n${queries.map((query, index) => `${index + 1}. ${query}`).join('\n')}` : '',
      Object.keys(structuredIntent).length ? `结构化意图：\n${JSON.stringify(structuredIntent, null, 2)}` : ''
    ].filter(Boolean).join('\n')
  }
  if (event.event_type === 'retrieval_debug') {
    const queries = readArray(payload, 'queries').filter((item): item is string => typeof item === 'string')
    const combinedQuery = readString(payload, 'combined_query') || readString(payload, 'expanded_query')
    const structuredIntent = readObject(payload, 'structured_intent')
    const provider = readString(payload, 'vector_provider')
    return [
      queries.length ? `检索 query：\n${queries.map((query, index) => `${index + 1}. ${query}`).join('\n')}` : `组合查询：${combinedQuery || '-'}`,
      Object.keys(structuredIntent).length ? `结构化意图：\n${JSON.stringify(structuredIntent, null, 2)}` : '',
      `向量来源：${provider || '-'}`
    ].filter(Boolean).join('\n')
  }
  if (event.event_type === 'rerank_finished') {
    const debug = readObject(payload, 'debug')
    const selected = readArray(debug, 'selected')
    if (!selected.length) return ''
    return selected
      .map((item, index) => {
        const value = typeof item === 'object' && item !== null ? item as Record<string, unknown> : {}
        return `${index + 1}. ${readString(value, 'file_name')}${pageSuffix(readNumber(value, 'page_no'))}：${readString(value, 'content_preview')}`
      })
      .join('\n')
  }
  return ''
}

function toCitation(payload: Record<string, unknown>): Citation | null {
  const documentId = readString(payload, 'document_id')
  const fileName = readString(payload, 'file_name')
  const chunkId = readString(payload, 'chunk_id')
  if (!documentId || !fileName || !chunkId) return null
  return {
    document_id: documentId,
    file_name: fileName,
    page_no: readNumber(payload, 'page_no'),
    chunk_id: chunkId,
    source_label: readString(payload, 'source_label')
  }
}

function readString(payload: Record<string, unknown>, key: string) {
  const value = payload[key]
  return typeof value === 'string' ? value : ''
}

function readNumber(payload: Record<string, unknown>, key: string) {
  const value = payload[key]
  return typeof value === 'number' ? value : null
}

function readObject(payload: Record<string, unknown>, key: string) {
  const value = payload[key]
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function readArray(payload: Record<string, unknown>, key: string) {
  const value = payload[key]
  return Array.isArray(value) ? value : []
}

function rankPrefix(payload: Record<string, unknown>) {
  const rank = readNumber(payload, 'rank')
  return rank ? `#${rank} ` : ''
}

function scoreBreakdown(payload: Record<string, unknown>) {
  const breakdown = readObject(payload, 'score_breakdown')
  const vector = readNumber(breakdown, 'vector')
  const keyword = readNumber(breakdown, 'keyword')
  const structured = readNumber(breakdown, 'structured') ?? readNumber(breakdown, 'intent')
  if (vector === null && keyword === null && structured === null) return ''
  return `（向量 ${formatScore(vector)} / 关键词 ${formatScore(keyword)} / 结构化 ${formatScore(structured)}）`
}

function formatScore(value: number | null) {
  return value === null ? '-' : value.toFixed(2)
}

function pageSuffix(pageNo: number | null) {
  return pageNo ? ` · 第 ${pageNo} 页` : ''
}
</script>

<template>
  <section class="chat-workbench" :class="{ 'trace-collapsed': traceCollapsed }">
    <aside class="session-list">
      <div class="panel-head">
        <span>会话</span>
        <el-tooltip content="新建会话" placement="top">
          <el-button :icon="Plus" circle @click="newSession" />
        </el-tooltip>
      </div>
      <div
        v-for="session in sessions"
        :key="session.session_id"
        class="session-item"
        :class="{ active: session.session_id === activeSessionId }"
        role="button"
        tabindex="0"
        @click="switchSession(session.session_id)"
        @keydown.enter="switchSession(session.session_id)"
        @keydown.space.prevent="switchSession(session.session_id)"
      >
        <div class="session-copy">
          <strong>{{ session.title }}</strong>
          <span>{{ session.updated_at }}</span>
        </div>
        <el-tooltip content="删除会话" placement="right">
          <button
            type="button"
            class="session-delete"
            :aria-label="`删除会话：${session.title}`"
            @click.stop="removeSession(session.session_id)"
          >
            <el-icon><Delete /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </aside>

    <main class="message-pane">
      <header class="page-header compact">
        <div>
          <p class="eyebrow">RAG Chat</p>
          <h1>企业问答</h1>
        </div>
        <el-tooltip content="刷新消息" placement="bottom">
          <el-button :icon="Refresh" circle @click="loadMessages(activeSessionId)" />
        </el-tooltip>
      </header>

      <div ref="messagesRef" class="messages">
        <article v-for="message in messages" :key="message.message_id" class="message-row" :class="message.role">
          <div class="message-badge">{{ message.role === 'user' ? '我' : 'RAG' }}</div>
          <div class="message-body">
            <p>{{ message.content }}</p>
            <div
              v-if="streaming && message.message_id === activeAssistantId && latestEvent"
              class="message-live-event"
            >
              <span>{{ eventName(latestEvent.event_type) }}</span>
              <p>{{ eventSummary(latestEvent) }}</p>
            </div>
            <div v-if="message.role === 'assistant' && message.citations?.length" class="citation-list">
              <span class="citation-heading">引用来源</span>
              <button
                v-for="citation in message.citations"
                :key="citation.chunk_id"
                class="citation-link"
                @click="openCitation(citation)"
              >
                <el-icon><Link /></el-icon>
                <span>{{ citation.source_label || citation.file_name }}{{ pageSuffix(citation.page_no || null) }}</span>
              </button>
            </div>
            <router-link v-if="message.trace_id" :to="`/traces/${message.trace_id}`">查看 Trace</router-link>
          </div>
        </article>
      </div>

      <div class="composer">
        <el-segmented
          v-model="mode"
          :options="[
            { label: '普通问答', value: 'normal' },
            { label: '深度分析', value: 'deep_analysis' }
          ]"
        />
        <el-input
          v-model="question"
          type="textarea"
          resize="none"
          :rows="3"
          placeholder="输入企业知识库问题"
          @keydown.ctrl.enter="sendQuestion"
        />
        <el-button type="primary" :icon="Promotion" :loading="streaming" @click="sendQuestion">发送</el-button>
      </div>
    </main>

    <aside class="trace-rail" :class="{ collapsed: traceCollapsed }">
      <el-tooltip :content="traceCollapsed ? '展开实时链路' : '收起实时链路'" placement="left">
        <button
          type="button"
          class="trace-collapse-button"
          :aria-expanded="!traceCollapsed"
          aria-label="切换实时链路"
          @click="toggleTraceRail"
        >
          <el-icon>
            <ArrowLeft v-if="traceCollapsed" />
            <ArrowRight v-else />
          </el-icon>
        </button>
      </el-tooltip>
      <div v-show="!traceCollapsed" class="panel-head">
        <span>实时链路</span>
        <el-tag effect="plain">{{ visibleEvents.length }} events</el-tag>
      </div>
      <div v-show="!traceCollapsed" class="event-list">
        <div v-for="event in visibleEvents" :key="`${event.trace_id}_${event.seq}`" class="event-item">
          <div class="event-title">
            <span>{{ event.seq }}</span>
            <strong>{{ eventName(event.event_type) }}</strong>
          </div>
          <p>{{ eventSummary(event) }}</p>
          <details v-if="eventDetail(event)" class="event-detail">
            <summary>{{ event.event_type === 'retrieval_hit' ? 'chunk 内容' : '调试详情' }}</summary>
            <pre>{{ eventDetail(event) }}</pre>
          </details>
        </div>
      </div>
    </aside>
  </section>
</template>
