<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="名称"><el-input v-model="queryParams.cleanName" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item label="匹配文本"><el-input v-model="queryParams.matchText" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item label="状态"><el-select v-model="queryParams.status" clearable style="width: 100px" @change="handleQuery"><el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col><el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" /><el-table-column label="ID" prop="cleanId" width="80" /><el-table-column label="名称" prop="cleanName" width="180" /><el-table-column label="匹配文本" prop="matchText" show-overflow-tooltip /><el-table-column label="替换文本" prop="replacement" show-overflow-tooltip /><el-table-column label="区分大小写" width="110"><template #default="scope">{{ optionLabel(yesNoOptions, scope.row.matchCase) }}</template></el-table-column><el-table-column label="状态" width="80"><template #default="scope">{{ optionLabel(statusOptions, scope.row.status) }}</template></el-table-column>
      <el-table-column label="操作" width="150"><template #default="scope"><el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button><el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog :title="title" v-model="open" width="680px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="cleanName"><el-input v-model="form.cleanName" /></el-form-item>
        <el-form-item label="匹配文本" prop="matchText"><el-input v-model="form.matchText" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="替换文本"><el-input v-model="form.replacement" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="区分大小写"><el-radio-group v-model="form.matchCase"><el-radio label="Y">是</el-radio><el-radio label="N">否</el-radio></el-radio-group></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio label="0">启用</el-radio><el-radio label="1">停用</el-radio></el-radio-group></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForm">确定</el-button><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { cleanRuleApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), ids = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), open = ref(false), multiple = ref(true), title = ref("");
const statusOptions = [{ label: "启用", value: "0" }, { label: "停用", value: "1" }];
const yesNoOptions = [{ label: "是", value: "Y" }, { label: "否", value: "N" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 }, form: {}, rules: { cleanName: [{ required: true, message: "名称不能为空", trigger: "blur" }], matchText: [{ required: true, message: "匹配文本不能为空", trigger: "blur" }] } });
const { queryParams, form, rules } = toRefs(data);
function getList() { loading.value = true; cleanRuleApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function reset() { form.value = { cleanId: undefined, cleanName: undefined, matchText: undefined, replacement: "", matchCase: "Y", status: "0" }; proxy.resetForm("formRef"); }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleSelectionChange(selection) { ids.value = selection.map((item) => item.cleanId); multiple.value = !selection.length; }
function handleAdd() { reset(); title.value = "新增内容清理规则"; open.value = true; }
function handleUpdate(row) { reset(); form.value = { ...row }; title.value = "修改内容清理规则"; open.value = true; }
function submitForm() { proxy.$refs.formRef.validate((valid) => { if (!valid) return; (form.value.cleanId ? cleanRuleApi.update : cleanRuleApi.add)(form.value).then((res) => { proxy.$modal.msgSuccess(res.msg); open.value = false; getList(); }); }); }
function handleDelete(row) { const removeIds = row.cleanId || ids.value; proxy.$modal.confirm(`是否确认删除清理规则编号为"${removeIds}"的数据项？`).then(() => cleanRuleApi.remove(removeIds)).then(() => { getList(); proxy.$modal.msgSuccess("删除成功"); }); }
getList();
</script>
