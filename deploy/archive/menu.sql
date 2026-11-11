insert into sys_menu values(
    1910, '素材库', 1900, 9, 'archive', 'tg/archive/index', '', '', 1, 1,
    'C', '0', '0', 'tg:archive:list', 'documentation', 'admin', current_timestamp, '', null, '采集素材与归档'
) on conflict (menu_id) do nothing;

insert into sys_menu values(
    1911, '素材归档与重试', 1910, 1, '#', '', '', '', 1, 1,
    'F', '0', '0', 'tg:archive:write', '#', 'admin', current_timestamp, '', null, '目录创建、归档、重试'
) on conflict (menu_id) do nothing;
