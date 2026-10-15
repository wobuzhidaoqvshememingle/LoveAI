/**
 * bridge.js - QWebChannel JS 通信封装
 * 所有 Python <-> JS 通信统一走这里
 * 导出 window.bridge 与 window.call
 */

(function () {
    'use strict';

    // 内部引用
    let channelBridge = null;   // QWebChannel 注册的 apiBridge 对象
    let isReady = false;        // 桥接是否就绪
    const readyCallbacks = [];  // bridgeReady 等待队列

    // ============ 信号默认处理函数（可被页面覆盖） ============

    /** 新消息到达 */
    function handleNewMessage(data) {
        console.log('[bridge] new_message:', data);
        // 默认：触发全局事件，由 app.js 接管
        window.dispatchEvent(new CustomEvent('bridge:new_message', { detail: data }));
    }

    /** 分析结果就绪 */
    function handleAnalysisReady(data) {
        console.log('[bridge] analysis_ready:', data);
        window.dispatchEvent(new CustomEvent('bridge:analysis_ready', { detail: data }));
    }

    /** 任务状态更新 */
    function handleTaskUpdated(data) {
        console.log('[bridge] task_updated:', data);
        window.dispatchEvent(new CustomEvent('bridge:task_updated', { detail: data }));
    }

    /** 好感度变化 */
    function handleAffectionChanged(data) {
        console.log('[bridge] affection_changed:', data);
        window.dispatchEvent(new CustomEvent('bridge:affection_changed', { detail: data }));
    }

    /** 勋章解锁 */
    function handleMedalUnlocked(data) {
        console.log('[bridge] medal_unlocked:', data);
        window.dispatchEvent(new CustomEvent('bridge:medal_unlocked', { detail: data }));
    }

    /** 人物切换 */
    function handlePersonSwitched(data) {
        console.log('[bridge] person_switched:', data);
        window.dispatchEvent(new CustomEvent('bridge:person_switched', { detail: data }));
    }

    /** 微信状态变化 */
    function handleWechatStatus(data) {
        console.log('[bridge] wechat_status:', data);
        window.dispatchEvent(new CustomEvent('bridge:wechat_status', { detail: data }));
    }

    /** 配置变化 */
    function handleConfigChanged(data) {
        console.log('[bridge] config_changed:', data);
        window.dispatchEvent(new CustomEvent('bridge:config_changed', { detail: data }));
    }

    // 暴露默认 handler，允许页面覆盖
    window.bridgeHandlers = {
        new_message: handleNewMessage,
        analysis_ready: handleAnalysisReady,
        task_updated: handleTaskUpdated,
        affection_changed: handleAffectionChanged,
        medal_unlocked: handleMedalUnlocked,
        person_switched: handlePersonSwitched,
        wechat_status: handleWechatStatus,
        config_changed: handleConfigChanged,
    };

    // ============ 信号监听注册 ============

    /**
     * 给 apiBridge 的每个信号 connect 上对应 handler
     */
    function connectSignals(bridge) {
        // 不同环境 QWebChannel 对信号名的呈现可能为 snake_case 或 camelCase，
        // 两种都尝试，连到第一个存在的信号对象。
        const signalMap = {
            new_message: ['new_message', 'newMessage'],
            analysis_ready: ['analysis_ready', 'analysisReady'],
            task_updated: ['task_updated', 'taskUpdated'],
            affection_changed: ['affection_changed', 'affectionChanged'],
            medal_unlocked: ['medal_unlocked', 'medalUnlocked'],
            person_switched: ['person_switched', 'personSwitched'],
            wechat_status: ['wechat_status', 'wechatStatus'],
            config_changed: ['config_changed', 'configChanged'],
        };

        Object.keys(signalMap).forEach(function (key) {
            const handler = window.bridgeHandlers[key];
            let signal = null;
            signalMap[key].forEach(function (nm) {
                if (!signal && bridge[nm] && typeof bridge[nm].connect === 'function') {
                    signal = bridge[nm];
                }
            });
            if (signal) {
                try {
                    signal.connect(function (payload) {
                        // Qt 传来的可能是 JSON 字符串，统一解析
                        let data = payload;
                        if (typeof payload === 'string') {
                            try { data = JSON.parse(payload); } catch (e) { /* 保持字符串 */ }
                        }
                        handler(data);
                    });
                } catch (e) {
                    console.error('[bridge] 信号连接失败:', key, e);
                }
            } else {
                console.warn('[bridge] 未找到信号:', key);
            }
        });
    }

    // ============ initBridge ============

    /**
     * 初始化 QWebChannel 连接
     * 完成后触发 window 上的 'bridgeReady' 事件
     */
    function initBridge() {
        // 浏览器直接打开（无 Qt 环境）时，提供一个 mock，避免后续调用报错
        if (typeof QWebChannel === 'undefined' || !window.qt || !window.qt.webChannelTransport) {
            console.warn('[bridge] 未检测到 QWebChannel / qt.webChannelTransport，使用 Mock 模式');
            channelBridge = createMockBridge();
            isReady = true;
            flushReady();
            return;
        }

        try {
            new QWebChannel(window.qt.webChannelTransport, function (channel) {
                channelBridge = channel.objects.apiBridge;
                if (!channelBridge) {
                    console.error('[bridge] apiBridge 对象未注册');
                    return;
                }
                connectSignals(channelBridge);
                isReady = true;
                flushReady();
            });
        } catch (e) {
            console.error('[bridge] 初始化失败:', e);
        }
    }

    /** 触发所有等待 bridgeReady 的回调 */
    function flushReady() {
        readyCallbacks.forEach(function (cb) {
            try { cb(channelBridge); } catch (e) { console.error(e); }
        });
        readyCallbacks.length = 0;
        window.dispatchEvent(new CustomEvent('bridgeReady', { detail: channelBridge }));
    }

    // ============ call(method, ...args) ============

    /**
     * 归一化 Python 端返回值。
     * Python @Slot 统一返回 JSON 字符串信封 {"ok":true,"data":...} / {"ok":false,"error":...}。
     * 这里统一 JSON.parse 并拆信封，让调用方直接拿到 data（数组/对象/值）。
     * 非 JSON 或非信封的返回（如纯文本回复）原样返回。
     * 失败信封包装为 {__reject: Error} 由 call() 转为 reject。
     */
    function _normalize(val) {
        if (typeof val === 'string') {
            var t = val.trim();
            if (t.charAt(0) === '{' || t.charAt(0) === '[') {
                try {
                    var p = JSON.parse(t);
                    if (p && typeof p === 'object' && ('ok' in p)) {
                        if (p.ok === false) {
                            return { __reject: new Error(p.error || '调用失败') };
                        }
                        return p.data;
                    }
                    return p;
                } catch (e) { /* 解析失败，原样返回 */ }
            }
        }
        return val;
    }

    /**
     * 异步调用 Python 端方法
     * @param {string} method 方法名
     * @param {...*} args 参数
     * @returns {Promise}
     */
    function call(method) {
        var args = Array.prototype.slice.call(arguments, 1);

        // 未就绪时先排队
        if (!isReady || !channelBridge) {
            return new Promise(function (resolve, reject) {
                readyCallbacks.push(function () {
                    call.apply(null, [method].concat(args)).then(resolve, reject);
                });
                // 30 秒未就绪直接拒绝
                setTimeout(function () {
                    reject(new Error('bridge 初始化超时'));
                }, 30000);
            });
        }

        var fn = channelBridge[method];
        if (typeof fn !== 'function') {
            return Promise.reject(new Error('Python 端不存在方法: ' + method));
        }

        // 30 秒超时
        var timeoutMs = 30000;
        return new Promise(function (resolve, reject) {
            var timer = setTimeout(function () {
                reject(new Error('调用超时: ' + method));
            }, timeoutMs);

            try {
                var result = fn.apply(channelBridge, args);
                // PyQt 的 invokeMethod 同步返回值，若返回 Promise 则接住
                if (result && typeof result.then === 'function') {
                    result.then(function (val) {
                        clearTimeout(timer);
                        var n = _normalize(val);
                        if (n && n.__reject) { reject(n.__reject); } else { resolve(n); }
                    }, function (err) {
                        clearTimeout(timer);
                        reject(err instanceof Error ? err : new Error(String(err)));
                    });
                } else {
                    clearTimeout(timer);
                    var nr = _normalize(result);
                    if (nr && nr.__reject) { reject(nr.__reject); } else { resolve(nr); }
                }
            } catch (e) {
                clearTimeout(timer);
                reject(e);
            }
        });
    }

    // ============ Mock Bridge（浏览器调试用） ============

    function createMockBridge() {
        var mock = {};
        // 所有方法返回空 Promise
        var methods = [
            'get_config', 'set_config_value',
            'list_persons', 'get_affection',
            'list_tasks', 'complete_task', 'abandon_task',
            'analyze_chat', 'send_reply_to_wechat',
            'get_medals', 'get_heroes',
        ];
        methods.forEach(function (m) {
            mock[m] = function () {
                console.log('[mock] call ' + m, arguments);
                return Promise.resolve({ ok: true, mock: true });
            };
        });
        return mock;
    }

    // ============ 导出 ============

    window.bridge = {
        init: initBridge,
        ready: function () { return isReady; },
        raw: function () { return channelBridge; },
    };
    window.call = call;

    // 自动启动（脚本加载完即初始化）
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initBridge);
    } else {
        initBridge();
    }
})();
