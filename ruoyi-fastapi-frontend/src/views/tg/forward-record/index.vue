<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="账号">
        <el-select v-model="queryParams.accountId" clearable filterable style="width: 220px" @change="handleAccountChange">
          <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
        </el-select>
      </el-form-item>
      <el-form-item label="消息">
        <el-select v-model="queryParams.messageId" clearable filterable style="width: 220px" @change="handleQuery">
          <el-option v-for="message in messageOptions" :key="message.messageId" :label="messageLabel(message)" :value="message.messageId" />
        </el-select>
      </el-form-item>
      <el-form-item label="目标">
        <el-select v-model="queryParams.targetChatPk" clearable filterable style="width: 220px" @change="handleQuery">
          <el-option v-for="chat in targetOptions" :key="chat.chatPk" :label="chat.chatTitle" :value="chat.chatPk" />
        </el-select>
      </el-form-item>
      <el-form-item label="类型"><el-select v-model="queryParams.forwardType" clearable style="width: 120px" @change="handleQuery"><el-option v-for="item in forwardTypeOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item label="状态"><el-select v-model="queryParams.status" clearable style="width: 120px" @change="handleQuery"><el-option v-for="item in sendStatusOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows">
      <el-table-column label="ID" prop="recordId" width="80" />
      <el-table-column label="消息" width="140"><template #default="scope">{{ recordMessageLabel(scope.row.messageId) }}</template></el-table-column>
      <el-table-column label="目标渠道" min-width="180" show-overflow-tooltip><template #default="scope">{{ targetChatName(scope.row) }}</template></el-table-column>
      <el-table-column label="类型" width="90"><template #default="scope">{{ optionLabel(forwardTypeOptions, scope.row.forwardType) }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="scope">{{ optionLabel(sendStatusOptions, scope.row.status) }}</template></el-table-column>
      <el-table-column label="创建时间" prop="createTime" width="180"><template #default="scope">{{ formatCreateTime(scope.row.createTime) }}</template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
  </div>
</template>
<script setup>
import { accountApi, chatApi, forwardRecordApi, messageApi } from "@/api/tg";
import { parseTime } from "@/utils/ruoyi";
const { proxy } = getCurrentInstance();
const rows = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0);
const accountOptions = ref([]);
const messageOptions = ref([]);
const targetOptions = ref([]);
const forwardTypeOptions = [{ label: "自动", value: "auto" }, { label: "手动", value: "manual" }, { label: "主动发送", value: "dialog" }];
const sendStatusOptions = [{ label: "成功", value: "success" }, { label: "失败", value: "failed" }, { label: "部分失败", value: "partial_failed" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 } });
const { queryParams } = toRefs(data);
function getList() { loading.value = true; forwardRecordApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function getAccountOptions() { accountApi.list({ pageNum: 1, pageSize: 1000, status: "0" }).then((res) => { accountOptions.value = res.rows || []; }); }
function getMessageOptions(accountId) { messageApi.list({ pageNum: 1, pageSize: 1000, accountId }).then((res) => { messageOptions.value = res.rows || []; }); }
function getTargetOptions(accountId) { chatApi.list({ pageNum: 1, pageSize: 1000, accountId, canSend: "Y", status: "0" }).then((res) => { targetOptions.value = res.rows || []; }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function messageLabel(message) { return `${message.sourceChatTitle || "消息"} #${message.telegramMessageId || message.messageId}`; }
function recordMessageLabel(messageId) { if (!messageId) return "-"; return messageLabel(messageOptions.value.find((item) => item.messageId === messageId) || { messageId }); }
function targetChatName(row) { return row.targetChatTitle || targetOptions.value.find((item) => item.chatPk === row.targetChatPk)?.chatTitle || "-"; }
function formatCreateTime(time) { const value = parseTime(time); return value && !value.startsWith("0-0-0") ? value : "-"; }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleAccountChange(accountId) { queryParams.value.messageId = undefined; queryParams.value.targetChatPk = undefined; getMessageOptions(accountId); getTargetOptions(accountId); handleQuery(); }
getAccountOptions();
getMessageOptions(undefined);
getTargetOptions(undefined);
getList();
</script>
