-- Telegram ForwardBot PostgreSQL initialization.
-- This file is intentionally standalone so it can be applied after the base RuoYi schema.

create table if not exists tg_account (
    account_id bigserial primary key,
    account_name varchar(100) not null,
    phone varchar(50) not null,
    api_id integer not null,
    api_hash varchar(255) not null,
    session_path varchar(500),
    session_status varchar(30) not null default 'logged_out',
    login_code_hash varchar(255),
    last_error text,
    status char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500)
);

alter table tg_account add column if not exists login_code_hash varchar(255);

create table if not exists tg_chat (
    chat_pk bigserial primary key,
    account_id bigint not null,
    chat_id varchar(100) not null,
    chat_title varchar(255) not null,
    username varchar(255),
    chat_type varchar(30) not null,
    can_listen char(1) not null default 'N',
    can_send char(1) not null default 'N',
    status char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500),
    constraint uq_tg_chat_account_chat unique(account_id, chat_id)
);

create table if not exists tg_listener_rule (
    rule_id bigserial primary key,
    account_id bigint not null,
    source_chat_pk bigint not null,
    source_chat_pks varchar(1000),
    target_chat_pks varchar(1000) not null,
    rule_name varchar(100) not null,
    status char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500)
);

create table if not exists tg_sensitive_word (
    word_id bigserial primary key,
    word varchar(255) not null,
    match_case char(1) not null default 'N',
    status char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500)
);

create table if not exists tg_ad_text (
    ad_id bigserial primary key,
    ad_name varchar(100) not null,
    ad_content text not null,
    enabled char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500)
);

create table if not exists tg_content_clean_rule (
    clean_id bigserial primary key,
    clean_name varchar(100) not null,
    match_text text not null,
    replacement text,
    match_case char(1) not null default 'Y',
    status char(1) not null default '0',
    create_by varchar(64) default '',
    create_time timestamp,
    update_by varchar(64) default '',
    update_time timestamp,
    remark varchar(500)
);

insert into tg_content_clean_rule(clean_name, match_text, replacement, match_case, status, create_by, create_time, remark)
select
    '移除大事件频道投稿尾巴',
    '关注大事件频道➡️ @bx666 投稿：@tx188',
    '',
    'Y',
    '0',
    'admin',
    current_timestamp,
    '发送前移除固定渠道推广文案'
where not exists (
    select 1 from tg_content_clean_rule where match_text = '关注大事件频道➡️ @bx666 投稿：@tx188'
);

create table if not exists tg_message (
    message_id bigserial primary key,
    account_id bigint not null,
    source_chat_pk bigint,
    source_chat_id varchar(100) not null,
    source_chat_title varchar(255),
    telegram_message_id bigint not null,
    message_text text,
    sent_at timestamp,
    is_sensitive char(1) not null default 'N',
    sensitive_word varchar(255),
    auto_forward_status varchar(30) not null default 'pending',
    create_time timestamp,
    update_time timestamp,
    constraint uq_tg_message_source unique(account_id, source_chat_id, telegram_message_id)
);

create table if not exists tg_message_media (
    media_id bigserial primary key,
    message_id bigint not null,
    media_type varchar(30) not null,
    local_path varchar(500) not null default '',
    source_telegram_message_id bigint,
    media_index integer not null default 0,
    file_name varchar(255),
    mime_type varchar(100),
    file_size bigint,
    create_time timestamp
);

create table if not exists tg_forward_record (
    record_id bigserial primary key,
    message_id bigint,
    account_id bigint not null,
    target_chat_pk bigint,
    target_chat_id varchar(100) not null,
    target_chat_title varchar(255),
    forward_type varchar(20) not null,
    status varchar(30) not null,
    sent_telegram_message_id bigint,
    error_message text,
    create_time timestamp
);

alter table tg_forward_record alter column message_id drop not null;

create index if not exists idx_tg_message_account_time on tg_message(account_id, create_time desc);
create index if not exists idx_tg_forward_record_message on tg_forward_record(message_id);
create index if not exists idx_tg_listener_rule_account_source on tg_listener_rule(account_id, source_chat_pk);

insert into sys_job(
    job_name,
    job_group,
    job_executor,
    invoke_target,
    job_args,
    job_kwargs,
    cron_expression,
    misfire_policy,
    concurrent,
    status,
    create_by,
    create_time,
    update_by,
    update_time,
    remark
)
select
    'TG媒体本地文件清理',
    'default',
    'default',
    'module_task.telegram_media_cleanup.cleanup_expired_local_files',
    null,
    null,
    '0 0 3 * * ?',
    '3',
    '1',
    '0',
    'admin',
    current_timestamp,
    '',
    null,
    '自动删除7天前的Telegram媒体本地文件'
where not exists (
    select 1 from sys_job where invoke_target = 'module_task.telegram_media_cleanup.cleanup_expired_local_files'
);

insert into sys_menu values(1900, 'TG管理', 0, 5, 'tg', null, '', '', 1, 0, 'M', '0', '0', '', 'message', 'admin', current_timestamp, '', null, 'TG管理目录')
on conflict (menu_id) do nothing;
insert into sys_menu values(1901, '账号管理', 1900, 1, 'account', 'tg/account/index', '', '', 1, 0, 'C', '0', '0', 'tg:account:list', 'user', 'admin', current_timestamp, '', null, 'TG账号管理')
on conflict (menu_id) do nothing;
insert into sys_menu values(1902, '频道管理', 1900, 2, 'chat', 'tg/chat/index', '', '', 1, 0, 'C', '0', '0', 'tg:chat:list', 'tree', 'admin', current_timestamp, '', null, 'TG频道管理')
on conflict (menu_id) do nothing;
insert into sys_menu values(1903, '监听规则', 1900, 3, 'listener', 'tg/listener/index', '', '', 1, 0, 'C', '0', '0', 'tg:listener:list', 'monitor', 'admin', current_timestamp, '', null, 'TG监听规则')
on conflict (menu_id) do nothing;
insert into sys_menu values(1904, '敏感词', 1900, 4, 'sensitive-word', 'tg/sensitive-word/index', '', '', 1, 0, 'C', '0', '0', 'tg:sensitive-word:list', 'eye-open', 'admin', current_timestamp, '', null, 'TG敏感词')
on conflict (menu_id) do nothing;
insert into sys_menu values(1905, '广告词', 1900, 5, 'ad-text', 'tg/ad-text/index', '', '', 1, 0, 'C', '0', '0', 'tg:ad-text:list', 'edit', 'admin', current_timestamp, '', null, 'TG广告词')
on conflict (menu_id) do nothing;
insert into sys_menu values(1906, '内容清理', 1900, 6, 'clean-rule', 'tg/clean-rule/index', '', '', 1, 0, 'C', '0', '0', 'tg:clean-rule:list', 'textarea', 'admin', current_timestamp, '', null, 'TG内容清理')
on conflict (menu_id) do nothing;
insert into sys_menu values(1907, '消息中心', 1900, 7, 'message', 'tg/message/index', '', '', 1, 0, 'C', '0', '0', 'tg:message:list', 'message', 'admin', current_timestamp, '', null, 'TG消息中心')
on conflict (menu_id) do nothing;
insert into sys_menu values(1908, '发送记录', 1900, 8, 'forward-record', 'tg/forward-record/index', '', '', 1, 0, 'C', '0', '0', 'tg:forward-record:list', 'log', 'admin', current_timestamp, '', null, 'TG发送记录')
on conflict (menu_id) do nothing;

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
