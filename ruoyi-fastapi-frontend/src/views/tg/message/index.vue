<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="账号">
        <el-select v-model="queryParams.accountId" clearable filterable style="width: 220px" @change="handleAccountChange">
          <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
        </el-select>
      </el-form-item>
      <el-form-item label="来源">
        <el-select v-model="queryParams.sourceChatPk" clearable filterable style="width: 220px" @change="handleQuery">
          <el-option v-for="chat in sourceOptions" :key="chat.chatPk" :label="chat.chatTitle" :value="chat.chatPk" />
        </el-select>
      </el-form-item>
      <el-form-item label="敏感"><el-select v-model="queryParams.isSensitive" clearable style="width: 120px" @change="handleQuery"><el-option v-for="item in yesNoOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item label="转发状态"><el-select v-model="queryParams.autoForwardStatus" clearable style="width: 140px" @change="handleQuery"><el-option v-for="item in forwardStatusOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows">
      <el-table-column label="ID" prop="messageId" width="80" />
      <el-table-column label="来源" prop="sourceChatTitle" width="180" show-overflow-tooltip />
      <el-table-column label="TG消息ID" prop="telegramMessageId" width="110" />
      <el-table-column label="文本" prop="messageText" show-overflow-tooltip />
      <el-table-column label="敏感" width="80"><template #default="scope">{{ optionLabel(yesNoOptions, scope.row.isSensitive) }}</template></el-table-column>
      <el-table-column label="命中词" prop="sensitiveWord" width="120" />
      <el-table-column label="转发状态" width="120"><template #default="scope">{{ optionLabel(forwardStatusOptions, scope.row.autoForwardStatus) }}</template></el-table-column>
      <el-table-column label="接收时间" prop="createTime" width="180"><template #default="scope">{{ parseTime(scope.row.createTime) }}</template></el-table-column>
      <el-table-column label="操作" width="120"><template #default="scope"><el-button link type="primary" icon="Position" @click="openForward(scope.row)">手动转发</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog title="手动转发" v-model="forwardOpen" width="520px" append-to-body>
      <el-form :model="forwardForm" label-width="120px">
        <el-form-item label="消息"><el-input :model-value="forwardForm.messageLabel" disabled /></el-form-item>
        <el-form-item label="目标频道">
          <el-select v-model="forwardForm.targetChatPks" multiple filterable collapse-tags style="width: 100%">
            <el-option v-for="chat in targetOptions" :key="chat.chatPk" :label="chat.chatTitle" :value="chat.chatPk" />
          </el-select>
        </el-form-item>
        <el-form-item label="转发方式">
          <el-radio-group v-model="forwardForm.forwardMode">
            <el-radio v-for="item in forwardModeOptions" :key="item.value" :label="item.value">{{ item.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForward">发送</el-button><el-button @click="forwardOpen = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { accountApi, chatApi, messageApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), forwardOpen = ref(false);
const accountOptions = ref([]);
const sourceOptions = ref([]);
const targetOptions = ref([]);
const yesNoOptions = [{ label: "是", value: "Y" }, { label: "否", value: "N" }];
const forwardStatusOptions = [{ label: "待转发", value: "pending" }, { label: "已阻止", value: "blocked" }, { label: "成功", value: "success" }, { label: "部分失败", value: "partial_failed" }];
const forwardModeOptions = [{ label: "原生隐藏转发", value: "native_hidden" }, { label: "清洗广告复制发送", value: "copy_clean" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 }, forwardForm: {} });
const { queryParams, forwardForm } = toRefs(data);
function getList() { loading.value = true; messageApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function getAccountOptions() { accountApi.list({ pageNum: 1, pageSize: 1000, status: "0" }).then((res) => { accountOptions.value = res.rows || []; }); }
function getChatOptions(accountId) { chatApi.list({ pageNum: 1, pageSize: 1000, accountId, status: "0" }).then((res) => { const chats = res.rows || []; sourceOptions.value = chats.filter((item) => item.canListen === "Y"); targetOptions.value = chats.filter((item) => item.canSend === "Y"); }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleAccountChange(accountId) { queryParams.value.sourceChatPk = undefined; getChatOptions(accountId); handleQuery(); }
function openForward(row) { forwardForm.value = { messageId: row.messageId, messageLabel: `${row.sourceChatTitle || ""} #${row.telegramMessageId || row.messageId}`, targetChatPks: [], forwardMode: "native_hidden" }; getChatOptions(row.accountId); forwardOpen.value = true; }
function submitForward() {
  const targetChatPksValue = forwardForm.value.targetChatPks || [];
  if (!targetChatPksValue.length) { proxy.$modal.msgError("目标频道不能为空"); return; }
  messageApi.manualForward({ messageId: forwardForm.value.messageId, targetChatPks: targetChatPksValue, forwardMode: forwardForm.value.forwardMode || "native_hidden" }).then((res) => { proxy.$modal.msgSuccess(res.msg); forwardOpen.value = false; getList(); });
}
getAccountOptions();
getChatOptions(undefined);
getList();
</script>
