# 多用户 Cookie 管理与手动任务选择设计

## 目标

- 使用 Cookie 文件和 UID 管理多个 B 站账号，不再依赖 Compose 中的 `my_user_id`、`multi_users`。
- 每个账号独立保存 Cookie、用户名、备注、启用状态和认证状态。
- 管理端支持扫码添加账号、重新登录、验证 Cookie、启停账号和修改备注。
- 手动任务支持选择一个或多个账号，单个账号登录失效时暂停该账号并继续处理其他账号。

## 数据模型

继续使用 `t_account` 和 `t_auth_session`：

- `t_account.account_key`：B 站 UID，作为账号主键。
- `t_account.bili_uid`：B 站 UID。
- `t_account.username`：通过已登录会话访问 `/x/web-interface/nav` 获取的 B 站昵称。
- `t_account.remark`：用户自定义备注；新账号优先使用 `username` 初始化。
- `t_account.enabled`：是否参与自动任务。
- `t_auth_session`：按 UID 保存认证状态和最近验证时间。

Cookie 规范路径为 `cookie/{uid}.json`。旧的非 UID 文件不删除、不强制重命名；启动或登录验证时读取 Cookie 内的 `DedeUserID`，按 UID 建立账号记录，并兼容旧文件继续使用。

## 登录与用户名

扫码流程只允许一个当前 Selenium 登录会话：

1. 创建扫码会话。
2. 扫码成功后从 `DedeUserID` Cookie 获取 UID。
3. 在同一已登录会话请求 `/x/web-interface/nav` 获取 `data.uname`。
4. 保存 Cookie 到 `cookie/{uid}.json`，创建或更新账号资料。
5. 更新该 UID 的认证状态。

Cookie 登录验证必须显式传入 UID，并只更新该 UID 的认证状态。认证失败抛出 `AuthenticationRequiredError`，任务将该 UID 标记为暂停，不继续注入后续 B 站操作。

## 手动任务

请求格式：

```json
{"user_ids": ["123456789", "987654321"]}
```

后台校验 UID 存在、已启用且存在可用 Cookie，然后按 UID 顺序执行。返回结果按 UID 记录 `completed`、`paused` 或 `failed`。未选择账号时拒绝启动。

## 管理端

- 新增“用户管理”导航页。
- 列表展示 UID、B 站用户名、备注、登录状态、Cookie 状态、启用状态和操作。
- UID 使用主色，备注使用浅色显示，例如 `123456789 备注内容`。
- 备注编辑只更新 `remark`，不会覆盖 `username`。
- 添加账号和重新登录复用扫码弹窗。

## 自动任务

自动任务从数据库启用账号列表获取 UID，不再读取 `multi_users`。收集任务使用任一已认证账号扫描订阅 UP，随后按账号分别执行转发；清理、回填和重新识别按账号独立登录和执行。一个账号失效或失败不会阻断其他账号。

## 验证

- 两个 UID 分别扫码并生成独立 Cookie 文件。
- 修改备注后重新验证，备注保持不变。
- 用户名获取失败时仍可创建 UID 账号，后续验证成功可补齐用户名。
- 手动任务只处理选中的 UID。
- 一个 UID Cookie 失效时状态为暂停，其他 UID 继续执行。
- 旧非 UID Cookie 文件可被识别，不删除原文件。
