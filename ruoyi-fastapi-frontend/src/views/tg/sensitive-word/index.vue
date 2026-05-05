<template>
  <div class="app-container">
    <el-form :model="queryParams" ref="queryRef" :inline="true" v-show="showSearch">
      <el-form-item label="敏感词"><el-input v-model="queryParams.word" clearable @keyup.enter="handleQuery" /></el-form-item>
      <el-form-item label="区分大小写"><el-select v-model="queryParams.matchCase" clearable style="width: 120px" @change="handleQuery"><el-option v-for="item in yesNoOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item label="状态"><el-select v-model="queryParams.status" clearable style="width: 100px" @change="handleQuery"><el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></el-form-item>
      <el-form-item><el-button type="primary" icon="Search" @click="handleQuery">搜索</el-button><el-button icon="Refresh" @click="resetQuery">重置</el-button></el-form-item>
    </el-form>
    <el-row :gutter="10" class="mb8"><el-col :span="1.5"><el-button type="primary" plain icon="Plus" @click="handleAdd">新增</el-button></el-col><el-col :span="1.5"><el-button type="danger" plain icon="Delete" :disabled="multiple" @click="handleDelete">删除</el-button></el-col><right-toolbar v-model:showSearch="showSearch" @queryTable="getList" /></el-row>
    <el-table v-loading="loading" :data="rows" @selection-change="handleSelectionChange">
      <el-table-column type="selection" width="55" /><el-table-column label="ID" prop="wordId" width="80" /><el-table-column label="敏感词" prop="word" /><el-table-column label="区分大小写" width="120"><template #default="scope">{{ optionLabel(yesNoOptions, scope.row.matchCase) }}</template></el-table-column><el-table-column label="状态" width="100"><template #default="scope">{{ optionLabel(statusOptions, scope.row.status) }}</template></el-table-column>
      <el-table-column label="操作" width="150"><template #default="scope"><el-button link type="primary" icon="Edit" @click="handleUpdate(scope.row)">修改</el-button><el-button link type="primary" icon="Delete" @click="handleDelete(scope.row)">删除</el-button></template></el-table-column>
    </el-table>
    <pagination v-show="total > 0" :total="total" v-model:page="queryParams.pageNum" v-model:limit="queryParams.pageSize" @pagination="getList" />
    <el-dialog :title="title" v-model="open" width="520px" append-to-body>
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="敏感词" prop="word"><el-input v-model="form.word" /></el-form-item>
        <el-form-item label="区分大小写"><el-radio-group v-model="form.matchCase"><el-radio label="Y">是</el-radio><el-radio label="N">否</el-radio></el-radio-group></el-form-item>
        <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio label="0">启用</el-radio><el-radio label="1">停用</el-radio></el-radio-group></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" /></el-form-item>
      </el-form>
      <template #footer><el-button type="primary" @click="submitForm">确定</el-button><el-button @click="open = false">取消</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { sensitiveWordApi } from "@/api/tg";
const { proxy } = getCurrentInstance();
const rows = ref([]), ids = ref([]), loading = ref(false), showSearch = ref(true), total = ref(0), open = ref(false), multiple = ref(true), title = ref("");
const yesNoOptions = [{ label: "是", value: "Y" }, { label: "否", value: "N" }];
const statusOptions = [{ label: "启用", value: "0" }, { label: "停用", value: "1" }];
const data = reactive({ queryParams: { pageNum: 1, pageSize: 10 }, form: {}, rules: { word: [{ required: true, message: "敏感词不能为空", trigger: "blur" }] } });
const { queryParams, form, rules } = toRefs(data);
function getList() { loading.value = true; sensitiveWordApi.list(queryParams.value).then((res) => { rows.value = res.rows; total.value = res.total; loading.value = false; }); }
function optionLabel(options, value) { return options.find((item) => item.value === value)?.label || value || "-"; }
function reset() { form.value = { wordId: undefined, word: undefined, matchCase: "N", status: "0" }; proxy.resetForm("formRef"); }
function handleQuery() { queryParams.value.pageNum = 1; getList(); }
function resetQuery() { proxy.resetForm("queryRef"); handleQuery(); }
function handleSelectionChange(selection) { ids.value = selection.map((item) => item.wordId); multiple.value = !selection.length; }
function handleAdd() { reset(); title.value = "新增敏感词"; open.value = true; }
function handleUpdate(row) { reset(); form.value = { ...row }; title.value = "修改敏感词"; open.value = true; }
function submitForm() { proxy.$refs.formRef.validate((valid) => { if (!valid) return; (form.value.wordId ? sensitiveWordApi.update : sensitiveWordApi.add)(form.value).then((res) => { proxy.$modal.msgSuccess(res.msg); open.value = false; getList(); }); }); }
function handleDelete(row) { const removeIds = row.wordId || ids.value; proxy.$modal.confirm(`是否确认删除敏感词编号为"${removeIds}"的数据项？`).then(() => sensitiveWordApi.remove(removeIds)).then(() => { getList(); proxy.$modal.msgSuccess("删除成功"); }); }
getList();
</script>
