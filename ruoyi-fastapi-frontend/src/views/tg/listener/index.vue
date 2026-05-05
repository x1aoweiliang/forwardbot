<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="监听账号">
        <el-select v-model="queryParams.accountId" clearable filterable style="width: 220px" @change="handleQuery">
          <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
        </el-select>
      </el-form-item>
      <el-form-item label="规则名称"><el-input v-model="queryParams.ruleName" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item label="状态">
        <el-select v-model="queryParams.status" clearable style="width: 100px" @change="handleQuery">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col><el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" /><el-table-column label="ID" prop="ruleId" width="80" /><el-table-column label="规则名称" prop="ruleName" /><el-table-column label="监听账号" width="140"><template #default="scope">{{ accountName(scope.row.accountId) }}</template></el-table-column><el-table-column label="来源" width="180"><template #default="scope">{{ chatName(scope.row.sourceChatPk) }}</template></el-table-column><el-table-column label="目标频道" show-overflow-tooltip><template #default="scope">{{ targetNames(scope.row.targetChatPks) }}</template></el-table-column><el-table-column label="状态" width="80"><template #default="scope">{{ optionLabel(statusOptions, scope.row.status) }}</template></el-table-column>
      <el-table-column label="操作" width="150"><template #default="scope"><el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button><el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog :title="title" v-model="open" width="640px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="130px">
        <el-form-item label="规则名称" prop="ruleName"><el-input v-model="form.ruleName" /></el-form-item>
        <el-form-item label="监听账号" prop="accountId">
          <el-select v-model="form.accountId" filterable style="width: 100%" @change="handleAccountChange">
            <el-option v-for="account in accountOptions" :key="account.accountId" :label="account.accountName" :value="account.accountId" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源" prop="sourceChatPk">
          <el-select v-model="form.sourceChatPk" filterable style="width: 100%">
            <el-option v-for="chat in sourceChatOptions" :key="chat.chatPk" :label="chat.chatTitle" :value="chat.chatPk" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标频道" prop="targetChatPkList">
          <el-select v-model="form.targetChatPkList" multiple filterable collapse-tags style="width: 100%">
            <el-option v-for="chat in targetChatOptions" :key="chat.chatPk" :label="chat.chatTitle" :value="chat.chatPk" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio label="0">启用</el-radio><el-radio label="1">停用</el-radio></el-radio-group></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForm">确定</el-button><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { accountApi, chatApi, listenerApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), ids = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), open = ref(false), multiple = ref(true), title = ref("");
const accountOptions = ref([]);
const allChatOptions = ref([]);
const sourceChatOptions = ref([]);
const targetChatOptions = ref([]);
const statusOptions = [{ label: "启用", value: "0" }, { label: "停用", value: "1" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 }, form: {}, rules: { ruleName: [{ required: true, message: "规则名称不能为空", trigger: "blur" }], accountId: [{ required: true, message: "监听账号不能为空", trigger: "change" }], sourceChatPk: [{ required: true, message: "来源不能为空", trigger: "change" }], targetChatPkList: [{ required: true, message: "目标频道不能为空", trigger: "change" }] } });
const { queryParams, form, rules } = toRefs(data);
function getList() { loading.value = true; listenerApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function getAccountOptions() { accountApi.list({ pageNum: 1, pageSize: 1000, status: "0" }).then((res) => { accountOptions.value = res.rows || []; }); }
function getAllChatOptions() { chatApi.list({ pageNum: 1, pageSize: 1000, status: "0" }).then((res) => { allChatOptions.value = res.rows || []; }); }
function getChatOptions(accountId) { if (!accountId) { sourceChatOptions.value = []; targetChatOptions.value = []; return; } chatApi.list({ pageNum: 1, pageSize: 1000, accountId, status: "0" }).then((res) => { const chats = res.rows || []; sourceChatOptions.value = chats.filter((item) => item.canListen === "Y"); targetChatOptions.value = chats.filter((item) => item.canSend === "Y"); }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function accountName(accountId) { return accountOptions.value.find((item) => item.accountId === accountId)?.accountName || accountId || "-"; }
function chatName(chatPk) { return allChatOptions.value.find((item) => item.chatPk === chatPk)?.chatTitle || chatPk || "-"; }
function targetNames(targetChatPks) { return String(targetChatPks || "").split(",").map((item) => chatName(Number(item))).filter(Boolean).join("，") || "-"; }
function reset() { form.value = { ruleId: undefined, ruleName: undefined, accountId: undefined, sourceChatPk: undefined, targetChatPks: undefined, targetChatPkList: [], status: "0" }; sourceChatOptions.value = []; targetChatOptions.value = []; proxy.resetForm("formRef"); }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleSelectionChange(selection) { ids.value = selection.map((item) => item.ruleId); multiple.value = !selection.length; }
function handleAdd() { reset(); title.value = "新增监听规则"; open.value = true; }
function handleUpdate(row) { reset(); form.value = { ...row, targetChatPkList: (row.targetChatPks || "").split(",").map((item) => Number(item)).filter(Boolean) }; getChatOptions(row.accountId); title.value = "修改监听规则"; open.value = true; }
function handleAccountChange(accountId) { form.value.sourceChatPk = undefined; form.value.targetChatPkList = []; getChatOptions(accountId); }
function submitForm() { form.value.targetChatPks = (form.value.targetChatPkList || []).join(","); proxy.$refs.formRef.validate((valid) => { if (!valid) return; (form.value.ruleId ? listenerApi.update : listenerApi.add)(form.value).then((res) => { proxy.$modal.msgSuccess(res.msg); open.value = false; getList(); }); }); }
function handleDelete(row) { const removeIds = row.ruleId || ids.value; proxy.$modal.confirm(`是否确认删除规则编号为"${removeIds}"的数据项？`).then(() => listenerApi.remove(removeIds)).then(() => { getList(); proxy.$modal.msgSuccess("删除成功"); }); }
getAccountOptions();
getAllChatOptions();
getList();
</script>
