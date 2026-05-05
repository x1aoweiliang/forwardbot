-- Telegram ForwardBot menu seed for RuoYi-FastAPI.
-- Apply this after the base RuoYi sys_menu table has been initialized.

insert into sys_menu values(
    1900, 'TG管理', 0, 5, 'tg', null, '', '', 1, 0,
    'M', '0', '0', '', 'message', 'admin', current_timestamp, '', null, 'TG管理目录'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1901, '账号管理', 1900, 1, 'account', 'tg/account/index', '', '', 1, 0,
    'C', '0', '0', 'tg:account:list', 'user', 'admin', current_timestamp, '', null, 'TG账号管理'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1902, '频道管理', 1900, 2, 'chat', 'tg/chat/index', '', '', 1, 0,
    'C', '0', '0', 'tg:chat:list', 'tree', 'admin', current_timestamp, '', null, 'TG频道管理'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1903, '监听规则', 1900, 3, 'listener', 'tg/listener/index', '', '', 1, 0,
    'C', '0', '0', 'tg:listener:list', 'monitor', 'admin', current_timestamp, '', null, 'TG监听规则'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1904, '敏感词', 1900, 4, 'sensitive-word', 'tg/sensitive-word/index', '', '', 1, 0,
    'C', '0', '0', 'tg:sensitive-word:list', 'eye-open', 'admin', current_timestamp, '', null, 'TG敏感词'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1905, '广告词', 1900, 5, 'ad-text', 'tg/ad-text/index', '', '', 1, 0,
    'C', '0', '0', 'tg:ad-text:list', 'edit', 'admin', current_timestamp, '', null, 'TG广告词'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1906, '内容清理', 1900, 6, 'clean-rule', 'tg/clean-rule/index', '', '', 1, 0,
    'C', '0', '0', 'tg:clean-rule:list', 'textarea', 'admin', current_timestamp, '', null, 'TG内容清理'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1907, '消息中心', 1900, 7, 'message', 'tg/message/index', '', '', 1, 0,
    'C', '0', '0', 'tg:message:list', 'message', 'admin', current_timestamp, '', null, 'TG消息中心'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1908, '发送记录', 1900, 8, 'forward-record', 'tg/forward-record/index', '', '', 1, 0,
    'C', '0', '0', 'tg:forward-record:list', 'log', 'admin', current_timestamp, '', null, 'TG发送记录'
) on conflict (menu_id) do nothing;

update sys_menu m
set
    menu_name = v.menu_name,
    parent_id = v.parent_id,
    order_num = v.order_num,
    path = v.path,
    component = v.component,
    query = v.query,
    route_name = v.route_name,
    is_frame = v.is_frame,
    is_cache = v.is_cache,
    menu_type = v.menu_type,
    visible = v.visible,
    status = v.status,
    perms = v.perms,
    icon = v.icon,
    update_by = 'admin',
    update_time = current_timestamp,
    remark = v.remark
from (
    values
    (1900, 'TG管理', 0, 5, 'tg', null, '', '', 1, 0, 'M', '0', '0', '', 'message', 'TG管理目录'),
    (1901, '账号管理', 1900, 1, 'account', 'tg/account/index', '', '', 1, 0, 'C', '0', '0', 'tg:account:list', 'user', 'TG账号管理'),
    (1902, '频道管理', 1900, 2, 'chat', 'tg/chat/index', '', '', 1, 0, 'C', '0', '0', 'tg:chat:list', 'tree', 'TG频道管理'),
    (1903, '监听规则', 1900, 3, 'listener', 'tg/listener/index', '', '', 1, 0, 'C', '0', '0', 'tg:listener:list', 'monitor', 'TG监听规则'),
    (1904, '敏感词', 1900, 4, 'sensitive-word', 'tg/sensitive-word/index', '', '', 1, 0, 'C', '0', '0', 'tg:sensitive-word:list', 'eye-open', 'TG敏感词'),
    (1905, '广告词', 1900, 5, 'ad-text', 'tg/ad-text/index', '', '', 1, 0, 'C', '0', '0', 'tg:ad-text:list', 'edit', 'TG广告词'),
    (1906, '内容清理', 1900, 6, 'clean-rule', 'tg/clean-rule/index', '', '', 1, 0, 'C', '0', '0', 'tg:clean-rule:list', 'textarea', 'TG内容清理'),
    (1907, '消息中心', 1900, 7, 'message', 'tg/message/index', '', '', 1, 0, 'C', '0', '0', 'tg:message:list', 'message', 'TG消息中心'),
    (1908, '发送记录', 1900, 8, 'forward-record', 'tg/forward-record/index', '', '', 1, 0, 'C', '0', '0', 'tg:forward-record:list', 'log', 'TG发送记录')
) as v(menu_id, menu_name, parent_id, order_num, path, component, query, route_name, is_frame, is_cache, menu_type, visible, status, perms, icon, remark)
where m.menu_id = v.menu_id;
