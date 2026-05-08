<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="名称"><el-input v-model="queryParams.adName" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item label="默认"><el-select v-model="queryParams.enabled" clearable style="width: 100px" @change="handleQuery"><el-option v-for="item in enabledOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col><el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" /><el-table-column label="ID" prop="adId" width="80" /><el-table-column label="名称" prop="adName" width="180" /><el-table-column label="内容" prop="adContent" show-overflow-tooltip /><el-table-column label="默认" width="90"><template #default="scope">{{ optionLabel(enabledOptions, scope.row.enabled) }}</template></el-table-column>
      <el-table-column label="操作" width="230"><template #default="scope"><el-button link type="primary" icon="Check" @click="enable(scope.row)">设为默认</el-button><el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button><el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog :title="title" v-model="open" width="620px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="adName"><el-input v-model="form.adName" /></el-form-item>
        <el-form-item label="内容" prop="adContent"><el-input v-model="form.adContent" type="textarea" :rows="5" /></el-form-item>
        <el-form-item label="默认"><el-radio-group v-model="form.enabled"><el-radio label="1">默认</el-radio><el-radio label="0">普通</el-radio></el-radio-group></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForm">确定</el-button><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { adTextApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), ids = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), open = ref(false), multiple = ref(true), title = ref("");
const enabledOptions = [{ label: "默认", value: "1" }, { label: "普通", value: "0" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 }, form: {}, rules: { adName: [{ required: true, message: "名称不能为空", trigger: "blur" }], adContent: [{ required: true, message: "内容不能为空", trigger: "blur" }] } });
const { queryParams, form, rules } = toRefs(data);
function getList() { loading.value = true; adTextApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function reset() { form.value = { adId: undefined, adName: undefined, adContent: undefined, enabled: "0" }; proxy.resetForm("formRef"); }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleSelectionChange(selection) { ids.value = selection.map((item) => item.adId); multiple.value = !selection.length; }
function handleAdd() { reset(); title.value = "新增广告词"; open.value = true; }
function handleUpdate(row) { reset(); form.value = { ...row }; title.value = "修改广告词"; open.value = true; }
function submitForm() { proxy.$refs.formRef.validate((valid) => { if (!valid) return; (form.value.adId ? adTextApi.update : adTextApi.add)(form.value).then((res) => { proxy.$modal.msgSuccess(res.msg); open.value = false; getList(); }); }); }
function enable(row) { adTextApi.enable(row.adId).then((res) => { proxy.$modal.msgSuccess(res.msg); getList(); }); }
function handleDelete(row) { const removeIds = row.adId || ids.value; proxy.$modal.confirm(`是否确认删除广告词编号为"${removeIds}"的数据项？`).then(() => adTextApi.remove(removeIds)).then(() => { getList(); proxy.$modal.msgSuccess("删除成功"); }); }
getList();
</script>
