# 独立素材采集与归档部署

本分支 `codex/media-archive` 提供独立的 Telegram 素材采集应用。它与原转发部署共用代码基础，但使用自己的 PostgreSQL、Redis、登录密钥、Telegram session、夸克授权及素材磁盘。

默认入口为 `http://服务器地址:12581`。Compose 项目名为 `forwardbot-archive`，不复用原部署的容器名、网络或数据卷；PostgreSQL 和 Redis 不向宿主机开放端口。前端使用 `Archive-Token` Cookie，API 前缀为 `/archive-api`。

## 首次部署

在这个分支的独立检出目录中执行，勿将配置文件或数据卷指向原转发部署：

```bash
cp deploy/archive/.env.example deploy/archive/.env
cp deploy/archive/storages.example.json deploy/archive/storages.json
```

编辑 `.env`：给数据库、Redis、JWT 分别设置新的随机密钥。可以运行三次 `openssl rand -hex 32` 生成。按需修改 `ARCHIVE_HTTP_PORT`。

编辑 `storages.json`：只保留要使用的存储配置。尚未配置云端时可填 `[]`，先使用采集和本地预览。S3 密钥填写在 `.env` 中，不写入数据库或前端。以上实际配置文件已被 Git 忽略。

```bash
docker compose --env-file deploy/archive/.env -f compose.archive.yml config --quiet
docker compose --env-file deploy/archive/.env -f compose.archive.yml up -d --build
docker compose --env-file deploy/archive/.env -f compose.archive.yml logs -f backend
```

全新数据库由 PostgreSQL 初始化脚本创建管理后台基础表及菜单，后端启动前运行 `alembic upgrade head` 创建 Telegram 和归档表。启动入口还会为这套应用生成并持久化独立的传输加密密钥，不读取原部署密钥。不要把基础初始化 SQL 手动导入现有业务数据库，它包含建表重置语句。部署请使用这里的专用 Compose，不使用原转发的 Compose 或 `.env.dev`。

管理后台沿用项目基础 SQL 中的初始管理员账号，首次登录后修改密码。新数据库不含原部署的 Telegram 账号和监听规则，需在「TG管理 → 账号管理」重新登录，再同步群列表。

采集部署固定 `APP_WORKERS=1`、`APP_RELOAD=false`，不要对 backend 做多副本扩容。这样同一 Telegram session 只由一个进程持有；该进程中有独立的下载、上传和补采任务，不在新消息回调中下载大文件。

## 夸克配置

使用夸克官方 CLI 的 NDJSON 接口，官方安装源：

- https://github.com/quark-clouddrive/quarkclouddrive_offical
- https://raw.githubusercontent.com/quark-clouddrive/quarkclouddrive_offical/main/skills/quarkclouddrive/install.sh

后端镜像包含 Node.js。CLI 安装目录 `/opt/quark` 和授权用户目录 `/root` 都使用本部署专用的持久卷。首次使用时在容器内安装并授权，不复制原部署或个人电脑的 Telegram / 夸克会话：

```bash
docker compose --env-file deploy/archive/.env -f compose.archive.yml exec backend bash
curl -fL https://raw.githubusercontent.com/quark-clouddrive/quarkclouddrive_offical/main/skills/quarkclouddrive/install.sh -o /opt/quark/install.sh
bash /opt/quark/install.sh
node /opt/quark/scripts/quark-drive.cjs login
```

在终端提示的授权页完成登录。无浏览器的服务器根据 CLI 的授权引导获取授权码，然后在容器内使用 `login --token` 完成；不要把授权码写进仓库、Compose 文件或日志。可用 `node /opt/quark/scripts/quark-drive.cjs get-user-info` 检查授权状态。

默认配置 `root_fid: "0"` 表示可以从授权范围内的网盘根目录选择归档位置。也可以填写专用素材目录的 FID，将应用的目录浏览限定在这个目录之下。

```json
[{"id":"quark","name":"夸克素材库","type":"quark","cli_path":"/opt/quark/scripts/quark-drive.cjs","root_fid":"0"}]
```

目录浏览读取 CLI `browse --all` 返回的完整 JSONL 文件，不能只使用最多五项的预览列表。创建目录使用幂等的 `create-folder`。上传确认成功数量后再读取目录，核对文件名、数量和大小；不会把 CLI 退出码为零直接当作整条消息上传成功。授权失效会显示任务错误，需要重新授权后重试，不会在后台启动交互登录。

夸克适配器的远端校验依据是文件名和大小，未做远端内容的逐字节校验。重试时会复用同一内容指纹子目录里的同名、同大小文件，请勿手动替换这些文件。

该适配器按官方文档实现，真实账号授权、大视频及限额需在目标服务器验收。不要在验证前宣称云端链路已打通。

## S3 配置

示例见 `storages.example.json`。目标 bucket 必须预先存在，应用只创建目录前缀，不创建 bucket。

- AWS：填写 `bucket`、`region`。也可省略两个凭据环境变量名，使用容器运行环境提供的 IAM 凭据链。
- S3 兼容服务：额外填写 `endpoint_url`；按服务要求使用 `addressing_style: "path"`。
- `prefix` 限定素材根路径；界面内的 `/` 代表这个前缀，不代表整个 bucket。
- 所需能力：列举指定前缀、读取对象元信息、上传对象及分段上传的相关操作。应用不删除远端对象。

上传使用 SDK 分段传输，完成后核对远端大小和 SHA-256 元信息。同名但内容不同的对象会阻止上传，不直接覆盖；远端已存在同一文件时跳过，支持“远端已成功而本地状态尚未提交”后的恢复。

## 使用方式

1. 在监听规则中选择多个来源群，开启「自动下载图片、视频」。新建规则默认只采集；如果同时开启转发，则还需填写目标群。
2. 新消息的原文先入库，图片和视频进入下载队列；相册共用一条素材记录，保留每项 caption。敏感词拦截规则仍然有效，其他类型附件显示为「非图片或视频」。
3. 在「素材库」查看下载状态、预览素材，按群、原文、下载状态或归档状态筛选。
4. 选中已就绪消息，点击归档，选择存储及已有目录，或点击新建目录。每条消息会生成包含原文、元数据、全部已采集图片视频的子目录。
5. 在消息详情查看每个文件的上传结果。失败后重试只处理未完成文件；可以把同一消息归档到多个存储或不同目录。

子目录包含日期、来源群 ID、Telegram 消息 ID 和内容指纹。归档提交时固定内容清单；晚到的相册素材会增加版本，旧归档保留，素材列表重新显示待归档。文字及元数据为 `message.txt` 和 `metadata.json`，媒体使用源消息 ID 命名，避免原文件同名覆盖。

首次补采扫描只记录监控起点，不下载启用前的全部历史。随后每分钟根据持久化游标补采停机和断线期间遗漏的消息。恢复任务仍要求源消息存在、Telegram 账号仍有访问权限。

## 磁盘、恢复与权限

- 素材保存在 `forwardbot-archive_media` 卷，不挂到公开静态目录；预览需登录并具有 `tg:archive:list` 权限，由服务端签发绑定单个素材的短期链接。视频支持 Range 请求。
- `tg:archive:write` 控制目录创建、归档提交及失败重试。管理员可在角色管理中分配这两个权限。
- 下载写入 `.part` 文件，按已有字节恢复；完整性检查后原子替换正式文件。任务状态、重试次数、租约、上传文件结果都保存在数据库。
- 磁盘剩余空间不足时暂停并自动重试，`ARCHIVE_MIN_FREE_BYTES` 默认预留 256 MiB。应按视频量为素材卷预留容量。
- 原来的七天转发缓存清理只清理 `storage_kind=cache`。采集素材不会因为超过七天或完成上传被自动删除，第一版保留本地素材。
- 停服务用 `docker compose ... stop` 或不带 `-v` 的 `down`。不要使用 `down -v`，这会删除本部署的数据库和素材卷。
- 备份应同时覆盖数据库、素材卷、应用状态、夸克授权以及部署配置。恢复时使用同一份配套备份。

## 验证

开发环境安装后端依赖及 `pytest aiosqlite`，执行：

```bash
cd ruoyi-fastapi-backend
python -m pytest tests/test_telegram_services.py tests/test_media_archive.py -q
cd ../ruoyi-fastapi-frontend
npm install
npm run build:archive
```

测试使用临时 SQLite 数据库与模拟云端，不访问生产数据库或发送 Telegram 消息。部署验收还应包含实际群里的图片、相册、大视频、重启恢复、夸克登录和上传，以及 S3 上传。未提供真实账号或存储凭据时，这些外部链路不能由本地测试替代。
