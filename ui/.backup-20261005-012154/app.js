/**
 * app.js - 主界面逻辑
 * DOMContentLoaded 后启动，所有 Python 调用走 window.call()
 */

(function () {
    'use strict';

    // ============ 工具函数 ============

    /** Toast 提示 */
    function showToast(message, type) {
        type = type || 'info';
        var t = document.createElement('div');
        t.className = 'toast ' + type;
        t.textContent = message;
        document.body.appendChild(t);
        setTimeout(function () { t.remove(); }, 2500);
    }

    /** 显示模态框 */
    function showModal(id) {
        var m = document.getElementById(id);
        if (m) m.classList.add('show');
    }

    /** 隐藏模态框 */
    function hideModal(id) {
        var m = document.getElementById(id);
        if (m) m.classList.remove('show');
    }

    /** 格式化日期 */
    function formatDate(dateStr) {
        try {
            var d = new Date(dateStr);
            return d.getFullYear() + '-' +
                   String(d.getMonth() + 1).padStart(2, '0') + '-' +
                   String(d.getDate()).padStart(2, '0');
        } catch (e) {
            return dateStr;
        }
    }

    // 暴露到全局
    window.UI = { showToast: showToast, showModal: showModal, hideModal: hideModal, formatDate: formatDate };

    // ============ 好感度环绘制 ============

    /**
     * 绘制环形进度
     * @param {HTMLCanvasElement} canvas
     * @param {number} score 0-100
     */
    function drawAffectionRing(canvas, score) {
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var w = canvas.width, h = canvas.height;
        var cx = w / 2, cy = h / 2;
        var radius = Math.min(w, h) / 2 - 4;

        ctx.clearRect(0, 0, w, h);

        // 背景环
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.strokeStyle = getCssVar('--border-color');
        ctx.lineWidth = 4;
        ctx.stroke();

        // 金色渐变进度
        var grad = ctx.createLinearGradient(0, 0, w, h);
        grad.addColorStop(0, '#f0d060');
        grad.addColorStop(1, '#b8941f');
        ctx.beginPath();
        ctx.arc(cx, cy, radius, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * score / 100));
        ctx.strokeStyle = grad;
        ctx.lineWidth = 4;
        ctx.lineCap = 'round';
        ctx.stroke();

        // 中心数字
        ctx.fillStyle = getCssVar('--gold-primary');
        ctx.font = 'bold ' + Math.floor(w / 4) + 'px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(Math.round(score), cx, cy);
    }

    function getCssVar(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    // ============ 18 维雷达图 ============

    /**
     * 绘制雷达图
     * @param {HTMLCanvasElement} canvas
     * @param {number[]} scores 18 维分数 0-100
     */
    function drawRadarChart(canvas, scores) {
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var w = canvas.width = canvas.clientWidth * 2;  // 高清
        var h = canvas.height = canvas.clientHeight * 2;
        var cx = w / 2, cy = h / 2;
        var radius = Math.min(w, h) / 2 - 60;
        var n = 18;

        ctx.clearRect(0, 0, w, h);

        // 网格 4 层
        for (var layer = 1; layer <= 4; layer++) {
            ctx.beginPath();
            for (var i = 0; i <= n; i++) {
                var angle = (Math.PI * 2 / n) * i - Math.PI / 2;
                var r = radius * layer / 4;
                var x = cx + r * Math.cos(angle);
                var y = cy + r * Math.sin(angle);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = getCssVar('--border-color');
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // 轴线
        for (var i = 0; i < n; i++) {
            var angle = (Math.PI * 2 / n) * i - Math.PI / 2;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + radius * Math.cos(angle), cy + radius * Math.sin(angle));
            ctx.strokeStyle = getCssVar('--border-color');
            ctx.stroke();
        }

        // 数据多边形
        ctx.beginPath();
        for (var i = 0; i <= n; i++) {
            var angle = (Math.PI * 2 / n) * (i % n) - Math.PI / 2;
            var s = (scores && scores[i % n] != null) ? scores[i % n] : 50;
            var r = radius * s / 100;
            var x = cx + r * Math.cos(angle);
            var y = cy + r * Math.sin(angle);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.fillStyle = 'rgba(212, 175, 55, 0.3)';
        ctx.fill();
        ctx.strokeStyle = getCssVar('--gold-primary');
        ctx.lineWidth = 2;
        ctx.stroke();
    }

    // ============ 导航 Tab 切换 ============

    function initNavTabs() {
        var items = document.querySelectorAll('.sidebar .nav-item[data-tab]');
        items.forEach(function (item) {
            item.addEventListener('click', function () {
                var tab = item.getAttribute('data-tab');
                items.forEach(function (i) { i.classList.remove('active'); });
                item.classList.add('active');

                document.querySelectorAll('.tab-pane').forEach(function (p) {
                    p.classList.remove('active');
                });
                var pane = document.getElementById('tab-' + tab);
                if (pane) pane.classList.add('active');

                // 切换到雷达图 tab 时重绘
                if (tab === 'profile') {
                    setTimeout(function () {
                        drawRadarChart(document.getElementById('radarChartCanvas'), window._radarScores);
                    }, 50);
                }
            });
        });
    }

    // ============ 设置 ============

    function initSettings() {
        var openBtn = document.getElementById('openSettingsBtn');
        var sideBtn = document.getElementById('sidebarSettingsBtn');
        var bottomBtn = document.getElementById('bottomSettingsBtn');

        [openBtn, sideBtn, bottomBtn].forEach(function (btn) {
            if (btn) btn.addEventListener('click', function () { showModal('settingsModal'); });
        });

        // 关闭按钮
        document.querySelectorAll('[data-close-modal]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                hideModal(btn.getAttribute('data-close-modal'));
            });
        });

        // 每个设置项 change → 实时保存
        document.querySelectorAll('[data-config]').forEach(function (el) {
            var key = el.getAttribute('data-config');

            if (el.classList.contains('switch-toggle')) {
                el.addEventListener('click', function () {
                    el.classList.toggle('on');
                    var val = el.classList.contains('on');
                    call('set_config_value', key, val).then(function () {
                        showToast('已保存', 'success');
                    }).catch(function (e) { console.error(e); });
                });
            } else {
                el.addEventListener('change', function () {
                    call('set_config_value', key, el.value).then(function () {
                        showToast('已保存', 'success');
                    }).catch(function (e) { console.error(e); });
                });
            }
        });

        // 保存全部
        var saveBtn = document.getElementById('saveSettingsBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', function () {
                document.querySelectorAll('[data-config]').forEach(function (el) {
                    var key = el.getAttribute('data-config');
                    var val;
                    if (el.classList.contains('switch-toggle')) {
                        val = el.classList.contains('on');
                    } else {
                        val = el.value;
                    }
                    call('set_config_value', key, val);
                });
                showToast('全部设置已保存', 'success');
            });
        }

        // 亮色主题切换实时生效
        var lightToggle = document.getElementById('lightThemeToggle');
        if (lightToggle) {
            lightToggle.addEventListener('click', function () {
                setTimeout(function () {
                    if (lightToggle.classList.contains('on')) {
                        document.body.classList.add('theme-light');
                    } else {
                        document.body.classList.remove('theme-light');
                    }
                }, 50);
            });
        }
    }

    // ============ 数据加载 ============

    function loadConfig() {
        call('get_config').then(function (cfg) {
            if (!cfg || typeof cfg !== 'object') return;
            document.querySelectorAll('[data-config]').forEach(function (el) {
                var key = el.getAttribute('data-config');
                var val = deepGet(cfg, key);
                if (val == null) return;
                if (el.classList.contains('switch-toggle')) {
                    el.classList.toggle('on', !!val);
                    if (key === 'ui.light_theme' && val) {
                        document.body.classList.add('theme-light');
                    }
                } else {
                    el.value = val;
                }
            });
        }).catch(function (e) { console.error('加载配置失败', e); });
    }

    function deepGet(obj, path) {
        return path.split('.').reduce(function (o, k) { return (o || {})[k]; }, obj);
    }

    function loadPersons() {
        call('list_persons').then(function (list) {
            if (!list || !list.length) return;
            var p = list[0];
            document.getElementById('topPersonName').textContent = p.name || '未命名';
            document.getElementById('topStageBadge').textContent = p.stage || '初识';
        }).catch(function (e) { console.error(e); });
    }

    function loadAffection() {
        call('get_affection').then(function (data) {
            var total = (data && data.total) || 50;
            drawAffectionRing(document.getElementById('topAffectionRing'), total);
            window._radarScores = (data && data.dimensions) || null;
            drawRadarChart(document.getElementById('radarChartCanvas'), window._radarScores);
        }).catch(function (e) { console.error(e); });
    }

    function loadTasks() {
        call('list_tasks').then(function (tasks) {
            var html = '';
            if (!tasks || !tasks.length) {
                html = '<p class="placeholder-text">暂无任务</p>';
            } else {
                tasks.forEach(function (t) {
                    html += '<div class="task-card">' +
                        '<div class="task-title">' + (t.title || '任务') + '</div>' +
                        '<div class="task-meta">' + (t.type || '') + ' · 进度 ' + (t.progress || 0) + '%</div>' +
                        '<div class="progress-bar"><div class="progress-fill" style="width:' + (t.progress || 0) + '%"></div></div>' +
                        '<div class="task-actions">' +
                            '<button class="btn-ghost" data-act="done" data-id="' + (t.id || '') + '">完成</button>' +
                            '<button class="btn-ghost" data-act="abandon" data-id="' + (t.id || '') + '">放弃</button>' +
                        '</div></div>';
                });
            }
            document.getElementById('taskList').innerHTML = html;
            document.getElementById('dashboardTaskList').innerHTML = html;
        }).catch(function (e) { console.error(e); });
    }

    function loadMedals() {
        call('get_medals').then(function (medals) {
            var grid = document.getElementById('medalGrid');
            if (!medals || !medals.length) {
                grid.innerHTML = '<p class="placeholder-text">暂无勋章数据</p>';
                return;
            }
            var unlockedCount = medals.filter(function (m) { return m.unlocked; }).length;
            document.getElementById('statMedals').textContent = unlockedCount + '/' + medals.length;

            grid.innerHTML = medals.map(function (m) {
                return '<div class="medal-item ' + (m.unlocked ? 'unlocked' : 'locked') + '">' +
                    '<div class="medal-icon">' + (m.icon || '🎖') + '</div>' +
                    '<div class="medal-name">' + (m.name || '') + '</div>' +
                '</div>';
            }).join('');
        }).catch(function (e) {
            // 占位：25 个空勋章
            var grid = document.getElementById('medalGrid');
            var html = '';
            for (var i = 0; i < 25; i++) {
                html += '<div class="medal-item locked"><div class="medal-icon">🎖</div><div class="medal-name">未解锁</div></div>';
            }
            grid.innerHTML = html;
            console.error(e);
        });
    }

    // ============ 聊天分析（8模块 + 弹幕流） ============

    // 模块名映射（与后端 8 模块对应）
    var ANALYSIS_MODULES = [
        { key: 'emotion',  name: '情感分析' },
        { key: 'keywords', name: '关键词提取' },
        { key: 'rag',      name: 'RAG检索' },
        { key: 'intent',   name: '意图识别' },
        { key: 'relation', name: '关系评估' },
        { key: 'reply',    name: '回复生成' },
        { key: 'safety',   name: '安全检测' },
        { key: 'affection',name: '好感度更新' },
    ];

    // 记录本次分析前的好感度（用于对比）
    var _affectionBefore = null;

    function initChatAnalysis() {
        var btn = document.getElementById('analyzeBtn');
        if (!btn) return;

        // 点击"开始分析"
        btn.addEventListener('click', function () {
            startChatAnalysis();
        });

        // 回车快捷：Ctrl+Enter 触发
        var input = document.getElementById('chatInput');
        if (input) {
            input.addEventListener('keydown', function (e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    startChatAnalysis();
                }
            });
        }

        // "填入微信"按钮
        var fillBtn = document.getElementById('fillWechatBtn');
        if (fillBtn) {
            fillBtn.addEventListener('click', function () {
                var reply = (window._lastSuggestedReply || '').trim();
                if (!reply) { showToast('暂无回复内容', 'warning'); return; }
                call('send_reply_to_wechat', reply).then(function (res) {
                    showToast('✅ 已填入微信输入框', 'success');
                    appendDanmaku('已将回复填入微信', 'success');
                }).catch(function (e) {
                    showToast('填入失败: ' + e.message, 'error');
                    appendDanmaku('填入微信失败: ' + e.message, 'error');
                });
            });
        }

        // "复制"按钮
        var copyBtn = document.getElementById('copyReplyBtn');
        if (copyBtn) {
            copyBtn.addEventListener('click', function () {
                var reply = (window._lastSuggestedReply || '').trim();
                if (!reply) { showToast('暂无回复内容', 'warning'); return; }
                // 原生复制（兼容 Qt WebEngine）
                try {
                    navigator.clipboard.writeText(reply);
                    showToast('已复制到剪贴板', 'success');
                } catch (e) {
                    // fallback
                    var ta = document.createElement('textarea');
                    ta.value = reply; document.body.appendChild(ta);
                    ta.select(); document.execCommand('copy'); ta.remove();
                    showToast('已复制', 'success');
                }
            });
        }

        // 清空弹幕
        var clearBtn = document.getElementById('clearDanmakuBtn');
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                var stream = document.getElementById('danmakuStream');
                if (stream) stream.innerHTML = '<p class="placeholder-text">弹幕已清空</p>';
            });
        }

        // 微信状态指示灯初始化
        refreshWechatStatus();
    }

    /** 刷新微信连接状态指示灯 */
    function refreshWechatStatus() {
        call('get_wechat_status').then(function (s) {
            updateWechatStatusIndicator(s && s.connected);
        }).catch(function () {
            updateWechatStatusIndicator(false);
        });
    }

    /** 更新顶部微信状态灯 */
    function updateWechatStatusIndicator(connected) {
        var dot = document.getElementById('wechatDot');
        var txt = document.getElementById('wechatStatusText');
        if (!dot || !txt) return;
        dot.className = 'wechat-dot ' + (connected ? 'online' : 'offline');
        txt.textContent = connected ? '微信已连接' : '微信未连接';
    }

    /**
     * 启动聊天分析主流程
     * 1. 取输入文本
     * 2. 显示进度条
     * 3. 启动弹幕
     * 4. 调 analyze_chat
     * 5. 按顺序点亮各模块进度
     */
    function startChatAnalysis() {
        var text = document.getElementById('chatInput').value.trim();
        if (!text) { showToast('请先粘贴对话内容', 'error'); return; }

        var personId = (window._currentPersonId || null);

        // 记录分析前好感度
        call('get_affection').then(function (data) {
            _affectionBefore = (data && data.total) || null;
        }).catch(function () { _affectionBefore = null; });

        var btn = document.getElementById('analyzeBtn');
        if (btn) { btn.disabled = true; btn.textContent = '分析中...'; }

        // 显示进度条 + 弹幕区
        var progCard = document.getElementById('analysisProgressCard');
        if (progCard) progCard.style.display = 'block';
        resetModuleProgress();
        resetAnalysisCards();
        appendDanmaku('🚀 开始分析对话，共 ' + text.length + ' 字', 'info');

        // 逐模块点亮（模拟进度，实际结果由 analysis_ready 推送）
        var moduleOrder = ['emotion', 'keywords', 'rag', 'intent', 'relation', 'reply', 'safety', 'affection'];
        moduleOrder.forEach(function (mkey, idx) {
            setTimeout(function () {
                updateAnalysisProgress(idx, 'active');
                appendDanmaku('正在执行「' + ANALYSIS_MODULES[idx].name + '」...', 'info');
            }, idx * 350);
        });

        // 调用后端
        call('analyze_chat', text, personId).then(function (result) {
            // 后台是异步的：这里只表示请求已受理
            appendDanmaku('✅ 分析请求已提交，等待大模型返回结果...', 'success');
        }).catch(function (e) {
            showToast('分析失败: ' + e.message, 'error');
            appendDanmaku('❌ 分析请求失败: ' + e.message, 'error');
            // 全部标红
            ANALYSIS_MODULES.forEach(function (_, idx) { updateAnalysisProgress(idx, 'error'); });
            if (btn) { btn.disabled = false; btn.textContent = '开始分析'; }
        });
    }

    /** 重置 8 模块进度条 */
    function resetModuleProgress() {
        var nodes = document.querySelectorAll('#moduleProgressBar .progress-module');
        nodes.forEach(function (n) {
            n.className = 'progress-module pending';
        });
    }

    /**
     * 更新某模块进度状态
     * @param {number} idx 0-7
     * @param {string} status pending | active | done | error
     */
    function updateAnalysisProgress(idx, status) {
        var nodes = document.querySelectorAll('#moduleProgressBar .progress-module');
        if (!nodes[idx]) return;
        // 保留原 data-idx，替换状态类
        nodes[idx].className = 'progress-module ' + status;
    }

    /** 重置 8 模块卡片内容为占位 */
    function resetAnalysisCards() {
        var defaults = {
            'emotionTag': '--',
            'emotionKeywords': '',
            'emotionIsQuestion': '',
            'wordCloud': '<p class="placeholder-text">分析中...</p>',
            'ragResultList': '<p class="placeholder-text">分析中...</p>',
            'intentCategory': '--',
            'intentConfidence': '',
            'relationStage': '--',
            'suggestedReply': '<p class="placeholder-text">AI 正在生成回复...</p>',
            'safetyStatus': '--',
            'safetyStats': '',
            'affectionBefore': (_affectionBefore != null ? _affectionBefore : '--'),
            'affectionAfter': '--',
            'affectionDelta': '±0',
        };
        Object.keys(defaults).forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.innerHTML = defaults[id];
        });
        var fill = document.getElementById('emotionIntensityFill');
        if (fill) fill.style.width = '0%';
        var txt = document.getElementById('emotionIntensityText');
        if (txt) txt.textContent = '0%';
    }

    /**
     * 弹幕流追加一条消息
     * @param {string} text 消息内容
     * @param {string} type info | success | warning | error
     */
    function appendDanmaku(text, type) {
        type = type || 'info';
        var stream = document.getElementById('danmakuStream');
        if (!stream) return;
        // 去掉初始占位
        var ph = stream.querySelector('.placeholder-text');
        if (ph) ph.remove();

        var line = document.createElement('div');
        line.className = 'danmaku-line dm-' + type;
        var time = new Date();
        var ts = String(time.getHours()).padStart(2, '0') + ':' +
                 String(time.getMinutes()).padStart(2, '0') + ':' +
                 String(time.getSeconds()).padStart(2, '0');
        line.innerHTML = '<span class="dm-time">[' + ts + ']</span> ' + escapeHtml(text);
        stream.appendChild(line);
        // 自动滚到底
        stream.scrollTop = stream.scrollHeight;
        // 限制最多 200 条
        while (stream.children.length > 200) stream.removeChild(stream.firstChild);
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
        });
    }

    /**
     * 渲染完整分析结果（由 analysis_ready 信号触发）
     * 数据格式：{module:"chat_analysis", data:{person_id, chat_text, length,
     *   emotion:{label,intensity,keywords,is_question},
     *   rag_context_used, llm_analysis, suggested_reply,
     *   analysis_modules:{emotion, rag, llm, safety}}}
     */
    function renderAnalysisResult(payload) {
        var d = (payload && payload.data) || payload || {};
        appendDanmaku('📥 收到分析结果', 'success');

        // 标记所有模块完成
        ANALYSIS_MODULES.forEach(function (_, idx) { updateAnalysisProgress(idx, 'done'); });

        // 1. 情感分析
        if (d.emotion) {
            renderEmotion(d.emotion);
            appendDanmaku('情感分析完成：' + d.emotion.label + ' (' + Math.round((d.emotion.intensity||0)*100) + '%)', 'success');
        }

        // 2. 关键词（从 emotion.keywords 或独立字段）
        var kws = (d.emotion && d.emotion.keywords) || d.keywords || [];
        renderKeywords(kws);
        if (kws.length) appendDanmaku('提取到 ' + kws.length + ' 个关键词', 'info');

        // 3. RAG
        renderRAGResults(d.rag_context_used || d.analysis_modules && d.analysis_modules.rag || []);

        // 4. 意图识别
        var intent = d.llm_analysis && d.llm_analysis.intent || d.intent;
        renderIntent(intent);

        // 5. 关系评估
        renderRelation(d.llm_analysis || d.relation || {});

        // 6. 回复建议
        renderSuggestedReply(d.suggested_reply || (d.llm_analysis && d.llm_analysis.reply) || '');

        // 7. 安全检测
        renderSafety(d.analysis_modules && d.analysis_modules.safety || d.safety || {});

        // 8. 好感度变化
        var after = (d.affection_after != null) ? d.affection_after :
                    (d.affection && d.affection.total);
        renderAffectionChange(_affectionBefore, after);

        // 隐藏进度条（稍作停留）
        setTimeout(function () {
            var progCard = document.getElementById('analysisProgressCard');
            // 保持显示最近一次进度，不隐藏；如需隐藏可在此注释
        }, 800);

        // 恢复按钮
        var btn = document.getElementById('analyzeBtn');
        if (btn) { btn.disabled = false; btn.textContent = '开始分析'; }
    }

    /** 1. 渲染情感卡片 */
    function renderEmotion(em) {
        var tag = document.getElementById('emotionTag');
        if (tag) {
            tag.textContent = em.label || 'neutral';
            tag.className = 'emotion-tag et-' + (em.label || 'neutral').toLowerCase();
        }
        var intensity = Math.min(1, Math.max(0, em.intensity || 0));
        var fill = document.getElementById('emotionIntensityFill');
        if (fill) fill.style.width = Math.round(intensity * 100) + '%';
        var txt = document.getElementById('emotionIntensityText');
        if (txt) txt.textContent = Math.round(intensity * 100) + '%';

        var kwBox = document.getElementById('emotionKeywords');
        if (kwBox) {
            var kws = em.keywords || [];
            kwBox.innerHTML = kws.map(function (k) {
                return '<span class="kw-tag">' + escapeHtml(k) + '</span>';
            }).join('');
        }
        var qBox = document.getElementById('emotionIsQuestion');
        if (qBox) {
            qBox.innerHTML = em.is_question
                ? '<span class="badge type-badge">❓ 疑问句</span>'
                : '<span class="badge">陈述句</span>';
        }
    }

    /** 2. 渲染关键词词云 */
    function renderKeywords(keywords) {
        var box = document.getElementById('wordCloud');
        if (!box) return;
        if (!keywords || !keywords.length) {
            box.innerHTML = '<p class="placeholder-text">未提取到关键词</p>';
            return;
        }
        box.innerHTML = keywords.map(function (k, i) {
            // 前 3 个放大
            var size = (i < 3) ? 'wc-lg' : (i < 8 ? 'wc-md' : 'wc-sm');
            return '<span class="wc-word ' + size + '">' + escapeHtml(k) + '</span>';
        }).join('');
    }

    /** 3. 渲染 RAG 检索结果 */
    function renderRAGResults(results) {
        var box = document.getElementById('ragResultList');
        if (!box) return;
        var list = [];
        if (Array.isArray(results)) list = results;
        else if (results && results.items) list = results.items;
        else if (results && results.length) list = results;

        if (!list.length) {
            box.innerHTML = '<p class="placeholder-text">未检索到相关知识</p>';
            appendDanmaku('RAG 检索完成，未命中知识库', 'warning');
            return;
        }
        box.innerHTML = list.map(function (item) {
            var title = item.title || item.name || '知识条目';
            var summary = item.summary || item.content || item.snippet || '';
            var score = item.score != null ? ('<span class="rag-score">相似度 ' + Math.round(item.score * 100) + '%</span>') : '';
            return '<div class="rag-item">' +
                '<div class="rag-title">📄 ' + escapeHtml(title) + ' ' + score + '</div>' +
                '<div class="rag-summary">' + escapeHtml(summary) + '</div>' +
            '</div>';
        }).join('');
        appendDanmaku('RAG 检索完成，命中 ' + list.length + ' 条相关知识', 'success');
    }

    /** 4. 渲染意图识别 */
    function renderIntent(intent) {
        var box = document.getElementById('intentCategory');
        if (!box) return;
        if (!intent) { box.textContent = '未识别'; return; }
        // intent 可能是字符串或 {category, confidence}
        var cat = typeof intent === 'string' ? intent : (intent.category || '日常');
        var conf = typeof intent === 'object' ? intent.confidence : null;
        box.innerHTML = '<span class="intent-badge ib-' + escapeHtml(cat) + '">' + escapeHtml(cat) + '</span>';
        var cBox = document.getElementById('intentConfidence');
        if (cBox && conf != null) {
            cBox.innerHTML = '置信度 <b>' + Math.round(conf * 100) + '%</b>';
        }
    }

    /** 5. 渲染关系评估（阶段 + 迷你雷达图） */
    function renderRelation(llm) {
        var stageBox = document.getElementById('relationStage');
        if (stageBox) {
            var stage = llm.stage || llm.relation_stage || '初识';
            stageBox.innerHTML = '<span class="relation-stage-badge">' + escapeHtml(stage) + '</span>';
        }
        // 迷你雷达图：复用主雷达图代码
        var dims = llm.dimensions || window._radarScores;
        setTimeout(function () {
            drawRadarChart(document.getElementById('miniRadarCanvas'), dims);
        }, 50);
    }

    /** 6. 渲染回复建议 */
    function renderSuggestedReply(reply) {
        var box = document.getElementById('suggestedReply');
        if (!box) return;
        if (!reply) {
            box.innerHTML = '<p class="placeholder-text">未生成回复</p>';
            window._lastSuggestedReply = '';
            return;
        }
        box.textContent = reply;
        window._lastSuggestedReply = reply;
        appendDanmaku('✅ 已生成建议回复', 'success');
    }

    /** 7. 渲染安全检测 */
    function renderSafety(safety) {
        var box = document.getElementById('safetyStatus');
        if (!box) return;
        var triggered = safety && (safety.triggered || safety.break_fake);
        if (triggered) {
            box.innerHTML = '<span class="safety-badge safety-danger">⚠ 触发安全破假</span>';
            appendDanmaku('⚠️ 安全检测：触发敏感词/破假机制', 'warning');
        } else {
            box.innerHTML = '<span class="safety-badge safety-ok">✅ 通过安全检测</span>';
            appendDanmaku('安全检测通过，无敏感内容', 'info');
        }
        var stats = document.getElementById('safetyStats');
        if (stats) {
            var replaced = (safety && safety.replaced_count) || 0;
            var total = (safety && safety.total_scanned) || 0;
            stats.innerHTML = '扫描 <b>' + total + '</b> 条 · 替换 <b>' + replaced + '</b> 处敏感词';
        }
    }

    /** 8. 渲染好感度变化（数字滚动动画） */
    function renderAffectionChange(before, after) {
        var bBox = document.getElementById('affectionBefore');
        var aBox = document.getElementById('affectionAfter');
        var dBox = document.getElementById('affectionDelta');
        if (!bBox || !aBox || !dBox) return;

        if (before == null) before = _affectionBefore;
        if (after == null) {
            // 没拿到新值则显示当前
            aBox.textContent = '--';
            dBox.textContent = '±0';
            return;
        }

        bBox.textContent = before != null ? before : '--';
        // 数字滚动动画
        animateNumber(aBox, after);

        var delta = after - (before != null ? before : after);
        if (delta > 0) {
            dBox.textContent = '+' + delta;
            dBox.className = 'affection-delta delta-up';
        } else if (delta < 0) {
            dBox.textContent = '' + delta;
            dBox.className = 'affection-delta delta-down';
        } else {
            dBox.textContent = '±0';
            dBox.className = 'affection-delta';
        }
    }

    /** 数字滚动动画 */
    function animateNumber(el, target) {
        var start = 0;
        var duration = 600;
        var t0 = Date.now();
        function tick() {
            var p = Math.min(1, (Date.now() - t0) / duration);
            var v = Math.round(start + (target - start) * p);
            el.textContent = v;
            if (p < 1) requestAnimationFrame(tick);
            else el.textContent = target;
        }
        tick();
    }

    // ============ 信号监听（bridge:xxx 自定义事件） ============

    function initSignalListeners() {
        window.addEventListener('bridge:new_message', function (e) {
            var data = e.detail || {};
            showToast('💬 新消息: ' + (data.sender || ''), 'info');
            appendDanmaku('📩 收到新消息：' + (data.sender || '') + ' - ' + (data.content || '').slice(0, 30), 'info');
            // 顶部头像闪烁
            var av = document.getElementById('topAvatar');
            if (av) { av.classList.add('animate-pulse'); setTimeout(function(){ av.classList.remove('animate-pulse'); }, 2000); }

            // 如果开启了自动分析，自动触发
            var autoToggle = document.getElementById('autoAnalysisToggle');
            if (autoToggle && autoToggle.checked && data.content) {
                document.getElementById('chatInput').value = data.content;
                setTimeout(startChatAnalysis, 500);
            }
        });

        window.addEventListener('bridge:affection_changed', function (e) {
            var data = e.detail || {};
            if (data.total != null) {
                drawAffectionRing(document.getElementById('topAffectionRing'), data.total);
                // 同步好感度卡片"分析后"
                var aBox = document.getElementById('affectionAfter');
                if (aBox) animateNumber(aBox, data.total);
            }
            if (data.dimensions) {
                window._radarScores = data.dimensions;
                drawRadarChart(document.getElementById('radarChartCanvas'), data.dimensions);
                drawRadarChart(document.getElementById('miniRadarCanvas'), data.dimensions);
            }
        });

        window.addEventListener('bridge:task_updated', function () {
            loadTasks();
        });

        window.addEventListener('bridge:medal_unlocked', function (e) {
            var m = e.detail || {};
            showToast('🎉 解锁新勋章: ' + (m.name || ''), 'success');
            loadMedals();
        });

        window.addEventListener('bridge:person_switched', function (e) {
            var p = e.detail || {};
            document.getElementById('topPersonName').textContent = p.name || '未选择';
            document.getElementById('topStageBadge').textContent = p.stage || '初识';
            window._currentPersonId = p.id || null;
            loadAffection();
        });

        // 分析结果就绪 → 渲染 8 模块
        window.addEventListener('bridge:analysis_ready', function (e) {
            renderAnalysisResult(e.detail);
        });

        // 微信状态变化
        window.addEventListener('bridge:wechat_status', function (e) {
            var s = e.detail || {};
            updateWechatStatusIndicator(!!s.connected);
            showToast('微信状态: ' + (s.connected ? '已连接' : '断开'));
            appendDanmaku('🔌 微信连接状态：' + (s.connected ? '已连接' : '断开'), s.connected ? 'success' : 'warning');
        });

        window.addEventListener('bridge:config_changed', function () {
            loadConfig();
        });
    }

    // ============ 右侧面板折叠 ============

    function initPanelToggle() {
        var collapseBtn = document.getElementById('collapsePanelBtn');
        var panel = document.getElementById('rightPanel');
        if (collapseBtn && panel) {
            collapseBtn.addEventListener('click', function () {
                panel.classList.toggle('collapsed');
                collapseBtn.textContent = panel.classList.contains('collapsed') ? '展开 »' : '收起 «';
            });
        }

        ['taskPanelToggle', 'memoPanelToggle'].forEach(function (id) {
            var t = document.getElementById(id);
            if (t) t.addEventListener('click', function () {
                t.parentElement.classList.toggle('collapsed');
            });
        });
    }

    // ============ 新手引导 ============

    function initOnboarding() {
        var ov = document.getElementById('onboardingOverlay');
        var btn = document.getElementById('onboardingNextBtn');
        if (ov) ov.classList.add('show');
        if (btn) btn.addEventListener('click', function () {
            if (ov) ov.classList.remove('show');
        });
    }

    // ============ 启动 ============

    document.addEventListener('DOMContentLoaded', function () {
        initNavTabs();
        initSettings();
        initChatAnalysis();
        initSignalListeners();
        initPanelToggle();
        initOnboarding();

        // bridgeReady 后加载所有数据
        window.addEventListener('bridgeReady', function () {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
        });

        // 如果 bridge 已经 ready（mock 模式下立即触发）
        if (window.bridge && window.bridge.ready && window.bridge.ready()) {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
        }
    });

})();
