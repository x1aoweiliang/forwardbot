import request from "@/utils/request";

function crudApi(base) {
  return {
    list(query) {
      return request({ url: `${base}/list`, method: "get", params: query });
    },
    add(data) {
      return request({ url: base, method: "post", data });
    },
    update(data) {
      return request({ url: base, method: "put", data });
    },
    remove(ids) {
      return request({ url: `${base}/${ids}`, method: "delete" });
    },
    detail(id) {
      return request({ url: `${base}/${id}`, method: "get" });
    },
  };
}

export const accountApi = {
  ...crudApi("/telegram/account"),
  sendCode(data) {
    return request({ url: "/telegram/account/send-code", method: "post", data });
  },
  confirmLogin(data) {
    return request({ url: "/telegram/account/confirm-login", method: "post", data });
  },
  start(accountId) {
    return request({ url: `/telegram/account/${accountId}/start`, method: "post" });
  },
  stop(accountId) {
    return request({ url: `/telegram/account/${accountId}/stop`, method: "post" });
  },
};

export const chatApi = {
  ...crudApi("/telegram/chat"),
  sync(accountId) {
    return request({ url: `/telegram/chat/sync/${accountId}`, method: "post" });
  },
  sendMessage(data) {
    return request({ url: "/telegram/chat/send-message", method: "post", data });
  },
};
export const listenerApi = crudApi("/telegram/listener");
export const sensitiveWordApi = crudApi("/telegram/sensitive-word");
export const cleanRuleApi = crudApi("/telegram/clean-rule");

export const adTextApi = {
  ...crudApi("/telegram/ad-text"),
  enable(adId) {
    return request({ url: `/telegram/ad-text/${adId}/enable`, method: "post" });
  },
};

export const messageApi = {
  list(query) {
    return request({ url: "/telegram/message/list", method: "get", params: query });
  },
  manualForward(data) {
    return request({ url: "/telegram/message/manual-forward", method: "post", data });
  },
};

export const forwardRecordApi = {
  list(query) {
    return request({ url: "/telegram/forward-record/list", method: "get", params: query });
  },
};

export const archiveApi = {
  library(params) { return request({ url: '/telegram/archive/library', params }); },
  detail(id) { return request({ url: `/telegram/archive/message/${id}` }); },
  rename(id, data) { return request({ url: `/telegram/archive/message/${id}/name`, method: 'put', data }); },
  retryDownload(id) { return request({ url: `/telegram/archive/message/${id}/retry`, method: 'post' }); },
  cleanup(data) { return request({ url: '/telegram/archive/local/cleanup', method: 'post', data, timeout: 180000 }); },
  quarkStatus(id) { return request({ url: `/telegram/archive/quark/${id}/status`, timeout: 180000 }); },
  quarkLogin(id) { return request({ url: `/telegram/archive/quark/${id}/login`, method: 'post', timeout: 180000 }); },
  quarkPoll(id, session) { return request({ url: `/telegram/archive/quark/${id}/login/${session}`, timeout: 180000 }); },
  quarkCancel(id, session) { return request({ url: `/telegram/archive/quark/${id}/login/${session}`, method: 'delete' }); },
  storages() { return request({ url: '/telegram/archive/storages' }); },
  folders(storageId, path) {
    return request({ url: '/telegram/archive/folders', params: { storage_id: storageId, path }, timeout: 180000 });
  },
  createFolder(data) { return request({ url: '/telegram/archive/folders', method: 'post', data, timeout: 180000 }); },
  submit(data) { return request({ url: '/telegram/archive/tasks', method: 'post', data }); },
  retryArchive(id) { return request({ url: `/telegram/archive/tasks/${id}/retry`, method: 'post' }); },
  ticket(id) { return request({ url: `/telegram/archive/media/${id}/ticket` }); },
  contentUrl(id, ticket) {
    return `${import.meta.env.VITE_APP_BASE_API}/telegram/archive/media/${id}/content?ticket=${encodeURIComponent(ticket)}`;
  },
};
