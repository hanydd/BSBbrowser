# BSB browser

BSB browser，全称 BilibiliSponsorBlock Browser，是一个基于 Django 的
Web 界面，用于浏览 SponsorBlock 兼容数据库中的数据。

当前仓库面向 BilibiliSponsorBlock 的使用场景，提供片段、视频、用户和 UUID
详情页，并集成了跳转 Bilibili 页面、嵌入播放器、本地开发配置和 Docker 部署方案。

## 项目功能

- 在首页浏览最近提交的片段
- 按视频 ID 或视频链接、用户名、用户公开 ID、片段 UUID 进行搜索
- 查看视频、用户、片段维度的统计信息
- 按投票数、播放量、分类、动作类型、伪隐藏状态筛选表格数据
- 从详情页直接跳转到对应的 Bilibili 页面或播放器

## 技术栈

- Python 3.12
- Django 5.1
- PostgreSQL
- Redis
- Gunicorn
- WhiteNoise

## 数据要求

本项目本身不负责创建 SponsorBlock 的数据库结构。当前 Django 模型均使用
`managed = False`，因此运行前需要准备一个已经存在的 PostgreSQL 数据库，
其中至少应包含以下表：

- `sponsorTimes`
- `userNames`
- `vipUsers`
- `warnings`
- `lockCategories`
- `config`

其中 `config` 表中还应包含 `updated` 这一项，用于在页面中显示数据最后更新时间。

## 本地开发

1. 创建并激活虚拟环境
2. 安装依赖：`pip install -r requirements.txt`
3. 确保 PostgreSQL 和 Redis 可用
4. 根据本地环境修改 [`SBtools/settings/development.py`](SBtools/settings/development.py)
5. 启动开发服务器：`python manage.py runserver`

默认情况下，[`manage.py`](manage.py) 会使用 `SBtools.settings.development`。

## Docker 部署

仓库内已经包含可直接使用的 Docker 部署配置。

1. 复制 `.env.docker.example` 为 `.env.docker`
2. 根据实际环境填写 `SECRET_KEY`、`DB_PASSWORD` 以及 PostgreSQL / Redis 相关配置
3. 执行 `docker compose up --build -d`

需要注意：

- 容器默认使用 `SBtools.settings.docker`
- [`docker-entrypoint.sh`](docker-entrypoint.sh) 会先执行 `python manage.py migrate --noinput`，然后启动 Gunicorn
- 默认配置下，容器会通过 `host.docker.internal` 连接宿主机上的 PostgreSQL 和 Redis
- 静态文件会在镜像构建阶段收集到 `/app/staticfiles`，并由 WhiteNoise 提供服务

相关文件：

- [`.env.docker.example`](.env.docker.example)
- [`docker-compose.yml`](docker-compose.yml)
- [`Dockerfile`](Dockerfile)
- [`docker-entrypoint.sh`](docker-entrypoint.sh)

## 设置模块

- [`SBtools/settings/development.py`](SBtools/settings/development.py)：本地开发环境配置
- [`SBtools/settings/docker.py`](SBtools/settings/docker.py)：Docker 环境配置
- [`SBtools/settings/production.py`](SBtools/settings/production.py)：非 Docker 的生产环境配置

## 路由说明

- `/`：首页，展示最近片段并提供搜索入口
- `/video/<videoid>/`：视频详情页，展示该视频下的所有片段
- `/userid/<userid>/`：用户详情页，展示该用户 ID 的所有片段
- `/username/<username>/`：用户名详情页，展示该用户名下的所有片段
- `/uuid/<uuid>/`：片段详情页，展示单个片段及其关联视频上下文

## 许可证

[AGPL-3.0-or-later](https://www.gnu.org/licenses/agpl-3.0.html)
