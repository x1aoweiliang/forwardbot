<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="账号">
        <el-select v-model="queryParams.accountId" clearable filterable style="width: 220px" @change="handleQuery">
          <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
        </el-select>
      </el-form-item>
      <el-form-item label="类型">
        <el-select v-model="queryParams.chatType" clearable style="width: 120px" @change="handleQuery">
          <el-option v-for="item in chatTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="监听">
        <el-select v-model="queryParams.canListen" clearable style="width: 100px" @change="handleQuery">
          <el-option v-for="item in yesNoOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="发送">
        <el-select v-model="queryParams.canSend" clearable style="width: 100px" @change="handleQuery">
          <el-option v-for="item in yesNoOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="queryParams.status" clearable style="width: 100px" @change="handleQuery">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="标题"><el-input v-model="queryParams.chatTitle" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col>
      <el-col :span="1.5"><el-button type="warning" plain icon="Refresh" :disabled="!queryParams.accountId" @click="syncDialogs">同步对话框</el-button></el-col>
      <el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>
    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" />
      <el-table-column label="ID" prop="chatPk" width="80" />
      <el-table-column label="账号" width="140"><template #default="scope">{{ accountName(scope.row.accountId) }}</template></el-table-column>
      <el-table-column label="Chat ID" prop="chatId" />
      <el-table-column label="标题" prop="chatTitle" />
      <el-table-column label="用户名" prop="username" />
      <el-table-column label="类型" width="100"><template #default="scope">{{ optionLabel(chatTypeOptions, scope.row.chatType) }}</template></el-table-column>
      <el-table-column label="监听" width="80"><template #default="scope">{{ optionLabel(yesNoOptions, scope.row.canListen) }}</template></el-table-column>
      <el-table-column label="发送" width="80"><template #default="scope">{{ optionLabel(yesNoOptions, scope.row.canSend) }}</template></el-table-column>
      <el-table-column label="广告词" width="160"><template #default="scope">{{ adTextName(scope.row.adTextId) }}</template></el-table-column>
      <el-table-column label="状态" width="80"><template #default="scope">{{ optionLabel(statusOptions, scope.row.status) }}</template></el-table-column>
      <el-table-column label="操作" width="210"><template #default="scope"><el-button link type="primary" icon="Message" :disabled="scope.row.canSend !== 'Y'" @click="openSend(scope.row)">发送</el-button><el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button><el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog :title="title" v-model="open" width="640px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="账号" prop="accountId">
          <el-select v-model="form.accountId" filterable style="width: 100%">
            <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
          </el-select>
        </el-form-item>
        <el-form-item label="Chat ID" prop="chatId"><el-input v-model="form.chatId" /></el-form-item>
        <el-form-item label="标题" prop="chatTitle"><el-input v-model="form.chatTitle" /></el-form-item>
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="类型"><el-radio-group v-model="form.chatType"><el-radio v-for="item in chatTypeOptions" :key="item.value" :label="item.value">{{ item.label }}</el-radio></el-radio-group></el-form-item>
        <el-form-item label="权限"><el-checkbox v-model="listenChecked">可监听</el-checkbox><el-checkbox v-model="sendChecked">可发送</el-checkbox></el-form-item>
        <el-form-item label="广告词">
          <el-select v-model="form.adTextId" clearable filterable style="width: 100%">
            <el-option v-for="item in adTextOptions" :key="item.adId" :label="item.adName" :value="item.adId" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio label="0">启用</el-radio><el-radio label="1">停用</el-radio></el-radio-group></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForm">确定</el-button><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
    <el-dialog title="发送消息" v-model="sendOpen" width="560px" append-to-body>
      <el-form ref="sendFormRef" :model="sendForm" :rules="sendRules" label-width="90px">
        <el-form-item label="对话框"><el-input v-model="sendForm.chatTitle" disabled /></el-form-item>
        <el-form-item label="内容" prop="text"><el-input v-model="sendForm.text" type="textarea" :rows="5" /></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitSend">发送</el-button><el-button @click="sendOpen = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { accountApi, adTextApi, chatApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), ids = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), open = ref(false), sendOpen = ref(false), multiple = ref(true);
const title = ref("");
const listenChecked = ref(false), sendChecked = ref(false);
const accountOptions = ref([]);
const adTextOptions = ref([]);
const chatTypeOptions = [{ label: "频道", value: "channel" }, { label: "群组", value: "group" }, { label: "私聊", value: "private" }];
const yesNoOptions = [{ label: "是", value: "Y" }, { label: "否", value: "N" }];
const statusOptions = [{ label: "启用", value: "0" }, { label: "停用", value: "1" }];
const data = reactive({
  queryParams: { pageNum: 1, pageSize: 10 },
  form: {},
  sendForm: {},
  rules: { accountId: [{ required: true, message: "账号不能为空", trigger: "change" }], chatId: [{ required: true, message: "Chat ID不能为空", trigger: "blur" }], chatTitle: [{ required: true, message: "标题不能为空", trigger: "blur" }] },
  sendRules: { text: [{ required: true, message: "发送内容不能为空", trigger: "blur" }] },
});
const { queryParams, form, sendForm, rules, sendRules } = toRefs(data);
function getList() { loading.value = true; chatApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function getAccountOptions() { accountApi.list({ pageNum: 1, pageSize: 1000, status: "0" }).then((res) => { accountOptions.value = res.rows || []; }); }
function getAdTextOptions() { adTextApi.list({ pageNum: 1, pageSize: 1000 }).then((res) => { adTextOptions.value = res.rows || []; }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function accountName(accountId) { return accountOptions.value.find((item) => item.accountId === accountId)?.accountName || accountId || "-"; }
function adTextName(adTextId) { return adTextOptions.value.find((item) => item.adId === adTextId)?.adName || "-"; }
function reset() { form.value = { chatPk: undefined, accountId: undefined, chatId: undefined, chatTitle: undefined, username: undefined, chatType: "channel", canListen: "N", canSend: "N", adTextId: undefined, status: "0" }; listenChecked.value = false; sendChecked.value = false; proxy.resetForm("formRef"); }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleSelectionChange(selection) { ids.value = selection.map((item) => item.chatPk); multiple.value = !selection.length; }
function handleAdd() { reset(); title.value = "新增频道/群组"; open.value = true; }
function handleUpdate(row) { reset(); form.value = { ...row }; listenChecked.value = row.canListen === "Y"; sendChecked.value = row.canSend === "Y"; title.value = "修改频道/群组"; open.value = true; }
function submitForm() { form.value.canListen = listenChecked.value ? "Y" : "N"; form.value.canSend = sendChecked.value ? "Y" : "N"; proxy.$refs.formRef.validate((valid) => { if (!valid) return; (form.value.chatPk ? chatApi.update : chatApi.add)(form.value).then((res) => { proxy.$modal.msgSuccess(res.msg); open.value = false; getList(); }); }); }
function handleDelete(row) { const removeIds = row.chatPk || ids.value; proxy.$modal.confirm(`是否确认删除频道编号为"${removeIds}"的数据项？`).then(() => chatApi.remove(removeIds)).then(() => { getList(); proxy.$modal.msgSuccess("删除成功"); }); }
function syncDialogs() { chatApi.sync(queryParams.value.accountId).then((res) => { proxy.$modal.msgSuccess(res.msg); getList(); }); }
function openSend(row) { sendForm.value = { chatPk: row.chatPk, chatTitle: row.chatTitle, text: "" }; sendOpen.value = true; }
function submitSend() { proxy.$refs.sendFormRef.validate((valid) => { if (!valid) return; chatApi.sendMessage({ chatPk: sendForm.value.chatPk, text: sendForm.value.text }).then((res) => { proxy.$modal.msgSuccess(res.msg); sendOpen.value = false; }); }); }
getAccountOptions();
getAdTextOptions();
getList();
</script>
