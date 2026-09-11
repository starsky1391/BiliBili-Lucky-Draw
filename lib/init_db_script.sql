-- create database luckybili default character set utf8mb4 collate utf8mb4_general_ci;
use luckybili;

create table t_draw_dynamic
(
    dyn_url     varchar(255) charset latin1 not null
        primary key,
    dynamic_id  varchar(64)                    null,
    up_id       varchar(50)                    null,
    publish_time datetime                       null,
    insert_time datetime                    null,
    lottery_time datetime                   null,
    lottery_source varchar(30)                null,
    source      varchar(255) charset latin1 null,
    note        varchar(255)                null,
    status      varchar(20)                 null
)
    collate = utf8_bin;

create table t_scan_cache
(
    scan_key    varchar(64)   not null
        primary key,
    scan_type   varchar(50)   not null,
    scan_url    varchar(1024) not null,
    source      varchar(255)  null,
    note        varchar(255)  null,
    status      varchar(20)   null,
    link_count  int           null,
    last_error  varchar(500)  null,
    insert_time datetime      null,
    update_time datetime      null
)
    DEFAULT CHARSET = utf8mb4
    COLLATE = utf8mb4_general_ci;

create table t_followdups
(
    id          int auto_increment
        primary key,
    up_id       varchar(255) null,
    up_url      varchar(255) null,
    status      int          null,
    update_time datetime     null,
    user_id     varchar(50)  null
)
    comment '关注的up主信息';

create table t_prize
(
    id        int auto_increment
        primary key,
    status    int          null,
    prize_url varchar(255) null,
    user_id   varchar(50)  null
)
    comment '中奖信息表';

create table t_share_info
(
    id           int auto_increment,
    share_url    varchar(255) null,
    status       int          not null comment '当前动态状态',
    upId         varchar(50)  null,
    upUrl        varchar(255) null,
    machine_ip   varchar(20)  null,
    share_time   datetime     null,
    share_status int          null,
    user_id      varchar(50)  null,
    constraint t_share_info_pk
        unique (id)
)
    comment '动态转发的基本信息表';

create table t_shared_urls
(
    dyn_url     varchar(255) not null,
    insert_time datetime     null,
    update_time datetime     null,
    status      varchar(50)  null,
    user_id     varchar(20)  not null,
    primary key (user_id, dyn_url)
);

create table t_statistics
(
    id          int auto_increment
        primary key,
    content     varchar(255) collate utf8_bin null,
    insert_time datetime                      null,
    note        varchar(500) collate utf8_bin null comment '备注',
    user_id     varchar(50)                   null
);

create table if not exists t_account
(
    id          bigint auto_increment primary key,
    account_key varchar(100) not null unique,
    bili_uid    varchar(50) null,
    enabled     tinyint not null default 1,
    config_file varchar(255) null,
    insert_time datetime null,
    update_time datetime null
) engine=InnoDB default charset=utf8mb4;

create table if not exists t_up_info
(
    up_id       varchar(50) not null primary key,
    is_managed  tinyint not null default 1,
    insert_time datetime null,
    update_time datetime null
) engine=InnoDB default charset=utf8mb4;

create table if not exists t_account_dynamic
(
    id               bigint auto_increment primary key,
    dynamic_id       varchar(64) not null,
    account_key      varchar(100) not null,
    share_status     tinyint not null default 0,
    share_time       datetime null,
    own_dynamic_id   varchar(64) null,
    own_dynamic_url  varchar(255) null,
    cleanup_status   tinyint not null default 0 comment '0未清理，1已删除，2删除失败，3处理中',
    error_message    varchar(500) null,
    insert_time      datetime null,
    update_time      datetime null,
    unique key uk_account_dynamic (dynamic_id, account_key),
    key idx_account_cleanup (account_key, cleanup_status)
) engine=InnoDB default charset=utf8mb4;

create table if not exists t_pending_unfollow
(
    id                bigint auto_increment primary key,
    account_key       varchar(100) not null,
    up_id             varchar(50) not null,
    source_dynamic_id varchar(64) null,
    status            tinyint not null default 2 comment '2因352失败待重试，3处理中',
    error_message     varchar(500) null,
    retry_time        datetime null,
    insert_time       datetime not null,
    update_time       datetime not null,
    unique key uk_account_up (account_key, up_id),
    key idx_pending_unfollow (account_key, status, retry_time)
) engine=InnoDB default charset=utf8mb4;

create table if not exists t_auth_session
(
    id                bigint auto_increment primary key,
    account_key       varchar(100) not null unique,
    status            varchar(30) not null default 'UNKNOWN',
    uid               varchar(50) null,
    last_verified_at  datetime null,
    cookie_saved_at   datetime null,
    last_error        varchar(500) null,
    update_time       datetime not null
) engine=InnoDB default charset=utf8mb4;

