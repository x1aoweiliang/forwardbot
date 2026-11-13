<template>
  <div class="app-container archive-library">
    <div class="library-heading">
      <div><h2>素材库</h2><p>自动收集群消息，保留原文，整理并归档图片和视频。</p></div>
      <div class="library-actions" v-hasPermi="['tg:archive:write']">
        <el-button v-if="quarkStorages.length" @click="openQuark">夸克账号</el-button>
        <el-button :disabled="!selected.length" @click="clearSelection">清除选择 {{ selected.length || '' }}</el-button>
        <el-button type="danger" plain :disabled="!selected.length" :loading="cleaning" @click="cleanupLocal(selected)">清理所选本地素材</el-button>
        <el-button type="primary" :disabled="!selected.length || selected.some(row => row.downloadState !== 'ready' || !row.readyCount)" @click="openArchive(selected)">归档所选 {{ selected.length || '' }}</el-button>
      </div>
    </div>
    <el-form :inline="true" @submit.prevent="search">
      <el-form-item label="账号"><el-select v-model="query.accountId" clearable placeholder="全部账号" @change="changeAccount">
        <el-option v-for="item in accounts" :key="item.accountId" :label="item.accountName" :value="item.accountId" />
      </el-select></el-form-item>
      <el-form-item label="来源群"><el-select v-model="query.sourceChatPk" filterable clearable placeholder="全部来源" @change="search">
        <el-option v-for="item in chats" :key="item.chatPk" :label="item.chatTitle" :value="item.chatPk" />
      </el-select></el-form-item>
      <el-form-item label="状态"><el-select v-model="query.state" clearable placeholder="全部状态" @change="search">
        <el-option v-for="item in filters" :key="item.value" :label="item.label" :value="item.value" />
      </el-select></el-form-item>
      <el-form-item><el-input v-model="query.keyword" placeholder="搜索原文" clearable @keyup.enter="search" /></el-form-item>
      <el-form-item><el-button type="primary" @click="search">搜索</el-button><el-button @click="refresh">刷新</el-button></el-form-item>
    </el-form>
    <el-table ref="libraryTable" v-loading="loading" :data="rows" row-key="messageId" @selection-change="selected = $event">
      <el-table-column type="selection" width="48" :reserve-selection="true" />
      <el-table-column label="消息" min-width="300"><template #default="{ row }">
        <el-button link type="primary" @click="openDetail(row)">{{ row.sourceChatTitle || row.sourceChatId }} · #{{ row.telegramMessageId }}</el-button>
        <p class="message-excerpt">{{ row.messageText || '无文字说明' }}</p>
      </template></el-table-column>
      <el-table-column label="素材" width="160"><template #default="{ row }">
        <div>{{ row.readyCount }} / {{ row.mediaCount }} 个已就绪</div><small>{{ formatBytes(row.downloadedBytes) }} / {{ formatBytes(row.totalBytes) }}</small>
      </template></el-table-column>
      <el-table-column label="下载" width="120"><template #default="{ row }"><el-tag :type="tagType(row.downloadState)">{{ label(row.downloadState) }}</el-tag></template></el-table-column>
      <el-table-column label="归档" width="110"><template #default="{ row }"><el-tag :type="row.archived ? 'success' : 'info'">{{ row.archived ? '已归档' : '待归档' }}</el-tag></template></el-table-column>
      <el-table-column label="消息时间" width="180"><template #default="{ row }">{{ parseTime(row.sentAt || row.createTime) }}</template></el-table-column>
      <el-table-column label="操作" width="290"><template #default="{ row }">
        <el-button link type="primary" @click="openDetail(row)">查看</el-button>
        <el-button v-hasPermi="['tg:archive:write']" link type="primary" :disabled="row.downloadState !== 'ready' || !row.readyCount" @click="openArchive([row])">归档</el-button>
        <el-button v-if="['failed', 'deleted'].includes(row.downloadState)" v-hasPermi="['tg:archive:write']" link type="warning" @click="retryDownload(row.messageId)">{{ row.downloadState === 'deleted' ? '重新下载' : '重试' }}</el-button>
        <el-button v-hasPermi="['tg:archive:write']" link type="danger" :disabled="cleaning || !row.mediaCount" @click="cleanupLocal([row])">清理本地</el-button>
      </template></el-table-column>
      <template #empty><el-empty description="还没有采集记录。在监听规则中开启自动下载后，新消息会出现在这里。" /></template>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="query.pageNum" v-model:limit="query.pageSize" @pagination="refresh" />

    <el-drawer v-model="detailOpen" title="消息素材" size="min(820px, 100vw)" @closed="clearDetail">
      <div v-loading="detailLoading" v-if="detail">
        <div class="detail-source">{{ detail.sourceChatTitle }} · #{{ detail.telegramMessageId }}<small>{{ parseTime(detail.sentAt || detail.createTime) }}</small></div>
        <pre class="original-text">{{ detail.messageText || '无文字说明' }}</pre>
        <div class="media-grid">
          <article v-for="media in detail.medias" :key="media.mediaId" class="media-card">
            <template v-if="media.downloadStatus === 'ready' && previewUrls[media.mediaId]">
              <el-image v-if="isImage(media)" :src="previewUrls[media.mediaId]" fit="contain" :preview-src-list="[previewUrls[media.mediaId]]" preview-teleported />
              <video v-else-if="media.mimeType?.startsWith('video/')" :src="previewUrls[media.mediaId]" controls preload="metadata" />
              <a v-else :href="previewUrls[media.mediaId]" target="_blank" rel="noopener noreferrer">打开素材</a>
            </template>
            <div v-else class="media-placeholder">{{ label(media.downloadStatus) }}</div>
            <div class="media-info"><strong :title="media.fileName">{{ media.fileName }}</strong><small>{{ formatBytes(media.fileSize) }}</small>
              <el-progress v-if="['queued', 'downloading', 'retry'].includes(media.downloadStatus)" :percentage="percent(media)" />
              <p v-if="media.caption">{{ media.caption }}</p><p v-if="media.lastError" class="error-text">{{ media.lastError }}</p>
              <el-button v-if="media.downloadStatus === 'ready'" link type="primary" @click="loadPreview(media, true)">刷新预览</el-button>
            </div>
          </article>
        </div>
        <div class="detail-actions">
          <el-button v-hasPermi="['tg:archive:write']" :disabled="!detail.medias.some(m => ['failed', 'retry', 'deleted'].includes(m.downloadStatus)) || detail.medias.some(m => m.downloadStatus === 'deleting')" @click="retryDownload(detail.messageId)">{{ detail.medias.some(m => m.downloadStatus === 'deleted') ? '重新下载已清理素材' : '重试失败下载' }}</el-button>
          <el-button v-hasPermi="['tg:archive:write']" type="danger" plain :loading="cleaning" :disabled="!detail.medias.length" @click="cleanupLocal([detail])">清理本地素材</el-button>
          <el-button v-hasPermi="['tg:archive:write']" type="primary" :disabled="!detailReady" @click="openArchive([detail])">归档这条消息</el-button>
        </div>
        <h3>归档记录</h3>
        <el-empty v-if="!detail.archives.length" description="尚未归档" :image-size="70" />
        <article v-for="task in detail.archives" :key="task.taskId" class="archive-record">
          <div class="record-heading"><strong>{{ storageName(task.storageId) }}</strong><el-tag :type="tagType(task.status)">{{ label(task.status) }}</el-tag></div>
          <p class="archive-path">/{{ [task.targetPath, task.packageName].filter(Boolean).join('/') }}</p>
          <small v-if="task.mediaRevision !== detail.mediaRevision">这份归档之后有新素材到达，可以再次归档完整消息。</small>
          <div v-for="file in task.files" :key="file.name" class="archive-file"><span>{{ file.name }}</span><span>{{ label(file.status) }}</span></div>
          <p v-if="task.lastError" class="error-text">{{ task.lastError }}</p>
          <el-button v-if="['failed', 'retry'].includes(task.status)" v-hasPermi="['tg:archive:write']" link type="primary" @click="retryArchive(task.taskId)">重试未完成文件</el-button>
        </article>
      </div>
    </el-drawer>

    <el-dialog v-model="quarkOpen" title="夸克账号" width="440px" @closed="closeQuark" :close-on-click-modal="false">
      <el-select v-model="quarkStorageId" style="width:100%" :disabled="quarkLoading" @change="checkQuark">
        <el-option v-for="storage in quarkStorages" :key="storage.id" :value="storage.id" :label="storage.name" />
      </el-select>
      <div v-loading="quarkLoading" class="quark-login">
        <el-alert v-if="quarkError" :title="quarkError" type="error" :closable="false" />
        <p v-if="quarkStatus.loggedIn">已登录：{{ quarkStatus.nickname }}</p>
        <template v-if="qrImage"><img :src="qrImage" alt="夸克登录二维码" width="240" height="240" /><p>使用夸克 App 扫码并确认登录，二维码 5 分钟内有效。</p></template>
        <p v-else-if="!quarkStatus.loggedIn && !quarkLoading">扫码后可浏览网盘目录并上传素材。</p>
        <el-button type="primary" :disabled="!quarkStorageId || quarkLoading" @click="startQuarkLogin">{{ qrImage ? '刷新二维码' : quarkStatus.loggedIn ? '重新扫码登录' : '获取登录二维码' }}</el-button>
      </div>
      <p class="quark-note">登录凭据仅保存在服务器。重新登录请使用同一个夸克账号。</p>
    </el-dialog>

    <el-dialog v-model="archiveOpen" title="归档素材" width="620px" :close-on-click-modal="false">
      <p>将 {{ archiveIds.length }} 条消息的图片和视频按消息从早到晚直接上传到所选目录，文件以消息文字命名。原文保留在素材库中。</p>
      <el-form label-position="top">
        <el-form-item label="存储位置"><el-select v-model="archiveForm.storageId" :disabled="preparingArchive || creatingFolder || submitting || foldersLoading" placeholder="选择夸克或 S3" style="width:100%" @change="changeStorage">
          <el-option v-for="storage in storageOptions" :key="storage.id" :label="`${storage.name} · ${storage.type === 'quark' ? '夸克' : 'S3'}`" :value="storage.id" />
        </el-select></el-form-item>
      </el-form>
      <el-alert v-if="!storageOptions.length" title="尚未配置存储目标，请联系管理员完成存储配置。" type="info" :closable="false" />
      <template v-if="archiveForm.storageId">
        <div class="folder-toolbar"><el-button :disabled="!folderPath || foldersLoading" @click="browse(parentPath(folderPath))">上一级</el-button><span>/{{ folderPath }}</span><el-button :disabled="foldersLoading" @click="newFolderOpen = !newFolderOpen">新建目录</el-button></div>
        <div v-if="newFolderOpen" class="new-folder"><el-input v-model="newFolderName" placeholder="文件夹名称" @keyup.enter="createFolder" /><el-button :loading="creatingFolder" @click="createFolder">创建</el-button></div>
        <div v-loading="foldersLoading" class="folder-list">
          <el-button v-for="folder in folderOptions" :key="folder.path" text class="folder-row" @click="browse(folder.path)"><el-icon><Folder /></el-icon><span>{{ folder.name }}</span><el-icon><ArrowRight /></el-icon></el-button>
          <el-empty v-if="!folderOptions.length && !foldersLoading" description="当前目录没有子文件夹，可以直接归档到这里。" :image-size="65" />
        </div>
        <el-alert v-if="folderError" :title="folderError" type="error" :closable="false" />
        <p class="destination">归档到：{{ storageName(archiveForm.storageId) }} /{{ folderPath }}</p>
      </template>
      <template #footer><el-button @click="archiveOpen = false">取消</el-button><el-button type="primary" :loading="submitting" :disabled="preparingArchive || creatingFolder || !archiveForm.storageId || foldersLoading || !!folderError" @click="submitArchive">开始归档</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { accountApi, archiveApi, chatApi } from '@/api/tg';

const { proxy } = getCurrentInstance();
const libraryTable = ref(null), cleaning = ref(false);
const rows = ref([]), total = ref(0), selected = ref([]), loading = ref(false);
const accounts = ref([]), chats = ref([]), storageOptions = ref([]);
const query = reactive({ pageNum: 1, pageSize: 20, state: undefined, keyword: '', accountId: undefined, sourceChatPk: undefined });
const detailOpen = ref(false), detailLoading = ref(false), detail = ref(null), previewUrls = reactive({});
const archiveOpen = ref(false), archiveIds = ref([]), archiveForm = reactive({ storageId: '' });
const folderPath = ref(''), folderOptions = ref([]), foldersLoading = ref(false), folderError = ref('');
const newFolderOpen = ref(false), newFolderName = ref(''), creatingFolder = ref(false), submitting = ref(false), preparingArchive = ref(false);
const quarkOpen = ref(false), quarkStorageId = ref(''), quarkStatus = ref({}), quarkLoading = ref(false), quarkError = ref(''), qrImage = ref('');
const quarkStorages = computed(() => storageOptions.value.filter(item => item.type === 'quark'));
let qrTimer, qrSession = null, quarkSequence = 0;
let pollTimer, disposed = false, librarySequence = 0, folderSequence = 0, detailSequence = 0;
const filters = [
  { value: 'unarchived', label: '待归档' }, { value: 'ready', label: '下载完成' },
  { value: 'pending', label: '正在下载 / 等待' }, { value: 'failed', label: '下载失败' },
  { value: 'deleted', label: '本地已清理' }, { value: 'deleting', label: '清理中 / 待重试' },
  { value: 'archived', label: '已归档' }, { value: 'blocked', label: '已拦截' },
];
const labels = { deleted: '本地已清理', deleting: '清理中 / 待重试', ready: '已就绪', pending: '下载中 / 等待', failed: '失败', blocked: '已拦截', queued: '排队中', downloading: '下载中', retry: '等待重试', skipped: '非图片或视频', completed: '已完成', uploading: '上传中', disabled: '未开启' };
const detailReady = computed(() => detail.value && detail.value.isSensitive !== 'Y' && detail.value.medias.some(m => m.downloadStatus === 'ready') && detail.value.medias.every(m => ['ready', 'skipped'].includes(m.downloadStatus)));
const label = value => labels[value] || value;
const tagType = value => ['ready', 'completed'].includes(value) ? 'success' : value === 'failed' ? 'danger' : ['blocked', 'retry'].includes(value) ? 'warning' : 'info';
const storageName = id => storageOptions.value.find(item => item.id === id)?.name || id;
const isImage = media => media.mimeType?.startsWith('image/') && media.mimeType !== 'image/svg+xml';
const parentPath = path => path.split('/').slice(0, -1).join('/');
function formatBytes(value) { if (!value) return '0 B'; const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024))); return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${['B', 'KB', 'MB', 'GB'][unit]}`; }
function percent(media) { return media.fileSize ? Math.min(100, Math.round(100 * media.downloadedBytes / media.fileSize)) : 0; }
async function refresh(silent = false) {
  const sequence = ++librarySequence;
  if (silent !== true) loading.value = true;
  try { const result = await archiveApi.library(query); if (sequence === librarySequence) { rows.value = result.rows; total.value = result.total; } }
  finally { if (sequence === librarySequence) loading.value = false; }
}
function search() { query.pageNum = 1; refresh(); }
function clearSelection() { libraryTable.value?.clearSelection(); selected.value = []; }
async function changeAccount() {
  query.sourceChatPk = undefined;
  const result = await chatApi.list({ pageNum: 1, pageSize: 1000, accountId: query.accountId, canListen: 'Y' });
  chats.value = result.rows || []; search();
}
async function loadPreview(media, force = false) {
  if (media.downloadStatus !== 'ready' || (!force && previewUrls[media.mediaId])) return;
  const messageId = detail.value?.messageId;
  const result = await archiveApi.ticket(media.mediaId);
  if (detailOpen.value && detail.value?.messageId === messageId && detail.value.medias.some(item => item.mediaId === media.mediaId && item.downloadStatus === 'ready')) previewUrls[media.mediaId] = archiveApi.contentUrl(media.mediaId, result.data.ticket);
}
async function refreshDetail() {
  if (!detail.value || !detailOpen.value) return;
  const sequence = ++detailSequence, id = detail.value.messageId;
  const result = await archiveApi.detail(id);
  if (sequence !== detailSequence || !detailOpen.value) return;
  detail.value = result.data;
  detail.value.medias.filter(media => media.downloadStatus !== 'ready').forEach(media => delete previewUrls[media.mediaId]);
  await Promise.allSettled(detail.value.medias.map(media => loadPreview(media)));
}
async function openDetail(row) {
  clearDetail(); detail.value = { ...row, medias: [], archives: [] }; detailOpen.value = true; detailLoading.value = true;
  try { await refreshDetail(); } finally { detailLoading.value = false; }
}
function clearDetail() { detailSequence++; detail.value = null; Object.keys(previewUrls).forEach(key => delete previewUrls[key]); }
async function retryDownload(id) { const result = await archiveApi.retryDownload(id); proxy.$modal.msgSuccess(result.msg); await Promise.all([refresh(true), refreshDetail()]); }
async function retryArchive(id) { const result = await archiveApi.retryArchive(id); proxy.$modal.msgSuccess(result.msg); await refreshDetail(); }
async function cleanupLocal(messages) {
  const ids = messages.map(item => item.messageId);
  const unarchived = messages.some(item => item.archived !== true && !item.archives?.some(task => task.status === 'completed' && task.mediaRevision === item.mediaRevision));
  try {
    await proxy.$modal.confirm(`将删除 ${ids.length} 条消息的本地图片、视频和下载残片，保留文字及云端归档。${unarchived ? '其中包含尚未归档的消息，清理后只能在源消息仍可访问时重新下载。' : '需要时可手动重新下载。'}确认清理？`);
  } catch { return; }
  cleaning.value = true;
  try {
    const result = await archiveApi.cleanup({ messageIds: ids });
    clearSelection();
    Object.keys(previewUrls).forEach(key => delete previewUrls[key]);
    const message = `${result.msg}，本次删除文件 ${formatBytes(result.data.freedBytes)}`;
    if (result.data.errors.length) proxy.$modal.msgWarning(message); else proxy.$modal.msgSuccess(message);
    await Promise.all([refresh(true), refreshDetail()]);
  } finally { cleaning.value = false; }
}
function cancelQr() {
  clearTimeout(qrTimer);
  const session = qrSession; qrSession = null; qrImage.value = '';
  if (session) archiveApi.quarkCancel(session.storageId, session.id).catch(() => {});
}
function closeQuark() { quarkSequence++; cancelQr(); quarkLoading.value = false; }
async function openQuark() {
  quarkStorageId.value = quarkStorages.value.some(item => item.id === quarkStorageId.value) ? quarkStorageId.value : quarkStorages.value[0]?.id || '';
  quarkOpen.value = true; await checkQuark();
}
async function checkQuark() {
  const sequence = ++quarkSequence;
  cancelQr(); quarkStatus.value = {}; quarkError.value = ''; quarkLoading.value = true;
  try {
    const result = await archiveApi.quarkStatus(quarkStorageId.value);
    if (sequence === quarkSequence) { quarkStatus.value = result.data; quarkError.value = result.data.message || ''; }
  } catch (error) { if (sequence === quarkSequence) quarkError.value = error.message || '登录状态读取失败'; }
  finally { if (sequence === quarkSequence) quarkLoading.value = false; }
}
async function startQuarkLogin() {
  const sequence = ++quarkSequence, storageId = quarkStorageId.value;
  cancelQr(); quarkError.value = ''; quarkLoading.value = true;
  try {
    const result = await archiveApi.quarkLogin(storageId);
    if (sequence !== quarkSequence || !quarkOpen.value) { await archiveApi.quarkCancel(storageId, result.data.sessionId); return; }
    qrSession = { id: result.data.sessionId, storageId, expires: Date.now() + result.data.expiresIn * 1000 };
    qrImage.value = result.data.image; qrTimer = setTimeout(() => pollQuark(sequence), 2000);
  } catch (error) { if (sequence === quarkSequence) quarkError.value = error.message || '获取二维码失败'; }
  finally { if (sequence === quarkSequence) quarkLoading.value = false; }
}
async function pollQuark(sequence) {
  if (sequence !== quarkSequence || !quarkOpen.value || !qrSession) return;
  if (Date.now() > qrSession.expires) { cancelQr(); quarkError.value = '二维码已过期，请重新获取'; return; }
  try {
    const result = await archiveApi.quarkPoll(qrSession.storageId, qrSession.id);
    if (sequence !== quarkSequence || !quarkOpen.value) return;
    if (result.data.status === 'authorized') {
      cancelQr(); quarkStatus.value = { loggedIn: true, nickname: result.data.nickname }; proxy.$modal.msgSuccess('夸克登录成功');
      if (archiveOpen.value && archiveForm.storageId === quarkStorageId.value) await browse(folderPath.value);
      return;
    }
    if (result.data.status === 'expired') { cancelQr(); quarkError.value = '二维码已过期，请重新获取'; return; }
    qrTimer = setTimeout(() => pollQuark(sequence), 2000);
  } catch (error) { if (sequence === quarkSequence) { cancelQr(); quarkError.value = error.message || '登录验证失败，请重新扫码'; } }
}
async function openArchive(messages) {
  archiveIds.value = messages.map(item => item.messageId); preparingArchive.value = true; archiveOpen.value = true;
  folderPath.value = ''; folderOptions.value = []; newFolderOpen.value = false; newFolderName.value = ''; folderError.value = '';
  try {
    const result = await archiveApi.storages(); storageOptions.value = result.data;
    if (!storageOptions.value.some(item => item.id === archiveForm.storageId)) archiveForm.storageId = storageOptions.value[0]?.id || '';
    await changeStorage();
  } catch (error) { folderError.value = error.message || '存储位置读取失败'; }
  finally { preparingArchive.value = false; }
}
async function changeStorage() {
  folderPath.value = ''; folderOptions.value = []; newFolderOpen.value = false; folderError.value = '';
  const storageId = archiveForm.storageId;
  if (!storageId) return;
  foldersLoading.value = true;
  try {
    let path = '';
    if (storageOptions.value.find(item => item.id === storageId)?.type === 'quark') {
      const now = new Date();
      const date = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
      await archiveApi.createFolder({ storageId, parentPath: '', name: '飞机' });
      const result = await archiveApi.createFolder({ storageId, parentPath: '飞机', name: date });
      path = result.data.path;
    }
    await browse(path);
  } catch (error) { folderError.value = error.message || '默认归档目录准备失败'; }
  finally { foldersLoading.value = false; }
}
async function browse(path) {
  const sequence = ++folderSequence;
  foldersLoading.value = true; folderError.value = '';
  try { const result = await archiveApi.folders(archiveForm.storageId, path); if (sequence === folderSequence) { folderPath.value = result.data.path; folderOptions.value = result.data.folders; } }
  catch (error) { if (sequence === folderSequence) folderError.value = error.message || '目录读取失败'; }
  finally { if (sequence === folderSequence) foldersLoading.value = false; }
}
async function createFolder() {
  if (!newFolderName.value.trim()) return;
  creatingFolder.value = true;
  try { const result = await archiveApi.createFolder({ storageId: archiveForm.storageId, parentPath: folderPath.value, name: newFolderName.value.trim() }); newFolderName.value = ''; newFolderOpen.value = false; await browse(result.data.path); }
  finally { creatingFolder.value = false; }
}
async function submitArchive() {
  submitting.value = true;
  try {
    const result = await archiveApi.submit({ messageIds: archiveIds.value, storageId: archiveForm.storageId, targetPath: folderPath.value });
    clearSelection();
    proxy.$modal.msgSuccess(result.msg); archiveOpen.value = false;
    await Promise.all([refresh(true), refreshDetail()]);
  }
  finally { submitting.value = false; }
}
onMounted(async () => {
  await Promise.allSettled([
    refresh(), accountApi.list({ pageNum: 1, pageSize: 1000 }).then(result => { accounts.value = result.rows || []; }),
    chatApi.list({ pageNum: 1, pageSize: 1000, canListen: 'Y' }).then(result => { chats.value = result.rows || []; }),
    archiveApi.storages().then(result => { storageOptions.value = result.data; }),
  ]);
  if (!disposed) pollTimer = setInterval(() => { if (!document.hidden) Promise.allSettled([refresh(true), refreshDetail()]); }, 5000);
});
onUnmounted(() => { disposed = true; closeQuark(); clearInterval(pollTimer); librarySequence++; folderSequence++; clearDetail(); });
</script>

<style scoped>
.library-heading,.record-heading,.folder-toolbar,.archive-file { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.library-actions { display:flex; gap:8px; flex-wrap:wrap; } .quark-login { min-height:160px; text-align:center; padding:20px 0; } .quark-login img { display:block; margin:12px auto; } .quark-note { color:var(--el-text-color-secondary); font-size:13px; }
.library-heading { margin-bottom:24px; } h2 { margin:0 0 8px; font-size:24px; } .library-heading p,small { color:var(--el-text-color-secondary); }
.library-heading p { margin:0; } .el-select { width:180px; }
.message-excerpt { margin:6px 0; white-space:pre-wrap; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.detail-source { font-size:16px; font-weight:600; } .detail-source small { display:block; margin-top:8px; font-weight:400; }
.original-text { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; line-height:1.7; padding:16px; background:var(--el-fill-color-light); border-radius:8px; }
.media-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.media-card,.archive-record { border:1px solid var(--el-border-color); border-radius:8px; overflow:hidden; }
.media-card video,.media-card .el-image,.media-placeholder { width:100%; height:220px; background:var(--el-fill-color-light); }
.media-placeholder { display:flex; align-items:center; justify-content:center; color:var(--el-text-color-secondary); }
.media-info { padding:12px; } .media-info strong { display:block; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; } .media-info p { white-space:pre-wrap; overflow-wrap:anywhere; }
.detail-actions { display:flex; justify-content:flex-end; gap:8px; margin:20px 0; } .archive-record { margin:12px 0; padding:16px; }
.archive-path { overflow-wrap:anywhere; color:var(--el-text-color-secondary); } .archive-file { font-size:13px; padding:6px 0; } .archive-file span:first-child { overflow-wrap:anywhere; min-width:0; }
.error-text { color:var(--el-color-danger); font-size:13px; } .folder-toolbar { margin:16px 0; } .folder-toolbar span { flex:1; overflow-wrap:anywhere; }
.folder-list { min-height:150px; max-height:280px; overflow:auto; border:1px solid var(--el-border-color); border-radius:6px; padding:8px; }
.folder-row { display:flex; width:100%; justify-content:flex-start; margin:0!important; height:42px; } .folder-row span { flex:1; text-align:left; padding:0 8px; }
.new-folder { display:flex; gap:8px; margin-bottom:12px; } .destination { overflow-wrap:anywhere; font-weight:600; }
@media(max-width:600px) { .media-grid { grid-template-columns:1fr; } .library-heading { align-items:flex-start; flex-direction:column; } }
</style>
