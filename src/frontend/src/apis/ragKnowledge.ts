import { request } from '../utils/request'

export interface ApiResponse<T> {
  status_code: number
  status_message: string
  data: T
}

export interface EnterpriseUser {
  user_id: string
  user_name: string
  role_name: string
  access_level: number
  is_admin: boolean
}

export interface EnterpriseDocument {
  document_id: string
  file_name: string
  file_type: string
  file_size: number
  permission_level: number
  status: string
  uploaded_by: string
  error_message?: string
  created_at: string
}

export interface ParseTraceEvent {
  stage: string
  status: 'pending' | 'running' | 'success' | 'failed'
  message: string
  at?: string | null
}

export interface ParseTracePage {
  page_no: number
  text_length: number
  block_count: number
  parser: string
  sample_blocks: Array<{
    text: string
    confidence?: number | string | null
    bbox?: unknown
  }>
}

export interface ParseTraceChunk {
  chunk_id: string
  page_no?: number | null
  chunk_type: string
  parser: string
  length: number
  preview: string
}

export interface ParseTraceSummary {
  page_count: number
  chunk_count: number
  vision_page_count: number
  text_char_count: number
  parser_counts: Record<string, number>
  chunk_type_counts: Record<string, number>
}

export interface DocumentParseTrace {
  summary: ParseTraceSummary
  events: ParseTraceEvent[]
  pages: ParseTracePage[]
  chunks: ParseTraceChunk[]
}

export interface DocumentParseJob {
  job_id: string
  document_id: string
  status: string
  parser_type: string
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
  trace?: DocumentParseTrace
}

export interface ChatSession {
  session_id: string
  user_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface Citation {
  document_id: string
  file_name: string
  page_no?: number | null
  chunk_id: string
  source_label?: string
}

export interface ChatMessage {
  message_id: string
  session_id: string
  role: 'user' | 'assistant'
  content: string
  trace_id?: string
  citations?: Citation[]
  created_at: string
}

export interface TraceRecord {
  trace_id: string
  session_id: string
  user_id: string
  question: string
  mode: string
  status: string
  created_at: string
  finished_at?: string
}

export interface TraceEvent {
  trace_id: string
  seq: number
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export const enterpriseLogin = (data: { user_name: string; user_password: string }) => {
  return request<ApiResponse<{ access_token: string; user: EnterpriseUser }>>({
    url: '/api/v1/auth/login',
    method: 'POST',
    data
  })
}

export const getMe = () => {
  return request<ApiResponse<EnterpriseUser>>({
    url: '/api/v1/auth/me',
    method: 'GET'
  })
}

export const listDocuments = () => {
  return request<ApiResponse<EnterpriseDocument[]>>({
    url: '/api/v1/documents',
    method: 'GET'
  })
}

export const uploadDocument = (formData: FormData) => {
  return request<ApiResponse<EnterpriseDocument>>({
    url: '/api/v1/documents/upload',
    method: 'POST',
    data: formData,
    timeout: 120000
  })
}

export const deleteDocument = (documentId: string) => {
  return request<ApiResponse<null>>({
    url: `/api/v1/documents/${documentId}`,
    method: 'DELETE'
  })
}

export const getDocumentDownloadUrl = (documentId: string) => {
  return request<ApiResponse<{ url: string }>>({
    url: `/api/v1/documents/${documentId}/download-url`,
    method: 'GET'
  })
}

export const getDocumentParseJob = (documentId: string) => {
  return request<ApiResponse<DocumentParseJob | null>>({
    url: `/api/v1/documents/${documentId}/parse-job`,
    method: 'GET'
  })
}

export const createChatSession = (title?: string) => {
  return request<ApiResponse<ChatSession>>({
    url: '/api/v1/chat/sessions',
    method: 'POST',
    data: { title }
  })
}

export const listChatSessions = () => {
  return request<ApiResponse<ChatSession[]>>({
    url: '/api/v1/chat/sessions',
    method: 'GET'
  })
}

export const deleteChatSession = (sessionId: string) => {
  return request<ApiResponse<null>>({
    url: `/api/v1/chat/sessions/${sessionId}`,
    method: 'DELETE'
  })
}

export const listChatMessages = (sessionId: string) => {
  return request<ApiResponse<ChatMessage[]>>({
    url: `/api/v1/chat/sessions/${sessionId}/messages`,
    method: 'GET'
  })
}

export const listTraces = () => {
  return request<ApiResponse<TraceRecord[]>>({
    url: '/api/v1/traces',
    method: 'GET'
  })
}

export const getTraceEvents = (traceId: string) => {
  return request<ApiResponse<TraceEvent[]>>({
    url: `/api/v1/traces/${traceId}/events`,
    method: 'GET'
  })
}

export async function streamChat(
  payload: { session_id: string; question: string; mode: 'normal' | 'deep_analysis' },
  onEvent: (event: TraceEvent) => void
) {
  const token = localStorage.getItem('token')
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(payload)
  })

  if (!response.ok || !response.body) {
    throw new Error(`问答流启动失败：${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const packets = buffer.split('\n\n')
    buffer = packets.pop() || ''
    for (const packet of packets) {
      const dataLine = packet.split('\n').find((line) => line.startsWith('data: '))
      if (dataLine) {
        onEvent(JSON.parse(dataLine.slice(6)) as TraceEvent)
      }
    }
  }
}
