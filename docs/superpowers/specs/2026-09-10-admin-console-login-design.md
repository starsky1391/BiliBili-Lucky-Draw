# Bilibili 管理控制台与二维码登录设计

## 目标

新增一个独立的 `admin_web` 容器，提供本机访问的运行控制台、失败动态列表、日志查看和设置页面。当前 Cookie 失效时，暂停所有需要访问或写入 B 站的任务；扫码登录成功并验证通过后自动恢复任务。

本期不增加任务控制表。任务暂停和恢复只由统一认证状态决定。

## 架构

- `dynamic_share`：保留现有调度任务；每个 B 站任务开始前检查认证状态。
- `admin_web`：FastAPI 后端和原生 HTML/CSS/JavaScript 前端。
- `bili-selenium`：提供二维码登录和现有自动化任务所需的 Selenium Remote WebDriver。
- `bili-db`：保存认证状态、失败信息和管理端读取所需的数据。

管理端仅映射到宿主机 `127.0.0.1`，不对局域网开放。

## 认证流程

1. 前端请求开始扫码登录。
2. `admin_web` 获取 Selenium 会话，打开 B 站登录页。
3. 后端返回二维码截图数据，前端轮询登录状态。
4. 用户扫码并完成 B 站验证。
5. 后端读取浏览器 Cookie，保存到 `cookie/{account_key}.json`。
6. 后端调用登录验证逻辑确认账号 UID 和会话有效。
7. 数据库认证状态变为 `AUTHENTICATED`，任务自动恢复。
8. Cookie 原文不写入数据库、不返回前端、不写入日志。

登录状态：

- `UNKNOWN`
- `AUTHENTICATED`
- `LOGIN_REQUIRED`
- `LOGIN_IN_PROGRESS`
- `LOGIN_FAILED`

## 数据结构

新增 `t_auth_session`：

- `id`
- `account_key`
- `status`
- `uid`
- `last_verified_at`
- `cookie_saved_at`
- `last_error`
- `update_time`

Cookie 文件不纳入 Git 或 Docker 镜像，通过 Compose volume 在两个服务间共享。

## 任务暂停规则

认证状态不是 `AUTHENTICATED` 时，跳过：

- 动态收集
- 详情页解析
- 评论
- 转发
- 关注
- 删除动态
- 取关

数据库查询、统计、日志记录和管理端展示继续可用。因认证失效跳过的任务不更新为永久失败。

## 管理端页面

- 总览：登录状态、任务最近执行时间、待处理统计、失败数量、Selenium 状态。
- 动态管理：默认列出失败动态；成功、跳过、已清理记录按状态折叠；点击查看详情。
- 日志：读取项目日志，支持正常/错误和关键字过滤。
- 设置：二维码登录、账号状态、Cookie 更新时间、配置状态。

删除、取关和重试等操作保留现有后端能力，前端必须二次确认。

## API

- `GET /api/overview`
- `GET /api/auth/status`
- `POST /api/auth/qrcode/start`
- `GET /api/auth/qrcode/status`
- `GET /api/dynamics/failures`
- `GET /api/dynamics/{id}`
- `GET /api/logs`

所有 API 只服务本机管理端，不返回凭证。

## 验证

- `docker compose config`
- FastAPI 健康检查
- 未登录状态下调度任务跳过 B 站操作
- 二维码登录成功后 Cookie 文件生成且认证状态恢复
- Cookie 文件、日志和数据库目录不会进入 Git 或镜像
- 前端四个页面在桌面和窄屏下可用
