// ==UserScript==
// @name         Bili.Dynamic.AutoDel.Debug.V10
// @namespace    https://github.com/
// @version      2026.09.07-debug-v10
// @description  删除B站转发的已开奖动态和源动态已被删除的动态。
// @author       monSteRhhe
// @match        http*://*.bilibili.com/*
// @icon         https://www.bilibili.com/favicon.ico
// @grant        GM_info
// @grant        GM_addStyle
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_notification
// @grant        GM_registerMenuCommand
// @run-at       document-end
// @require      https://unpkg.com/axios/dist/axios.min.js
// ==/UserScript==
/* globals axios, waitForKeyElements */

(function() {
    'use strict';

    const LOG_PREFIX = '[Bili.Dynamic.AutoDel.V10]';
    const VERBOSE_LOG = false;
    let scan_running = false,
        detail_blocked_until = 0;

    function formatDebugData(data) {
        if (data === undefined) {
            return '';
        }

        try {
            return JSON.stringify(data);
        } catch (error) {
            return String(data);
        }
    }

    function debugLog(message, data) {
        if (VERBOSE_LOG) {
            console.log(LOG_PREFIX, message, formatDebugData(data));
        }
    }

    function debugWarn(message, data) {
        console.warn(LOG_PREFIX, message, formatDebugData(data));
    }

    function debugError(message, data) {
        console.error(LOG_PREFIX, message, formatDebugData(data));
    }

    function sleep(ms) {
        return new Promise(function(resolve) {
            setTimeout(resolve, ms);
        });
    }

    async function randomDelay(min, max) {
        let delay = min + Math.floor(Math.random() * (max - min + 1));
        await sleep(delay);
        return delay;
    }

    function formatAxiosError(error) {
        return {
            message: error && error.message,
            code: error && error.code,
            http_status: error && error.response && error.response.status,
            response_code: error && error.response && error.response.data
                ? error.response.data.code
                : undefined,
            response_message: error && error.response && error.response.data
                ? error.response.data.message || error.response.data.msg
                : undefined
        };
    }

    function blockDetailRequests(reason) {
        detail_blocked_until = Date.now() + 15 * 60 * 1000;
        debugWarn('详情接口进入冷却期。', {
            reason: reason,
            cooldown_minutes: 15
        });
    }

    /**
     * 初始化数据的值
     */
    if (GM_getValue('set-unfollow') == undefined) {
        GM_setValue('set-unfollow', false);
    }
    if (GM_getValue('unfollow-list') == undefined) {
        GM_setValue('unfollow-list', []);
    }

    /**
     * 弹窗样式
     */
    let style = `
        .setting-content {
            color: #000;
            z-index: 10;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 400px;
            height: 310px;
            background-color: #efecfa;
            border-radius: 10px;
            padding: 10px;
        }
        .setting-content .setting-header {
            font-size: 25px;
            line-height: 25px;
            padding: 5px 20px;
            margin-bottom: 10px;
        }
        .setting-content .setting-body {
            width: 340px;
            height: 190px;
            margin: 0 auto;
            padding: 10px 10px 0 10px;
            background-color: #fff;
            border-radius: 10px;
            padding: 10px;
            font-size: 15px;
            overflow-y: auto;
        }
        .setting-content .setting-footer {
            text-align: right;
            padding: 17px 20px 17px 0;
        }
        .setting-content .setting-footer button {
            cursor: pointer;
            border-radius: 25px;
            background-color: #ffffff;
            border: none;
            height: 30px;
            min-width: 50px;
            padding: 5px 10px;
            font-size: 85%;
        }
        `;

    GM_addStyle(style);

    /**
     * 打开设置弹窗
     */
    function openSettingWindow() {
        // 创建弹窗
        let main_window = document.createElement('div');
        main_window.className = 'setting-popup';
        main_window.innerHTML = `
            <div class="setting-content">
                <div class="setting-header">
                    <span>设置<span>
                </div>
                <div class="setting-body">
                    <div class="setting-item">
                        <label>启用取关功能</label>
                        <input type="checkbox" id="set-unfollow" />
                    </div>
                </div>
                <div class="setting-footer">
                    <button class="setting-close">关闭</button>
                </div>
            </div>
        `;
        document.body.appendChild(main_window);

        // 绑定点击事件
        document.querySelector('.setting-close').addEventListener('click', closeSettingWindow);

        // 设置选中状态
        let checkbox_list = document.querySelectorAll('.setting-body input');
        for (let node of checkbox_list) {
            node.checked = GM_getValue(node.id);

            node.addEventListener('change', () => {
                GM_setValue(node.id, node.checked);
            })
        }
    }

    /**
     * 关闭弹窗
     */
    function closeSettingWindow() {
        document.body.removeChild(document.querySelector('.setting-popup'));
    }

    /**
     * 获取 X 天前的日期
     * @param {string} num 往前的天数
     * @returns 返回之前的日期，格式YY-MM-DD
     */
    function getBeforeDate(num) {
        let d = new Date();
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() - Number(num));
        let year = d.getFullYear(),
            month = d.getMonth() + 1, // getMonth() 返回的值为月份数-1
            day = d.getDate(),
            before_date = year + '-' + (month < 10 ? ('0' + month) : month) + '-' + (day < 10 ? ('0' + day) : day);
        return before_date;
    }

    /**
     * 获取 X 天前当天零点的时间戳
     * @param {number} num 往前的天数
     * @returns 返回 Unix 时间戳 (秒)
     */
    function getBeforeTimestamp(num) {
        let d = new Date();
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() - Number(num));
        return Math.floor(d.getTime() / 1000);
    }

    /**
     * 获取动态信息
     * @param {string} duid 用户的 DedeUserID
     * @param {string} offset 前往下一页动态的参数
     * @param {string} mode 选择的模式
     * @param {string} input 输入的内容
     */
    async function getDynamics(duid, offset, mode, input, scan_state) {
        let dynamics_api = 'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?offset=' + offset + '&host_mid=' + duid, // 动态 API
            lottery_api = 'https://api.vc.bilibili.com/lottery_svr/v1/lottery_svr/lottery_notice?business_type=4&business_id='; // 互动抽奖 API
        scan_state = scan_state || {
            limit: mode == 'latest_x' ? Number(input) : null,
            processed_count: 0
        };

        debugLog('开始请求动态分页。', {
            mode: mode,
            offset: offset,
            uid: duid
        });

        try {
            let response = await axios({
                url: dynamics_api,
                withCredentials: true // 跨域使用凭证
            });

            debugLog('动态分页接口返回。', {
                code: response.data.code,
                item_count: response.data.data && response.data.data.items
                    ? response.data.data.items.length
                    : 0,
                next_offset: response.data.data && response.data.data.offset
            });

            if (offset == '') {
                if (mode == 'auto') {
                    sendNotification('开始自动判断删除互动抽奖动态。');
                }
                if (mode == 'user') {
                    sendNotification('开始删除转发用户 ' + input + ' 的动态。');
                }
                if (mode == 'days_ago') {
                    sendNotification('开始删除 ' + getBeforeDate(input) + ' 之前的动态。');
                }
                if (mode == 'latest_x') {
                    sendNotification('开始检测最新 ' + input + ' 个动态。');
                }
            }

            if (response.data.code != 0) {
                debugError('获取动态失败。', response.data);
                sendNotification('获取动态失败，请查看控制台。');
                return;
            }

            let items_list = response.data.data.items || []; // 动态信息的数组
            for (let data of items_list) {
                if (scan_state.limit != null && scan_state.processed_count >= scan_state.limit) {
                    break;
                }
                debugLog('开始处理动态。', {
                    id: data.id_str,
                    type: data.type,
                    has_orig: data.orig != null,
                    orig_id: data.orig && data.orig.id_str
                });
                await processDynamic(data, mode, input, lottery_api);
                scan_state.processed_count += 1;
            }

            if (scan_state.limit != null && scan_state.processed_count >= scan_state.limit) {
                sendNotification('已检测最新 ' + scan_state.limit + ' 个动态。');
                let set_unfollow = Boolean(GM_getValue('set-unfollow')),
                    pending_unfollow_list = GM_getValue('unfollow-list') || [];
                if (set_unfollow && pending_unfollow_list.length > 0) {
                    await unfollowUser();
                }
                return;
            }

            let next_offset = response.data.data.offset;
            if (next_offset && next_offset != offset) {
                debugLog('当前分页处理完成，准备请求下一页。', {
                    current_offset: offset,
                    next_offset: next_offset
                });
                await randomDelay(1500, 3000);
                await getDynamics(duid, next_offset, mode, input, scan_state);
                return;
            }

            debugLog('分页结束。', {
                final_offset: offset
            });
            sendNotification('你已经到达了世界的尽头。');

            let set_unfollow = Boolean(GM_getValue('set-unfollow')),
                pending_unfollow_list = GM_getValue('unfollow-list') || [];
            debugLog('扫描完成，检查取关状态。', {
                set_unfollow: set_unfollow,
                pending_unfollow_list: pending_unfollow_list,
                pending_unfollow_count: pending_unfollow_list.length
            });

            if (set_unfollow) {
                await unfollowUser();
            }
        } catch (error) {
            debugError('处理动态失败。', formatAxiosError(error));
            sendNotification('处理动态失败，请查看控制台。');
        }
    }

    /**
     * 处理单条动态
     * @param {object} data 动态信息
     * @param {string} mode 选择的模式
     * @param {string} input 输入的内容
     * @param {string} lottery_api 互动抽奖 API
     */
    async function processDynamic(data, mode, input, lottery_api) {
        let auto_mode = mode == 'auto' || mode == 'latest_x';

        debugLog('进入单条动态判断。', {
            id: data.id_str,
            type: data.type,
            mode: mode,
            has_orig: data.orig != null,
            orig_id: data.orig && data.orig.id_str
        });

        if (auto_mode
            && data.type == 'DYNAMIC_TYPE_FORWARD'
            && isMissingDynamicId(data.orig && data.orig.id_str)) {
            debugLog('判定为源动态已删除，准备删除转发动态。', {
                id: data.id_str
            });
            if (await deleteDynamic(data)) {
                sendNotification('源动态已删除，已删除转发动态：' + data.id_str);
            }
            return;
        }

        if (data.orig == null || isMissingDynamicId(data.orig.id_str)
            || data.orig.modules == null
            || data.orig.modules.module_author == null) {
            debugWarn('动态结构不完整，跳过。', {
                id: data.id_str,
                type: data.type,
                has_orig: data.orig != null,
                orig_id: data.orig && data.orig.id_str
            });
            return;
        }

        let orig_id_str = data.orig.id_str, // 源动态 ID
            author = data.orig.modules.module_author;

        if (auto_mode) {
            let lottery_info = await getLotteryInfo(lottery_api + orig_id_str),
                now_timestamp = Math.floor(Date.now() / 1000),
                pub_timestamp = Number(author.pub_ts),
                fallback_timestamp = pub_timestamp + 120 * 24 * 60 * 60,
                summary_lottery_markers = getLotteryMarkers(data),
                summary_lottery_date_contexts = getLotteryDateContexts(data),
                detail_data = null,
                detail_requested = false;

            if (summary_lottery_markers.length > 0
                && summary_lottery_date_contexts.length == 0) {
                detail_requested = true;
                detail_data = await getDynamicDetail(orig_id_str);
            }

            let lottery_source = detail_data || data,
                is_lottery_dynamic = isLotteryDynamic(data)
                    || isLotteryDynamic(lottery_source),
                dynamic_lottery_end_timestamp = getLotteryEndTimestampFromDynamic(lottery_source),
                api_lottery_end_timestamp = lottery_info
                    && lottery_info.end_timestamp != null
                    ? lottery_info.end_timestamp
                    : null,
                lottery_end_timestamp = api_lottery_end_timestamp
                    || dynamic_lottery_end_timestamp;

            debugLog('自动判断抽奖信息。', {
                dynamic_id: data.id_str,
                orig_id: orig_id_str,
                author: author.name,
                pub_ts: author.pub_ts,
                lottery_info: lottery_info,
                now_timestamp: now_timestamp,
                fallback_timestamp: fallback_timestamp,
                is_lottery_dynamic: is_lottery_dynamic,
                lottery_markers: Array.from(new Set(
                    summary_lottery_markers.concat(getLotteryMarkers(lottery_source))
                )),
                dynamic_lottery_end_timestamp: dynamic_lottery_end_timestamp,
                api_lottery_end_timestamp: api_lottery_end_timestamp,
                lottery_end_timestamp: lottery_end_timestamp,
                lottery_date_contexts: getLotteryDateContexts(lottery_source),
                detail_requested: detail_requested,
                detail_loaded: detail_data != null,
                text_source: 'original-only'
            });

            let should_delete = (
                    lottery_info !== undefined
                    && lottery_info !== null
                    && lottery_info.status == '2'
                )
                || (
                    lottery_end_timestamp != null
                    && now_timestamp >= lottery_end_timestamp
                    && (
                        lottery_info !== undefined
                        || dynamic_lottery_end_timestamp != null
                    )
                )
                || (
                    lottery_info !== undefined
                    && lottery_end_timestamp == null
                    && is_lottery_dynamic
                    && Number.isFinite(pub_timestamp)
                    && now_timestamp >= fallback_timestamp
                );

            if (should_delete) {
                debugLog('自动判断：满足删除条件。', {
                    dynamic_id: data.id_str,
                    orig_id: orig_id_str,
                    reason: lottery_info !== undefined
                        && lottery_info !== null
                        && lottery_info.status == '2'
                        ? '抽奖状态为已开奖'
                        : lottery_end_timestamp != null
                            ? dynamic_lottery_end_timestamp != null
                                ? '动态正文已超过明确开奖时间'
                                : '抽奖接口已超过明确开奖时间'
                            : '抽奖接口无明确开奖信息，满足原动态发布时间加120天兜底'
                });
                if (await deleteDynamic(data)) {
                    saveUnfollowUserIfNeeded(data);
                }
            } else {
                debugLog('自动判断：跳过删除。', {
                    dynamic_id: data.id_str,
                    orig_id: orig_id_str,
                    reason: lottery_info === undefined
                        ? '抽奖接口请求失败'
                        : lottery_info === null
                            ? lottery_end_timestamp != null
                                ? '动态正文明确开奖时间尚未到'
                                : !is_lottery_dynamic
                                ? '没有抽奖信息'
                                : !Number.isFinite(pub_timestamp)
                                    ? '抽奖动态发布时间无效，无法计算120天兜底'
                                    : now_timestamp < fallback_timestamp
                                        ? '抽奖动态接口无记录，尚未满足120天兜底'
                                        : '抽奖动态接口无记录，但判断结果异常'
                            : lottery_end_timestamp != null
                                ? '明确开奖时间尚未到'
                            : '尚未满足删除条件'
                });
            }
        }

        if (mode == 'user') {
            //* 判断用户名 / UID
            if (input.indexOf(author.name) != -1
                || input.indexOf(author.mid) != -1) {
                debugLog('指定用户：匹配成功，准备删除。', {
                    dynamic_id: data.id_str,
                    author: author.name,
                    mid: author.mid
                });
                if (await deleteDynamic(data)) {
                    saveUnfollowUserIfNeeded(data);
                }
            } else {
                debugLog('指定用户：未匹配，跳过。', {
                    dynamic_id: data.id_str,
                    author: author.name,
                    mid: author.mid
                });
            }
        }

        if (mode == 'days_ago') {
            let dyn_timestamp = Number(author.pub_ts), // 源动态发布时间戳 (秒)
                lottery_info = await getLotteryInfo(lottery_api + orig_id_str),
                status = lottery_info == null ? null : lottery_info.status;

            //* 比较动态日期（包含年份）+ 排除互动抽奖未开奖的动态
            if (lottery_info !== undefined
                && lottery_info !== null
                && dyn_timestamp <= getBeforeTimestamp(input)
                && (status === null || status == '2')) {
                debugLog('按天数：满足删除条件。', {
                    dynamic_id: data.id_str,
                    pub_ts: dyn_timestamp,
                    threshold: getBeforeTimestamp(input),
                    lottery_status: status
                });
                if (await deleteDynamic(data)) {
                    saveUnfollowUserIfNeeded(data);
                }
            } else {
                debugLog('按天数：跳过删除。', {
                    dynamic_id: data.id_str,
                    pub_ts: dyn_timestamp,
                    threshold: getBeforeTimestamp(input),
                    lottery_info: lottery_info,
                    lottery_status: status
                });
            }
        }
    }

    /**
     * 判断源动态 ID 是否为空或已失效
     * @param {string|number} id 动态 ID
     * @returns 返回是否为空或已失效
     */
    function isMissingDynamicId(id) {
        return id == null || id == '' || String(id) == '0';
    }

    /**
     * 判断源动态是否包含抽奖标记
     * @param {object} data 动态信息
     * @returns 返回是否为抽奖动态
     */
    function isLotteryDynamic(data) {
        return getLotteryMarkers(data).length > 0;
    }

    /**
     * 获取原动态主体；列表项使用 orig，原动态详情项直接使用自身
     * @param {object} data 动态信息
     * @returns 返回可供解析的原动态主体
     */
    function getOriginalDynamicSource(data) {
        if (data == null || typeof data != 'object') {
            return null;
        }

        return data.orig != null && typeof data.orig == 'object'
            ? data.orig
            : data;
    }

    /**
     * 获取动态中的抽奖标记
     * @param {object} data 动态信息
     * @returns 返回匹配到的抽奖标记
     */
    function getLotteryMarkers(data) {
        let source = getOriginalDynamicSource(data);
        if (source == null) {
            return [];
        }

        let source_json = JSON.stringify(source);
        let marker_patterns = [
                'RICH_TEXT_NODE_TYPE_LOTTERY',
                'lottery',
                'award',
                'prize',
                '抽奖',
                '开奖',
                '中奖',
                '奖品'
            ],
            markers = [];

        for (let marker of marker_patterns) {
            if (source_json.indexOf(marker) != -1) {
                markers.push(marker);
            }
        }

        return markers;
    }

    /**
     * 获取动态正文中开奖时间附近的文本
     * @param {object} data 动态信息
     * @returns 返回开奖时间附近的文本片段
     */
    function getLotteryDateContexts(data) {
        let source = getOriginalDynamicSource(data);
        if (source == null) {
            return [];
        }

        let source_text = JSON.stringify(source),
            keywords = ['开奖时间', '开奖日期', '开奖'],
            contexts = [];

        for (let keyword of keywords) {
            let search_start = 0,
                keyword_index = source_text.indexOf(keyword, search_start);

            while (keyword_index != -1 && contexts.length < 10) {
                let context_start = Math.max(0, keyword_index - 30),
                    context_end = Math.min(source_text.length, keyword_index + 140);
                contexts.push(source_text.substring(context_start, context_end));
                search_start = keyword_index + keyword.length;
                keyword_index = source_text.indexOf(keyword, search_start);
            }
        }

        return contexts;
    }

    /**
     * 获取动态正文中明确写出的开奖时间
     * @param {object} data 动态信息
     * @returns 返回 Unix 时间戳 (秒)，没有明确时间时返回 null
     */
    function getLotteryEndTimestampFromDynamic(data) {
        let source = getOriginalDynamicSource(data),
            pub_timestamp = source
                && source.modules
                && source.modules.module_author
                ? Number(source.modules.module_author.pub_ts)
                : NaN,
            source_year = Number.isFinite(pub_timestamp)
                ? new Date(pub_timestamp * 1000).getFullYear()
                : new Date().getFullYear(),
            contexts = getLotteryDateContexts(data),
            date_patterns = [
                /(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)\s+(\d{1,2})\s*[:：]\s*(\d{2})/,
                /(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)(?:\s*(\d{1,2})\s*(?:时|点)(?:\s*(\d{1,2})\s*分?)?)?/,
                /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s*[T ]\s*(\d{1,2}):(\d{2}))?/,
                /(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)\s+(\d{1,2})\s*[:：]\s*(\d{2})/,
                /(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)(?:\s*(\d{1,2})\s*(?:时|点)(?:\s*(\d{1,2})\s*分?)?)?/
            ];

        for (let context of contexts) {
            for (let pattern of date_patterns) {
                let match = context.match(pattern);
                if (match == null) {
                    continue;
                }

                let has_year = /^\d{4}$/.test(match[1]),
                    year = has_year ? Number(match[1]) : source_year,
                    month = has_year ? Number(match[2]) : Number(match[1]),
                    day = has_year ? Number(match[3]) : Number(match[2]),
                    hour = has_year ? Number(match[4] || 0) : Number(match[3] || 0),
                    minute = has_year ? Number(match[5] || 0) : Number(match[4] || 0);

                if (!Number.isInteger(year)
                    || !Number.isInteger(month)
                    || !Number.isInteger(day)
                    || month < 1
                    || month > 12
                    || day < 1
                    || day > 31
                    || hour < 0
                    || hour > 23
                    || minute < 0
                    || minute > 59) {
                    continue;
                }

                let timestamp = Math.floor(
                    Date.UTC(year, month - 1, day, hour, minute) / 1000
                ) - 8 * 60 * 60;
                if (timestamp > 0) {
                    return timestamp;
                }
            }
        }

        return null;
    }

    /**
     * 获取原动态详情，用于补充动态分页摘要中缺失的正文
     * @param {string} dynamic_id 原动态 ID
     * @returns 返回详情动态对象，获取失败时返回 null
     */
    async function getDynamicDetail(dynamic_id) {
        let detail_url = 'https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id='
                + encodeURIComponent(dynamic_id)
                + '&timezone_offset=-480'
                + '&platform=web'
                + '&gaia_source=main_web'
                + '&web_location=333.1387'
                + '&features=itemOpusStyle,opusBigCover,onlyfansVote,endFooterHidden,'
                + 'decorationCard,onlyfansAssetsV2,ugcDelete,onlyfansQaCard,commentsNewVersion';

        if (Date.now() < detail_blocked_until) {
            debugWarn('详情请求因冷却期跳过。', {
                dynamic_id: dynamic_id,
                blocked_until: new Date(detail_blocked_until).toISOString()
            });
            return null;
        }

        let delay = await randomDelay(2500, 4500);
        debugLog('开始请求原动态详情。', {
            dynamic_id: dynamic_id,
            delay_ms: delay
        });

        try {
            let response = await axios({
                url: detail_url,
                withCredentials: true
            });

            debugLog('原动态详情接口返回。', {
                dynamic_id: dynamic_id,
                code: response.data && response.data.code,
                message: response.data && response.data.message
            });

            if (String(response.data && response.data.code) == '-352') {
                blockDetailRequests('接口返回 -352');
            }

            if (String(response.data && response.data.code) == '0') {
                let detail_item = response.data.data
                    && (response.data.data.item || response.data.data);
                if (detail_item != null && typeof detail_item == 'object') {
                    debugLog('原动态详情加载成功。', {
                        dynamic_id: dynamic_id,
                        has_desc: detail_item.desc != null,
                        has_modules: detail_item.modules != null,
                        keys: Object.keys(detail_item)
                    });
                    return detail_item;
                }
            }
        } catch (error) {
            debugError('原动态详情请求失败。', {
                dynamic_id: dynamic_id,
                error: formatAxiosError(error)
            });
            if (error && error.response && error.response.status == 412) {
                blockDetailRequests('HTTP 412');
            }
        }

        debugWarn('原动态详情不可用，继续使用动态分页摘要。', {
            dynamic_id: dynamic_id
        });
        return null;
    }

    /**
     * 获取互动抽奖状态
     * @param {string} url 互动抽奖 API
     * @returns 返回状态；无互动抽奖时为 null，请求失败时为 undefined
     */
    async function getLotteryInfo(url) {
        try {
            let delay = await randomDelay(800, 1500);
            let response = await axios({
                url: url,
                withCredentials: true
            });

            if (String(response.data.code) == '0') {
                let lottery_data = response.data.data || {};
                let lottery_info = {
                    status: lottery_data.status == null
                        ? null
                        : String(lottery_data.status),
                    end_timestamp: getLotteryEndTimestamp(lottery_data)
                };
                debugLog('抽奖接口查询成功。', {
                    url: url,
                    delay_ms: delay,
                    response: lottery_info,
                    raw_data: lottery_data
                });
                return lottery_info;
            }
            debugLog('抽奖接口返回无抽奖信息。', {
                url: url,
                delay_ms: delay,
                response: response.data
            });
            return null;
        } catch (error) {
            debugError('抽奖接口请求失败。', {
                url: url,
                error: formatAxiosError(error)
            });
            return undefined;
        }
    }

    /**
     * 从抽奖信息中获取明确的开奖时间
     * @param {object} lottery_data 抽奖信息
     * @returns 返回 Unix 时间戳 (秒)，没有明确时间时返回 null
     */
    function getLotteryEndTimestamp(lottery_data) {
        let time_keys = [
                'lottery_time',
                'lottery_end_time',
                'end_time',
                'end_ts',
                'draw_time',
                'award_time',
                'deadline'
            ],
            pending = [lottery_data],
            visited = new Set();

        while (pending.length > 0) {
            let current = pending.shift();
            if (current == null || typeof current != 'object'
                || visited.has(current)) {
                continue;
            }
            visited.add(current);

            for (let key of time_keys) {
                if (current[key] != null) {
                    let timestamp = toTimestamp(current[key]);
                    if (timestamp != null) {
                        return timestamp;
                    }
                }
            }

            for (let key of Object.keys(current)) {
                if (current[key] != null && typeof current[key] == 'object') {
                    pending.push(current[key]);
                }
            }
        }

        return null;
    }

    /**
     * 将时间值转换为 Unix 时间戳
     * @param {number|string} value 时间值
     * @returns 返回 Unix 时间戳 (秒)，无法转换时返回 null
     */
    function toTimestamp(value) {
        if (typeof value == 'number' && Number.isFinite(value)) {
            return value > 100000000000 ? Math.floor(value / 1000) : Math.floor(value);
        }

        if (typeof value == 'string') {
            if (/^\d+$/.test(value)) {
                let numeric_value = Number(value);
                return numeric_value > 100000000000
                    ? Math.floor(numeric_value / 1000)
                    : Math.floor(numeric_value);
            }

            let parsed_time = Date.parse(value);
            if (!isNaN(parsed_time)) {
                return Math.floor(parsed_time / 1000);
            }
        }

        return null;
    }

    /**
     * 删除动态
     * @param {object} item 每条动态的信息
     */
    async function deleteDynamic(item) {
        //* csrf 参数 -> 从 cookie 获取 bili_jct
        let delete_api = 'https://api.bilibili.com/x/dynamic/feed/operate/remove?csrf=' + getCookie('bili_jct'),
            re_id_str = item.id_str; // 转发动态的 ID
        let delay = await randomDelay(1000, 1800);
        debugLog('准备删除动态。', {
            dynamic_id: re_id_str,
            has_csrf: getCookie('bili_jct') != null,
            delay_ms: delay
        });

        try {
            let response = await axios({
                method: 'post',
                url: delete_api,
                withCredentials: true,
                data: {
                    dyn_id_str: re_id_str
                }
            });

            if (String(response.data.code) == '0') {
                sendNotification(re_id_str + ' 删除成功。');
                return true;
            }
            debugError('删除动态接口返回失败。', {
                dynamic_id: re_id_str,
                response: response.data
            });
        } catch (error) {
            debugError('删除动态请求失败。', {
                dynamic_id: re_id_str,
                error: formatAxiosError(error)
            });
        }
        return false;
    }

    /**
     * 根据设置保存待取关用户
     * @param {object} data 每条动态的信息
     */
    function saveUnfollowUserIfNeeded(data) {
        if (GM_getValue('set-unfollow')
            && data.orig != null
            && data.orig.modules != null
            && data.orig.modules.module_author != null
            && data.orig.modules.module_author.following) {
            saveUnfollowUser(data);
        }
    }

    /**
     * 删除动态
     * @param {object} data 每条动态的信息
     */
    function saveUnfollowUser(data) {
        let unfollow_arr = GM_getValue('unfollow-list') || [],
            uid = data.orig.modules.module_author.mid;
        if (unfollow_arr.indexOf(uid) == -1) {
            unfollow_arr.push(uid);
            GM_setValue('unfollow-list', unfollow_arr);
        }
    }

    /**
     * 取关用户
     */
    async function unfollowUser() {
        let unfollow_api = 'https://api.bilibili.com/x/relation/modify',
            unfollow_list = GM_getValue('unfollow-list') || [];

        for (let uid of unfollow_list) {
            try {
                let delay = await randomDelay(1200, 2200);
                let csrf = getCookie('bili_jct'),
                    form_data = new URLSearchParams();
                form_data.set('fid', String(uid));
                form_data.set('act', '2');
                form_data.set('re_src', '11');
                form_data.set('spmid', '333.999.0.0');
                form_data.set('csrf', csrf || '');

                let response = await axios({
                    method: 'post',
                    url: unfollow_api + '?csrf=' + encodeURIComponent(csrf || ''),
                    withCredentials: true,
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Referer': 'https://space.bilibili.com/' + getCookie('DedeUserID') + '/'
                    }
                    ,
                    data: form_data.toString()
                });

                if (String(response.data.code) == '0') {
                    sendNotification(uid + ' 取关成功。');
                    let remaining_list = (GM_getValue('unfollow-list') || [])
                        .filter(function(item) {
                            return item != uid;
                        });
                    GM_setValue('unfollow-list', remaining_list);
                } else {
                    debugError('取关接口返回失败。', {
                        uid: uid,
                        response: response.data
                    });
                }
            } catch (error) {
                debugError('取关请求失败。', {
                    uid: uid,
                    error: formatAxiosError(error)
                });
            }
        }

    }

    /**
     * 显示通知
     * @param {string} msg 发送的通知消息
     */
    function sendNotification(msg) {
        GM_notification({
            text: msg,
            title: GM_info.script.name,
            image: GM_info.script.icon,
            timeout: 1500,
        });
    }

    /**
     * 获取 cookie
     * @param {string} key 所需的 cookie 的键
     * @returns 返回 cookie 的值
     */
    function getCookie(key) {
        let cookieArr = document.cookie.split(';');
        for (var i = 0; i < cookieArr.length; i++) {
            let cookie = cookieArr[i].trim(),
                separator_index = cookie.indexOf('=');
            if (separator_index != -1
                && cookie.substring(0, separator_index) == key) {
                return decodeURIComponent(cookie.substring(separator_index + 1));
            }
        }
    }

    /**
     * 启动
     * @param {string} mode 选择的模式
     */
    async function start(mode) {
        let duid = getCookie('DedeUserID'),
            input = '';

        if (scan_running) {
            debugWarn('已有扫描在运行，忽略重复启动。', {
                mode: mode
            });
            sendNotification('已有扫描在运行。');
            return false;
        }

        if(duid == undefined) {
            sendNotification('未检测到登录状态。'); // 未登录时 DedeUserID 未定义
        } else {
            scan_running = true;

            try {
                if (mode == 'user') {
                    input = prompt('请输入想要删除的用户名或 UID (多个则用英文逗号「,」进行分割) :');
                    if (input == '' || input == undefined) {
                        debugWarn('指定用户模式取消或未输入内容。');
                        sendNotification('没有输入内容！')
                        return false
                    }
                    debugLog('指定用户模式输入完成。', {
                        input: input
                    });
                }
                if (mode == 'days_ago') {
                    input = prompt('请输入想要删除多少天前的动态 (整数即可) :');
                    if (input == null || !/^\d+$/.test(input.trim())) {
                        debugWarn('按天数模式输入无效。', {
                            input: input
                        });
                        sendNotification('输入错误！')
                        return false;
                    }
                    input = Number(input.trim());
                    debugLog('按天数模式输入完成。', {
                        days: input,
                        threshold: getBeforeDate(input),
                        threshold_timestamp: getBeforeTimestamp(input)
                    });
                }
                if (mode == 'latest_x') {
                    input = prompt('请输入想要检测的最新动态数量 (整数即可) :');
                    if (input == null || !/^\d+$/.test(input.trim()) || Number(input.trim()) <= 0) {
                        debugWarn('最新 X 模式输入无效。', {
                            input: input
                        });
                        sendNotification('输入错误！')
                        return false;
                    }
                    input = Number(input.trim());
                }

                await getDynamics(duid, '', mode, input);
            } finally {
                scan_running = false;
            }
        }
    }

    /**
     * 删除源动态已开奖 / 已删除对应的转发动态
     */
    GM_registerMenuCommand('自动判断', () => {
        start('auto');
    })

    /**
     * 删除源动态用户名 / UID对应的转发动态
     */
    GM_registerMenuCommand('指定用户', () => {
        start('user');
    })

    /**
     * 删除X天前发布的源动态对应的转发动态
     */
    GM_registerMenuCommand('删除X天前的转发动态', () => {
        start('days_ago');
    })

    /**
     * 检测最新 X 个动态
     */
    GM_registerMenuCommand('检测最新X个动态', () => {
        start('latest_x');
    })

    /**
     * 打开设置弹窗
     */
    GM_registerMenuCommand('打开设置', () => {
        openSettingWindow();
    })
})();
