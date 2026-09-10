# B 站动态扫描选择器改造设计

## 目标

适配当前 B 站用户动态页面结构，移除依赖固定 DOM 层级的绝对 XPath，并修复元素不存在时继续调用 Selenium 的问题。

## 当前问题

- B 站动态页面已经不再使用旧的 `page-dynamic` 容器。
- 现有代码依赖大量类似 `div[1]/div[3]/...` 的固定层级 XPath，页面布局变化后会超时。
- `find_first_ele()` 中两个相邻字符串缺少逗号，导致 XPath 被 Python 自动拼接。
- `find_first_ele()` 找不到元素时返回 `None`，调用方仍将其传入 Selenium，触发 `value must be a string`。

## 实现方案

### 动态列表定位

以当前页面稳定的动态卡片 class 为入口：

- `bili-dyn-item`
- `bili-dyn-list__item`

扫描时从动态卡片内部查找链接，不依赖父级具体层级。链接提取优先使用带有 B 站动态 URL 特征的 `href`，并保留现有的去重、过滤和数据库写入流程。

### 动态入口操作

每个 UP 主页面不再使用固定层级 XPath 查找“第一条动态”。改为在动态卡片中寻找可点击的动态链接或动态卡片元素，使用相对 CSS 选择器和动态 URL 特征完成定位。

### 异常处理

- 找不到动态卡片或动态链接时，记录对应 UP 主的警告并跳过。
- 禁止把 `None` 传给 `find_element` 或 `WebDriverWait`。
- 单个 UP 主失败时继续处理其他 UP 主。
- 保留现有异常日志和通知流程。

## 影响范围

- `service/search_draw_dynamic_service/SearchDynamicByUps.py`
- 必要时调整 `utils/webdriver_util.py` 的元素查找辅助函数。

不修改 `.env` 配置格式、数据库表结构、转发逻辑和定时任务。

## 验证标准

- `docker compose config --quiet` 通过。
- `docker compose up --build -d` 成功。
- Selenium 能加载 B 站动态页面并找到当前动态卡片。
- 四个配置中的 UP 主均不会因为 `page-dynamic` 不存在而触发固定层级 XPath 超时。
- `find_first_ele()` 不再返回并传递 `None` 选择器。
- 容器保持运行，数据库和 Selenium 健康检查均为 healthy。
