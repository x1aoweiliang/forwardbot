<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="账号名称" prop="accountName">
        <el-input v-model="queryParams.accountName" clearable placeholder="请输入账号名称" @keyup.enter="handleQuery" />
      </el-form-item>
      <el-form-item label="手机号" prop="phone">
        <el-input v-model="queryParams.phone" clearable placeholder="请输入手机号" @keyup.enter="handleQuery" />
      </el-form-item>
      <el-form-item label="状态" prop="status">
        <el-select v-model="queryParams.status" clearable style="width: 100px" @change="handleQuery">
          <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button>
        <el-button icon="Refresh" @click="resetQuery">重置</el-button>
      </el-form-item>
    </el-form>

    <el-row :gutter="10" class="mb8">
      <el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col>
      <el-col :span="1.5"><el-button type="success" plain icon="Edit" :disabled="single" @click="handleUpdate">修改</el-button></el-col>
      <el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col>
      <right-toolbar v-model:showSearch="showSearch" @queryTable="getList" />
    </el-row>

    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" align="center" />
      <el-table-column label="ID" prop="accountId" width="80" />
      <el-table-column label="账号名称" prop="accountName" />
      <el-table-column label="手机号" prop="phone" />
      <el-table-column label="API ID" prop="apiId" width="120" />
      <el-table-column label="登录状态" width="130"><template #default="scope">{{ optionLabel(sessionStatusOptions, scope.row.sessionStatus) }}</template></el-table-column>
      <el-table-column label="启停" prop="status" width="90">
        <template #default="scope">
          <el-tag :type="scope.row.status === '0' ? 'success' : 'info'">{{ scope.row.status === "0" ? "启用" : "停用" }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360" align="center">
        <template #default="scope">
          <el-button link type="primary" icon="Message" @click="sendCode(scope.row)">验证码</el-button>
          <el-button link type="primary" icon="Key" @click="openLogin(scope.row)">登录</el-button>
          <el-button link type="primary" icon="Refresh" @click="syncDialogs(scope.row)">同步对话框</el-button>
          <el-button link type="primary" icon="VideoPlay" @click="start(scope.row)">启动</el-button>
          <el-button link type="primary" icon="VideoPause" @click="stop(scope.row)">停止</el-button>
          <el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button>
          <el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />

    <el-dialog :title="title" v-model="open" width="640px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="账号名称" prop="accountName"><el-input v-model="form.accountName" /></el-form-item>
        <el-form-item label="手机号" prop="phone"><el-input v-model="form.phone" /></el-form-item>
        <el-form-item label="API ID" prop="apiId"><el-input-number v-model="form.apiId" :min="1" style="width: 100%" /></el-form-item>
        <el-form-item label="API Hash" prop="apiHash"><el-input v-model="form.apiHash" type="password" show-password /></el-form-item>
        <el-form-item label="状态" prop="status">
          <el-radio-group v-model="form.status"><el-radio label="0">启用</el-radio><el-radio label="1">停用</el-radio></el-radio-group>
        </el-form-item>
        <el-form-item label="备注" prop="remark"><el-input v-model="form.remark" type="textarea" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="submitForm">确定</el-button>
        <el-button @click="open = false">取消</el-button>
      </template>
    </el-dialog>

    <el-dialog title="确认登录" v-model="loginOpen" width="520px" append-to-body>
      <el-form :model="loginForm" label-width="110px">
        <el-form-item label="验证码"><el-input v-model="loginForm.code" /></el-form-item>
        <el-form-item label="二次密码"><el-input v-model="loginForm.password" type="password" show-password /></el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="confirmLogin">提交</el-button>
        <el-button @click="loginOpen = false">取消</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { accountApi, chatApi } from "@/api/tg";

const { proxy } = getCurrentInstance();
const rows = ref([]);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const loading = ref(false);
const showSearch = ref(true);
const total = ref(0);
const open = ref(false);
const loginOpen = ref(false);
const title = ref("");
const statusOptions = [{ label: "启用", value: "0" }, { label: "停用", value: "1" }];
const sessionStatusOptions = [{ label: "未登录", value: "logged_out" }, { label: "已发送验证码", value: "code_sent" }, { label: "需要二次密码", value: "password_required" }, { label: "已登录", value: "authorized" }];
const loginSessionStatus = ref("");

const data = reactive({
  queryParams: { pageNum: 1, pageSize: 10, accountName: undefined, phone: undefined },
  form: {},
  loginForm: {},
  rules: {
    accountName: [{ required: true, message: "账号名称不能为空", trigger: "blur" }],
    phone: [{ required: true, message: "手机号不能为空", trigger: "blur" }],
    apiId: [{ required: true, message: "API ID不能为空", trigger: "blur" }],
    apiHash: [{ required: true, message: "API Hash不能为空", trigger: "blur" }],
  },
});
const { queryParams, form, loginForm, rules } = toRefs(data);
function optionLabel(options, value) {
  return options.find((item) => item.value === value)?.label || value || "-";
}

function getList() {
  loading.value = true;
  accountApi.list(queryParams.value).then((res) => {
    rows.value = res.rows;
    total.value = res.total;
    loading.value = false;
  });
}
function reset() {
  form.value = { accountId: undefined, accountName: undefined, phone: undefined, apiId: undefined, apiHash: undefined, status: "0" };
  proxy.resetForm("formRef");
}
function handleQuery() {
  queryParams.value.pageNum = 1;
  getList();
}
function resetQuery() {
  proxy.resetForm("queryRef");
  handleQuery();
}
function handleSelectionChange(selection) {
  ids.value = selection.map((item) => item.accountId);
  single.value = selection.length !== 1;
  multiple.value = !selection.length;
}
function handleAdd() {
  reset();
  open.value = true;
  title.value = "新增Telegram账号";
}
function handleUpdate(row) {
  reset();
  form.value = { ...row, apiHash: row.apiHash || "********" };
  open.value = true;
  title.value = "修改Telegram账号";
}
function submitForm() {
  proxy.$refs["formRef"].validate((valid) => {
    if (!valid) return;
    const api = form.value.accountId ? accountApi.update : accountApi.add;
    api(form.value).then((res) => {
      proxy.$modal.msgSuccess(res.msg);
      open.value = false;
      getList();
    });
  });
}
function handleDelete(row) {
  const removeIds = row.accountId || ids.value;
  proxy.$modal.confirm(`是否确认删除账号编号为"${removeIds}"的数据项？`).then(() => accountApi.remove(removeIds)).then(() => {
    getList();
    proxy.$modal.msgSuccess("删除成功");
  });
}
function sendCode(row) {
  return accountApi.sendCode({ accountId: row.accountId }).then((res) => {
    proxy.$modal.msgSuccess(res.msg);
    getList();
  });
}
function openLogin(row) {
  loginForm.value = { accountId: row.accountId, code: undefined, password: undefined };
  loginSessionStatus.value = row.sessionStatus;
  if (["code_sent", "password_required"].includes(row.sessionStatus)) {
    loginOpen.value = true;
    return;
  }
  sendCode(row).then(() => {
    loginOpen.value = true;
  });
}
function confirmLogin() {
  const submitData = { ...loginForm.value };
  if (loginSessionStatus.value === "password_required") {
    submitData.code = undefined;
  }
  accountApi.confirmLogin(submitData).then((res) => {
    proxy.$modal.msgSuccess(res.msg);
    if (res.data?.sessionStatus === "password_required") {
      loginSessionStatus.value = "password_required";
      loginForm.value.code = undefined;
      return;
    }
    loginOpen.value = false;
    getList();
    if (res.data?.sessionStatus === "authorized") {
      chatApi.sync(loginForm.value.accountId).then(() => proxy.$modal.msgSuccess("对话框已同步"));
    }
  });
}
function syncDialogs(row) {
  chatApi.sync(row.accountId).then((res) => proxy.$modal.msgSuccess(res.msg));
}
function start(row) {
  accountApi.start(row.accountId).then((res) => proxy.$modal.msgSuccess(res.msg));
}
function stop(row) {
  accountApi.stop(row.accountId).then((res) => proxy.$modal.msgSuccess(res.msg));
}

getList();
</script>
