<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { getTraceEvents, listTraces, type TraceEvent, type TraceRecord } from '../../apis/ragKnowledge'

const route = useRoute()
const router = useRouter()
const traces = ref<TraceRecord[]>([])
const events = ref<TraceEvent[]>([])
const activeTraceId = ref('')
const loading = ref(false)

onMounted(loadTraces)

watch(
  () => route.params.traceId,
  async (traceId) => {
    if (typeof traceId === 'string' && traceId) {
      await selectTrace(traceId, false)
    }
  }
)

async function loadTraces() {
  loading.value = true
  try {
    const response = await listTraces()
    traces.value = response.data.data
    const routeTraceId = route.params.traceId
    if (typeof routeTraceId === 'string' && routeTraceId) {
      await selectTrace(routeTraceId, false)
    } else if (traces.value.length) {
      await selectTrace(traces.value[0].trace_id)
    }
  } finally {
    loading.value = false
  }
}

async function selectTrace(traceId: string, push = true) {
  activeTraceId.value = traceId
  const response = await getTraceEvents(traceId)
  events.value = response.data.data
  if (push) {
    router.replace(`/traces/${traceId}`)
  }
}

function chunkContent(event: TraceEvent) {
  if (event.event_type !== 'retrieval_hit') return ''
  const value = event.payload.content || event.payload.snippet
  return typeof value === 'string' ? value : ''
}
</script>

<template>
  <section class="trace-page">
    <aside class="trace-list">
      <div class="panel-head">
        <span>Trace</span>
        <el-tooltip content="刷新 Trace" placement="top">
          <el-button :icon="Refresh" circle @click="loadTraces" />
        </el-tooltip>
      </div>
      <button
        v-for="trace in traces"
        :key="trace.trace_id"
        class="trace-row"
        :class="{ active: trace.trace_id === activeTraceId }"
        @click="selectTrace(trace.trace_id)"
      >
        <strong>{{ trace.question }}</strong>
        <span>{{ trace.mode }} · {{ trace.status }}</span>
      </button>
    </aside>

    <main v-loading="loading" class="trace-detail">
      <header class="page-header">
        <div>
          <p class="eyebrow">Replay</p>
          <h1>Trace 详情</h1>
        </div>
        <el-tag effect="plain">{{ events.length }} events</el-tag>
      </header>

      <div class="timeline">
        <article v-for="event in events" :key="`${event.trace_id}_${event.seq}`" class="timeline-item">
          <div class="timeline-index">{{ event.seq }}</div>
          <div class="timeline-content">
            <div class="timeline-title">
              <strong>{{ event.event_type }}</strong>
              <span>{{ event.created_at }}</span>
            </div>
            <div v-if="chunkContent(event)" class="trace-chunk-content">
              <span>chunk 内容</span>
              <p>{{ chunkContent(event) }}</p>
            </div>
            <pre>{{ JSON.stringify(event.payload, null, 2) }}</pre>
          </div>
        </article>
      </div>
    </main>
  </section>
</template>
