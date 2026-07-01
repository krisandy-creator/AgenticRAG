<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Link, Refresh, UploadFilled, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import {
  deleteDocument,
  getDocumentDownloadUrl,
  getDocumentParseJob,
  listDocuments,
  uploadDocument,
  type DocumentParseJob,
  type EnterpriseDocument,
  type EnterpriseUser,
  type ParseTraceEvent
} from '../../apis/ragKnowledge'

const documents = ref<EnterpriseDocument[]>([])
const loading = ref(false)
const uploading = ref(false)
const uploadDialogVisible = ref(false)
const parseDrawerVisible = ref(false)
const parseLoading = ref(false)
const selectedFile = ref<File | null>(null)
const selectedDocument = ref<EnterpriseDocument | null>(null)
const parseJob = ref<DocumentParseJob | null>(null)
const permissionLevel = ref(10)
const user = computed<EnterpriseUser | null>(() => {
  const cached = localStorage.getItem('userInfo')
  return cached ? JSON.parse(cached) : null
})

const canManage = computed(() => Boolean(user.value?.is_admin))
const parseSummary = computed(() => parseJob.value?.trace?.summary)
const parseEvents = computed(() => parseJob.value?.trace?.events || [])
const parsePages = computed(() => parseJob.value?.trace?.pages || [])
const parseChunks = computed(() => parseJob.value?.trace?.chunks || [])

onMounted(loadDocuments)

async function loadDocuments() {
  loading.value = true
  try {
    const response = await listDocuments()
    documents.value = response.data.data
  } finally {
    loading.value = false
  }
}

function chooseFile(uploadFile: UploadFile) {
  selectedFile.value = uploadFile.raw ?? null
}

async function submitUpload() {
  if (!selectedFile.value) {
    ElMessage.warning('请选择文档')
    return
  }
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('permission_level', String(permissionLevel.value))
    await uploadDocument(formData)
    ElMessage.success('已上传，解析任务已进入后台')
    uploadDialogVisible.value = false
    selectedFile.value = null
    await loadDocuments()
  } finally {
    uploading.value = false
  }
}

async function openSource(row: EnterpriseDocument) {
  const response = await getDocumentDownloadUrl(row.document_id)
  const url = response.data.data.url
  if (url.startsWith('http')) {
    window.open(url, '_blank')
  } else {
    ElMessage.info('本地镜像文件已保存，当前没有可公开访问的 OSS URL')
  }
}

async function openParseTrace(row: EnterpriseDocument) {
  selectedDocument.value = row
  parseDrawerVisible.value = true
  await refreshParseTrace()
}

async function refreshParseTrace() {
  if (!selectedDocument.value) return
  parseLoading.value = true
  try {
    const response = await getDocumentParseJob(selectedDocument.value.document_id)
    parseJob.value = response.data.data
  } finally {
    parseLoading.value = false
  }
}

async function removeDocument(row: EnterpriseDocument) {
  await ElMessageBox.confirm(`删除文档「${row.file_name}」？`, '确认删除', { type: 'warning' })
  await deleteDocument(row.document_id)
  ElMessage.success('已删除')
  await loadDocuments()
}

function statusType(status: string) {
  if (status === 'ready' || status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'parsing' || status === 'running') return 'warning'
  return 'info'
}

function timelineType(status: ParseTraceEvent['status']) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function formatSize(size: number) {
  if (size > 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size > 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function formatMap(record: Record<string, number> | undefined) {
  if (!record || !Object.keys(record).length) return '暂无'
  return Object.entries(record)
    .map(([key, value]) => `${key} × ${value}`)
    .join('，')
}
</script>

<template>
  <section class="workspace-page documents-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">Documents</p>
        <h1>文档管理</h1>
      </div>
      <div class="header-actions">
        <el-tooltip content="刷新列表" placement="bottom">
          <el-button :icon="Refresh" circle @click="loadDocuments" />
        </el-tooltip>
        <el-button v-if="canManage" type="primary" :icon="UploadFilled" @click="uploadDialogVisible = true">
          上传文档
        </el-button>
      </div>
    </header>

    <el-table v-loading="loading" class="data-table" :data="documents" height="calc(100vh - 188px)">
      <el-table-column prop="file_name" label="文件名" min-width="260" show-overflow-tooltip />
      <el-table-column prop="file_type" label="类型" width="96" />
      <el-table-column label="权限等级" width="112">
        <template #default="{ row }">L{{ row.permission_level }}</template>
      </el-table-column>
      <el-table-column label="状态" width="118">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" effect="plain">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="112">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="上传时间" width="190" />
      <el-table-column label="操作" width="210" fixed="right">
        <template #default="{ row }">
          <el-tooltip content="解析详情" placement="top">
            <el-button :icon="View" circle @click="openParseTrace(row)" />
          </el-tooltip>
          <el-tooltip content="打开源文件" placement="top">
            <el-button :icon="Link" circle @click="openSource(row)" />
          </el-tooltip>
          <el-tooltip v-if="canManage" content="删除" placement="top">
            <el-button :icon="Delete" circle type="danger" @click="removeDocument(row)" />
          </el-tooltip>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="uploadDialogVisible" title="上传企业文档" width="520px">
      <el-upload drag :auto-upload="false" :limit="1" :on-change="chooseFile">
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div class="upload-text">拖入或选择 docx、PDF、Excel</div>
      </el-upload>
      <div class="permission-row">
        <span>文档权限等级</span>
        <el-input-number v-model="permissionLevel" :min="10" :max="100" :step="10" />
      </div>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">提交解析</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="parseDrawerVisible"
      :title="selectedDocument ? `解析详情：${selectedDocument.file_name}` : '解析详情'"
      size="560px"
    >
      <div v-loading="parseLoading" class="parse-trace-drawer">
        <template v-if="parseJob">
          <div class="drawer-toolbar">
            <el-tag :type="statusType(parseJob.status)" effect="plain">{{ parseJob.status }}</el-tag>
            <el-button :icon="Refresh" size="small" @click="refreshParseTrace">刷新</el-button>
          </div>

          <div v-if="parseSummary" class="parse-summary-grid">
            <div>
              <strong>{{ parseSummary.page_count }}</strong>
              <span>页面</span>
            </div>
            <div>
              <strong>{{ parseSummary.chunk_count }}</strong>
              <span>Chunks</span>
            </div>
            <div>
              <strong>{{ parseSummary.vision_page_count }}</strong>
              <span>多模态页</span>
            </div>
            <div>
              <strong>{{ parseSummary.text_char_count }}</strong>
              <span>字符</span>
            </div>
          </div>

          <section class="parse-section">
            <h3>解析链路</h3>
            <el-timeline>
              <el-timeline-item
                v-for="event in parseEvents"
                :key="event.stage"
                :type="timelineType(event.status)"
                :timestamp="event.at || ''"
              >
                <strong>{{ event.stage }}</strong>
                <p>{{ event.message }}</p>
              </el-timeline-item>
            </el-timeline>
          </section>

          <section class="parse-section">
            <h3>切分统计</h3>
            <p>解析器：{{ formatMap(parseSummary?.parser_counts) }}</p>
            <p>Chunk 类型：{{ formatMap(parseSummary?.chunk_type_counts) }}</p>
          </section>

          <section v-if="parsePages.length" class="parse-section">
            <h3>页面明细</h3>
            <div v-for="page in parsePages" :key="page.page_no" class="parse-page-card">
              <div class="parse-card-title">
                <strong>第 {{ page.page_no }} 页</strong>
                <span>{{ page.parser }} · {{ page.text_length }} 字符 · {{ page.block_count }} blocks</span>
              </div>
              <ul v-if="page.sample_blocks.length" class="parse-block-list">
                <li v-for="(block, index) in page.sample_blocks" :key="index">
                  <span>{{ block.text || '空文本块' }}</span>
                  <small v-if="block.confidence !== null && block.confidence !== undefined">
                    置信度 {{ block.confidence }}
                  </small>
                </li>
              </ul>
            </div>
          </section>

          <section v-if="parseChunks.length" class="parse-section">
            <h3>Chunk 样例</h3>
            <div v-for="chunk in parseChunks" :key="chunk.chunk_id" class="chunk-preview">
              <div>
                <strong>{{ chunk.chunk_type }}</strong>
                <span>{{ chunk.parser }} · {{ chunk.length }} 字符</span>
              </div>
              <p>{{ chunk.preview }}</p>
            </div>
          </section>
        </template>
        <el-empty v-else description="暂无解析任务" />
      </div>
    </el-drawer>
  </section>
</template>
