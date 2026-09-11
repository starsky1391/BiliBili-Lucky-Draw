# B 站抽奖动态数据库设计

## 目标

重新划分项目中的数据库表职责，避免在多个账号之间重复保存相同的动态、UP 主和开奖信息。

核心原则：

- 原始抽奖动态只保存一份。
- UP 主信息只按 `up_id` 保存一份。
- 每个账号对动态的转发结果单独保存。
- 每个账号的个人转发动态 URL 保存到账号转发关系中。
- 详情页/合集页扫描状态独立保存。
- 敏感登录凭证不写入数据库。

## 账号配置

多账号不使用 `USER_1_COOKIE`、`USER_2_COOKIE` 这类不断扩展的环境变量。

每个账号使用独立配置文件，例如：

```text
config/
└── accounts/
    ├── test_id.env
    ├── account_b.env
    └── account_c.env
```

账号配置文件保存：

```env
account_key=test_id
cookie_value=...
bili_jct=...
```

Docker 只挂载整个账号配置目录：

```yaml
volumes:
  - ./config/accounts:/app/config/accounts:ro
```

Cookie 和 `bili_jct` 属于敏感凭证，不保存到 MySQL。

## 表关系

```text
t_account
    └── t_account_dynamic

t_up_info
    └── t_draw_dynamic
            └── t_account_dynamic

t_scan_cache
    独立记录详情页/合集页扫描状态

t_statistics
    保存每轮任务汇总
```

本设计不创建 `t_account_up`。当前已有大量历史关注数据，无法可靠区分是用户手动关注还是程序主动关注，因此不建立账号与 UP 的关注来源关系。

## `t_account`

账号目录表，只保存账号标识和运行配置，不保存 Cookie。

```text
id
account_key       账号唯一标识，例如 test_id
bili_uid          B站账号 UID，可为空
enabled           是否启用
config_file       对应的账号配置文件名
insert_time
update_time
```

约束：

```text
account_key 唯一
```

说明：

- `account_key` 用于关联账号转发记录。
- `config_file` 指向 `config/accounts/` 下的配置文件。
- `enabled=1` 的账号才参与任务。
- Cookie、`SESSDATA`、`bili_jct` 等凭证只从账号配置文件读取。

## `t_up_info`

UP 主基础信息表，一个 UP 主只保存一条。

```text
up_id
is_managed       是否纳入项目处理范围
insert_time
update_time
```

约束：

```text
up_id 主键或唯一键
```

说明：

- 不保存 `up_name`，名称变化不影响业务。
- 不保存 `up_url`，主页地址由 `up_id` 生成：

```text
https://space.bilibili.com/{up_id}
```

- `is_managed=1` 表示这个 UP 属于项目可处理范围。
- 当前所有已经转发过的动态所属 UP，都迁移并标记为 `is_managed=1`。
- 后续新收集或新转发动态所属的 UP，也自动写入或更新为 `is_managed=1`。

注意：

```text
is_managed=1
```

只表示该 UP 纳入项目处理范围，不表示一定是程序主动关注的，也不表示可以安全取关。

## `t_draw_dynamic`

原始抽奖动态表。一条 B 站原动态只保存一条，与具体转发账号无关。

```text
id
dynamic_id        B站动态 ID
dyn_url           原抽奖动态 URL
up_id             发布动态的 UP UID
publish_time      B站动态真实发布时间
lottery_time      开奖或截止时间
lottery_source    explicit 或 fallback_120d
insert_time       程序发现并入库的时间
source            动态收集来源
note              备注
status            原动态处理状态
```

约束：

```text
dynamic_id 唯一
dyn_url 唯一
```

时间字段规则：

```text
publish_time = B站动态真实发布时间
```

优先从 B 站动态结构中的 `module_author.pub_ts` 获取，不能使用程序入库时间代替。

如果能识别明确的开奖/截至时间：

```text
lottery_time = 明确识别到的时间
lottery_source = explicit
```

如果没有明确的开奖/截至时间：

```text
lottery_time = publish_time + 120天
lottery_source = fallback_120d
```

这里的 120 天必须基于 `publish_time` 计算，不能基于 `insert_time` 计算。

建议的 `status`：

```text
0 = 待账号处理或仍有效
1 = 原动态已完成处理
2 = 动态已过期
3 = 动态无效、已删除或收集失败
```

说明：

- `t_draw_dynamic.status` 只表示原始动态状态。
- 不用它表示所有账号是否都已经转发。
- 多账号的转发状态由 `t_account_dynamic` 记录。

## `t_account_dynamic`

账号与原始动态的转发关系表，也用于保存该账号个人页面中的转发动态。

它不复制动态正文、UP 信息、发布时间或开奖时间。

```text
id
dynamic_id        对应 t_draw_dynamic.id
account_key       对应 t_account.account_key
share_status      该账号的转发状态
share_time        转发时间
own_dynamic_id    该账号个人转发动态 ID
own_dynamic_url   该账号个人转发动态 URL
cleanup_status    个人转发动态清理状态
error_message     最近一次失败原因
insert_time
update_time
```

唯一约束：

```text
UNIQUE(dynamic_id, account_key)
```

判断账号是否已经转发某条动态：

```sql
SELECT *
FROM t_account_dynamic
WHERE dynamic_id = ?
  AND account_key = ?;
```

如果记录存在且：

```text
share_status = 1
```

则跳过，不重复转发。

建议的 `share_status`：

```text
0 = 待转发
1 = 已成功转发
2 = 原动态已过期
3 = 转发失败
4 = 主动跳过
```

建议的 `cleanup_status`：

```text
0 = 未清理
1 = 已删除个人转发动态
2 = 删除失败
3 = 处理中
```

### `t_pending_unfollow`

`-352` 风控取关缓存表，按账号和 UP 独立保存，避免动态删除成功后丢失因风控失败的取关任务。普通取关失败只记录日志，不写入该表。

```text
account_key       当前操作账号
up_id             待取关 UP
source_dynamic_id 触发待取关的动态，可为空
status            2因352失败待重试，3处理中
error_message     最近一次失败原因
retry_time        下次允许重试时间
insert_time
update_time
```

同一账号和 UP 只保留一条缓存记录。清理任务优先处理缓存；重试取关成功后直接删除缓存记录。遇到新的 `-352` 时保存失败状态并停止本轮后续取关请求。

清理任务处理一条记录时，先将 `cleanup_status` 更新为 `3`，再调用 B 站删除接口。接口成功后更新为 `1`，失败后更新为 `2` 并保存错误原因。数据库记录始终保留，`share_status` 不因清理而改变。状态为 `3` 的记录不会被普通清理任务再次选中，避免任务中断后重复调用删除接口。

多个账号处理同一条动态时：

```text
t_draw_dynamic
└── dynamic_id = 1001，原动态只保存一条

t_account_dynamic
├── dynamic_id=1001，account_key=test_id，已转发
└── dynamic_id=1001，account_key=account_b，待转发
```

不会复制以下内容：

- 原动态 URL
- 动态正文
- UP UID
- 动态发布时间
- 开奖时间

个人转发动态清理时，按账号查询：

```sql
SELECT own_dynamic_id, own_dynamic_url
FROM t_account_dynamic
WHERE account_key = ?
  AND cleanup_status = 0
  AND own_dynamic_url IS NOT NULL;
```

## `t_scan_cache`

详情页、合集页扫描缓存表，用于避免重复打开已经扫描过的页面。

```text
scan_key
scan_type         detail 或 collection
scan_url
source
note
status            1成功，3失败
link_count        本次提取到的动态链接数量
last_error
insert_time
update_time
```

唯一标识：

```text
scan_type + scan_url
```

处理规则：

- 页面扫描成功后记录 `status=1`。
- 后续再次遇到相同页面时直接跳过。
- 页面扫描失败记录 `status=3`。
- 失败页面进入冷却期，冷却结束后允许重试。
- `t_scan_cache` 只记录页面扫描状态，不代替 `t_draw_dynamic` 保存动态。

## `t_statistics`

任务汇总表，保存每轮任务的统计结果，不代替详细日志。

```text
id
user_id 或 account_key
content
note
insert_time
```

示例：

```text
本轮收集动态 20 条，新增 5 条，详情页缓存跳过 15 次
本轮转发成功 8 条，过期 10 条，失败 2 条
```

## 暂不新增的表

### `t_account_up`

暂不创建。

原因：

- 当前已有大量历史关注关系。
- 无法可靠判断某个 UP 是用户手动关注还是程序主动关注。
- 无法据此安全执行自动取关。

### `t_task_log`

当前项目已有日志系统，可以先继续使用容器日志和 `t_statistics` 汇总，不立即新增详细日志表。

如果后续需要在前端查询历史日志，再单独增加日志持久化表。

## 旧表调整方向

```text
t_draw_dynamic
    保留并增加 dynamic_id、up_id、publish_time、lottery_source

t_share_info
    改造成 t_account_dynamic
    删除 upId、upUrl
    增加 account_key、own_dynamic_id、own_dynamic_url、cleanup_status

t_followdups
    不作为新的账号-UP 关系表使用
    历史数据只用于迁移已有 UP 信息

t_shared_urls
    后续废弃
    个人转发动态 URL 迁移到 t_account_dynamic

t_scan_cache
    保留

t_statistics
    保留，用于任务汇总

t_prize
    暂时保留，当前不参与主流程
```

## 历史数据迁移

迁移顺序：

1. 从现有 `t_share_info.upId` 去重写入 `t_up_info`。
2. 将这些 UP 的 `is_managed` 设置为 `1`。
3. 从现有 `t_draw_dynamic.dyn_url` 生成或补充 `dynamic_id`。
4. 将现有转发记录迁移到 `t_account_dynamic`。
5. 以 `dynamic_id + account_key` 去重，避免重复建立账号转发关系。
6. 将现有个人转发动态 URL 写入 `own_dynamic_url`；没有数据时保持为空。
7. 在确认迁移结果后，再停用 `t_share_info` 和 `t_shared_urls` 的写入。

迁移不得删除原表数据，先完成核对，再进行旧表废弃。

## 关键约束总结

```text
原始动态去重：
    dynamic_id 或 dyn_url

UP 去重：
    up_id

账号动态转发去重：
    dynamic_id + account_key

扫描页面去重：
    scan_type + scan_url

120天保底：
    publish_time + 120天

可处理 UP：
    当前所有已转发动态所属 UP -> is_managed=1
```
