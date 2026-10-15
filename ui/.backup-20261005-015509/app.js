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
                        loadPhotos();
                        loadPreferences();
                    }, 50);
                }
                // 阶段三：切换到对应Tab时加载数据
                if (tab === 'twin') { loadTwinAccuracy(); }
                if (tab === 'tasks') { loadActiveTasks(); }
                if (tab === 'medal-wall') { loadMedalStats(); loadAllMedalsWithCategory(); }
                if (tab === 'finance') { loadExpenseSummary(); }
                if (tab === 'guide') { loadGuideHistory(document.getElementById('guideHistoryFilter') ? document.getElementById('guideHistoryFilter').value : 'all'); }
                if (tab === 'counseling') { loadConsultHistory(); }
            });
        });

        // 勋章筛选事件
        var medalFilter = document.getElementById('medalFilter');
        if (medalFilter) medalFilter.addEventListener('change', function () { loadAllMedalsWithCategory(); });
        var medalCatFilter = document.getElementById('medalCategoryFilter');
        if (medalCatFilter) medalCatFilter.addEventListener('change', function () { loadAllMedalsWithCategory(); });
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

    // ============================================================
    // 阶段三：数字分身 (Twin)
    // ============================================================

    function initTwin() {
        // 预测反应
        var predictBtn = document.getElementById('twinPredictBtn');
        if (predictBtn) {
            predictBtn.addEventListener('click', function () {
                var action = document.getElementById('twinPredictInput').value.trim();
                if (!action) { showToast('请输入行为描述', 'warning'); return; }
                predictBtn.disabled = true; predictBtn.textContent = '预测中...';
                var personId = window._currentPersonId || null;
                call('predict_reaction', action, personId).then(function (res) {
                    predictBtn.disabled = false; predictBtn.textContent = '预测TA的反应';
                    renderTwinPredict(res);
                }).catch(function (e) {
                    predictBtn.disabled = false; predictBtn.textContent = '预测TA的反应';
                    showToast('预测失败: ' + e.message, 'error');
                });
            });
        }

        // 模拟回复
        var simBtn = document.getElementById('twinSimulateBtn');
        if (simBtn) {
            simBtn.addEventListener('click', function () {
                var ctx = document.getElementById('twinSimulateInput').value.trim();
                if (!ctx) { showToast('请输入对话上下文', 'warning'); return; }
                simBtn.disabled = true; simBtn.textContent = '模拟中...';
                var personId = window._currentPersonId || null;
                call('simulate_reply', ctx, personId).then(function (res) {
                    simBtn.disabled = false; simBtn.textContent = '模拟TA会怎么回';
                    var box = document.getElementById('twinSimulateResult');
                    box.style.display = 'block';
                    document.getElementById('twinSimulateReply').textContent = (res && res.reply) || (res && res.simulated_reply) || JSON.stringify(res);
                }).catch(function (e) {
                    simBtn.disabled = false; simBtn.textContent = '模拟TA会怎么回';
                    showToast('模拟失败: ' + e.message, 'error');
                });
            });
        }

        // A/B 对比
        var cmpBtn = document.getElementById('twinCompareBtn');
        if (cmpBtn) {
            cmpBtn.addEventListener('click', function () {
                var a = document.getElementById('twinOptionA').value.trim();
                var b = document.getElementById('twinOptionB').value.trim();
                if (!a || !b) { showToast('请输入两个方案', 'warning'); return; }
                cmpBtn.disabled = true; cmpBtn.textContent = '分析中...';
                var personId = window._currentPersonId || null;
                call('compare_options', a, b, personId).then(function (res) {
                    cmpBtn.disabled = false; cmpBtn.textContent = '对比分析';
                    var box = document.getElementById('twinCompareResult');
                    box.style.display = 'block';
                    document.getElementById('twinCompareWinner').textContent = (res && res.winner) || (res && res.recommended) || '请查看分析';
                    document.getElementById('twinCompareAnalysis').textContent = (res && res.analysis) || (res && res.reasoning) || JSON.stringify(res);
                }).catch(function (e) {
                    cmpBtn.disabled = false; cmpBtn.textContent = '对比分析';
                    showToast('对比失败: ' + e.message, 'error');
                });
            });
        }

        // 准确率刷新
        var accBtn = document.getElementById('refreshTwinAccuracyBtn');
        if (accBtn) accBtn.addEventListener('click', loadTwinAccuracy);

        // 反馈按钮
        ['twinFbAccurate', 'twinFbPartial', 'twinFbWrong'].forEach(function (id) {
            var btn = document.getElementById(id);
            if (btn) {
                btn.addEventListener('click', function () {
                    var score = parseInt(btn.getAttribute('data-score'));
                    var predId = window._lastPredictionId || null;
                    var personId = window._currentPersonId || null;
                    call('record_twin_feedback', predId, score, '', personId).then(function () {
                        showToast('感谢反馈！', 'success');
                        loadTwinAccuracy();
                    }).catch(function (e) { showToast('反馈失败: ' + e.message, 'error'); });
                });
            }
        });
    }

    function renderTwinPredict(res) {
        var box = document.getElementById('twinPredictResult');
        box.style.display = 'block';
        window._lastPredictionId = (res && res.prediction_id) || (res && res.id) || null;
        document.getElementById('twinPredictEmotion').textContent = (res && res.emotion) || (res && res.predicted_emotion) || '未知';
        var prob = (res && res.probability) || (res && res.confidence);
        document.getElementById('twinPredictProb').textContent = prob != null ? (Math.round(prob * 100) + '%') : '--';
        document.getElementById('twinPredictAdvice').textContent = (res && res.advice) || (res && res.suggestion) || '暂无建议';
    }

    function loadTwinAccuracy() {
        var personId = window._currentPersonId || null;
        call('get_twin_accuracy', personId).then(function (res) {
            if (!res) return;
            var overall = (res.accuracy != null) ? Math.round(res.accuracy * 100) + '%' : '--';
            document.getElementById('twinAccuracyOverall').textContent = overall;
            document.getElementById('twinAccuracyTotal').textContent = (res.total_predictions != null) ? res.total_predictions : '--';
            document.getElementById('twinAccuracyCorrect').textContent = (res.correct_count != null) ? res.correct_count : '--';
        }).catch(function (e) { console.error('加载分身准确率失败', e); });
    }

    // ============================================================
    // 阶段三：任务引擎 (Tasks)
    // ============================================================

    function initTasks() {
        // 生成主线任务
        var genBtn = document.getElementById('generateMainTasksBtn');
        if (genBtn) {
            genBtn.addEventListener('click', function () {
                var stage = parseInt(document.getElementById('taskStageSelect').value);
                var personId = window._currentPersonId || null;
                genBtn.disabled = true; genBtn.textContent = '生成中...';
                call('generate_main_tasks', personId, stage).then(function (res) {
                    genBtn.disabled = false; genBtn.textContent = '🎯 生成主线任务';
                    showToast('主线任务已生成', 'success');
                    loadActiveTasks();
                }).catch(function (e) {
                    genBtn.disabled = false; genBtn.textContent = '🎯 生成主线任务';
                    showToast('生成失败: ' + e.message, 'error');
                });
            });
        }

        // 刷新任务
        var refreshBtn = document.getElementById('refreshTasksBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', loadActiveTasks);

        // 任务详情（事件委托）
        document.getElementById('activeTaskList').addEventListener('click', function (e) {
            var card = e.target.closest('.task-card-detail');
            if (!card) return;
            var taskId = card.getAttribute('data-task-id');
            if (!taskId) return;
            call('get_task_progress', taskId).then(function (res) {
                showTaskProgressDetail(taskId, res);
            }).catch(function (err) { showToast('加载任务详情失败', 'error'); });
        });
    }

    function loadActiveTasks() {
        var personId = window._currentPersonId || null;
        call('get_active_tasks', personId).then(function (tasks) {
            var box = document.getElementById('activeTaskList');
            if (!tasks || !tasks.length) {
                box.innerHTML = '<p class="placeholder-text">暂无活动任务</p>';
                return;
            }
            box.innerHTML = tasks.map(function (t) {
                var progress = t.progress || 0;
                var deadline = t.deadline || t.due_date || '';
                return '<div class="task-card-detail" data-task-id="' + (t.id || '') + '">' +
                    '<div class="task-title">' + escapeHtml(t.title || '任务') + '</div>' +
                    '<div class="task-meta">' + escapeHtml(t.stage || '') + ' · ' + escapeHtml(t.direction || t.type || '') + '</div>' +
                    '<div class="progress-bar"><div class="progress-fill" style="width:' + progress + '%"></div></div>' +
                    '<div class="task-meta">进度 ' + progress + '%' + (deadline ? ' · 截止: ' + formatDate(deadline) : '') + '</div>' +
                '</div>';
            }).join('');
        }).catch(function (e) {
            document.getElementById('activeTaskList').innerHTML = '<p class="placeholder-text">加载失败</p>';
            console.error(e);
        });
    }

    function showTaskProgressDetail(taskId, detail) {
        // 简易弹窗显示分步进度
        var steps = (detail && detail.steps) || [];
        var html = '<div style="max-height:300px;overflow-y:auto;">';
        if (steps.length) {
            steps.forEach(function (s, i) {
                var done = s.done || s.completed;
                html += '<div style="padding:8px;border-bottom:1px solid var(--border-color);">' +
                    (done ? '✅' : '⬜') + ' ' + escapeHtml(s.title || s.name || '步骤' + (i+1)) +
                    '</div>';
            });
        } else {
            html += '<p class="placeholder-text">暂无分步数据</p>';
        }
        html += '</div>';
        // 复用 toast 区域显示
        var existing = document.getElementById('taskDetailModal');
        if (existing) existing.remove();
        var modal = document.createElement('div');
        modal.className = 'modal show';
        modal.id = 'taskDetailModal';
        modal.innerHTML = '<div class="modal-content" style="width:480px;">' +
            '<div class="modal-header"><h2>任务详情</h2><button class="icon-btn" id="closeTaskDetailBtn">✕</button></div>' +
            html + '</div>';
        document.body.appendChild(modal);
        document.getElementById('closeTaskDetailBtn').addEventListener('click', function () { modal.remove(); });
    }

    // ============================================================
    // 阶段三：攻略引擎 (Guide)
    // ============================================================

    var _guideMode = 'self';

    function initGuide() {
        // 模式切换
        document.querySelectorAll('[data-guide-mode]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('[data-guide-mode]').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                _guideMode = btn.getAttribute('data-guide-mode');
            });
        });

        // 生成攻略
        var genBtn = document.getElementById('generateGuideBtn');
        if (genBtn) {
            genBtn.addEventListener('click', function () {
                var q = document.getElementById('guideQuestionInput').value.trim();
                if (!q) { showToast('请输入问题', 'warning'); return; }
                genBtn.disabled = true; genBtn.textContent = '生成中...';
                var personId = window._currentPersonId || null;
                call('generate_guide', q, _guideMode, personId).then(function (res) {
                    genBtn.disabled = false; genBtn.textContent = '📖 生成攻略';
                    renderGuideResult(res);
                }).catch(function (e) {
                    genBtn.disabled = false; genBtn.textContent = '📖 生成攻略';
                    showToast('生成失败: ' + e.message, 'error');
                });
            });
        }

        // 搜索攻略
        var searchBtn = document.getElementById('guideSearchBtn');
        if (searchBtn) {
            searchBtn.addEventListener('click', function () {
                var kw = document.getElementById('guideSearchInput').value.trim();
                if (!kw) { showToast('请输入关键词', 'warning'); return; }
                call('search_guides', kw, 10).then(function (res) {
                    renderGuideSearchResults(res);
                }).catch(function (e) { showToast('搜索失败: ' + e.message, 'error'); });
            });
        }

        // 攻略历史筛选
        var histFilter = document.getElementById('guideHistoryFilter');
        if (histFilter) histFilter.addEventListener('change', function () { loadGuideHistory(histFilter.value); });

        loadGuideHistory('all');
    }

    function renderGuideResult(res) {
        var box = document.getElementById('guideResult');
        box.style.display = 'block';
        var d = res || {};
        document.getElementById('guideAnalysis').textContent = d.analysis || d.analysis_text || '--';
        document.getElementById('guideStrategy').textContent = d.strategy || d.strategy_text || '--';
        document.getElementById('guidePlan').textContent = d.plan || d.action_plan || '--';
        document.getElementById('guideRisk').textContent = d.risk || d.risks || '--';
        document.getElementById('guideExpectation').textContent = d.expectation || d.expected_outcome || '--';
        window._currentGuideId = d.guide_id || d.id || null;
        loadGuideHistory('all');
    }

    function renderGuideSearchResults(res) {
        var box = document.getElementById('guideSearchResults');
        var list = [];
        if (Array.isArray(res)) list = res;
        else if (res && res.results) list = res.results;
        else if (res && res.items) list = res.items;

        if (!list.length) {
            box.innerHTML = '<p class="placeholder-text">未找到相关攻略</p>';
            return;
        }
        box.innerHTML = list.map(function (g, i) {
            return '<div class="guide-item" data-guide-id="' + (g.id || '') + '">' +
                '<div class="guide-title">' + escapeHtml(g.title || g.question || '攻略#' + (i+1)) + '</div>' +
                '<div class="guide-snippet">' + escapeHtml((g.summary || g.excerpt || '').slice(0, 80)) + '</div>' +
            '</div>';
        }).join('');
    }

    function loadGuideHistory(mode) {
        call('list_guides', mode || 'all', 20).then(function (guides) {
            var box = document.getElementById('guideHistoryList');
            if (!guides || !guides.length) {
                box.innerHTML = '<p class="placeholder-text">暂无历史攻略</p>';
                return;
            }
            box.innerHTML = guides.map(function (g) {
                return '<div class="guide-item" data-guide-id="' + (g.id || '') + '">' +
                    '<div class="guide-title">' + escapeHtml(g.title || g.question || '未命名攻略') + '</div>' +
                    '<div class="guide-meta">' + escapeHtml(g.mode || '') + ' · ' + formatDate(g.created_at || g.date || '') + '</div>' +
                '</div>';
            }).join('');
        }).catch(function (e) { console.error('加载攻略历史失败', e); });
    }

    // ============================================================
    // 阶段三：财务分析 (Finance)
    // ============================================================

    function initFinance() {
        // 生成报告
        var genBtn = document.getElementById('generateFinanceReportBtn');
        if (genBtn) {
            genBtn.addEventListener('click', function () {
                var personId = window._currentPersonId || null;
                genBtn.disabled = true; genBtn.textContent = '生成中...';
                call('generate_financial_report', personId).then(function (res) {
                    genBtn.disabled = false; genBtn.textContent = '生成报告';
                    renderFinanceReport(res);
                }).catch(function (e) {
                    genBtn.disabled = false; genBtn.textContent = '生成报告';
                    showToast('生成失败: ' + e.message, 'error');
                });
            });
        }

        // 需求可行性检查
        var checkBtn = document.getElementById('checkDemandBtn');
        if (checkBtn) {
            checkBtn.addEventListener('click', function () {
                var name = document.getElementById('demandItemName').value.trim();
                var value = parseFloat(document.getElementById('demandItemValue').value);
                if (!name || !value || value <= 0) { showToast('请填写物品名称和价值', 'warning'); return; }
                var personId = window._currentPersonId || null;
                checkBtn.disabled = true; checkBtn.textContent = '检查中...';
                call('check_demand_feasibility', personId, value, name).then(function (res) {
                    checkBtn.disabled = false; checkBtn.textContent = '🔍 检查可行性';
                    var box = document.getElementById('demandResult');
                    box.style.display = 'block';
                    var risk = (res && res.risk_level) || (res && res.risk) || '未知';
                    var riskEl = document.getElementById('demandRiskLevel');
                    riskEl.textContent = risk;
                    riskEl.className = 'result-value risk-' + String(risk).toLowerCase();
                    document.getElementById('demandAdvice').textContent = (res && res.advice) || (res && res.suggestion) || '暂无建议';
                }).catch(function (e) {
                    checkBtn.disabled = false; checkBtn.textContent = '🔍 检查可行性';
                    showToast('检查失败: ' + e.message, 'error');
                });
            });
        }

        // 记录支出
        var recBtn = document.getElementById('recordExpenseBtn');
        if (recBtn) {
            recBtn.addEventListener('click', function () {
                var amount = parseFloat(document.getElementById('expenseAmount').value);
                var desc = document.getElementById('expenseDesc').value.trim();
                var dir = document.getElementById('expenseDirection').value;
                if (!amount || amount <= 0) { showToast('请输入有效金额', 'warning'); return; }
                if (!desc) { showToast('请输入描述', 'warning'); return; }
                var personId = window._currentPersonId || null;
                call('record_expense', personId, amount, desc, dir).then(function () {
                    showToast('支出已记录', 'success');
                    document.getElementById('expenseAmount').value = '';
                    document.getElementById('expenseDesc').value = '';
                    loadExpenseSummary();
                }).catch(function (e) { showToast('记录失败: ' + e.message, 'error'); });
            });
        }

        // 刷新汇总
        var refreshBtn = document.getElementById('refreshExpenseBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', loadExpenseSummary);
    }

    function renderFinanceReport(res) {
        var box = document.getElementById('financeReportContent');
        if (!res) { box.innerHTML = '<p class="placeholder-text">无数据</p>'; return; }
        var html = '<div class="finance-report-grid">';
        html += '<div class="finance-block"><div class="finance-label">收入估算</div><div class="finance-value">' + escapeHtml(String(res.income_estimate || res.income || '--')) + '</div></div>';
        html += '<div class="finance-block"><div class="finance-label">消费模式</div><div class="finance-value">' + escapeHtml(String(res.spending_pattern || res.consumption || '--')) + '</div></div>';
        html += '<div class="finance-block"><div class="finance-label">压力等级</div><div class="finance-value">' + escapeHtml(String(res.pressure_level || res.stress || '--')) + '</div></div>';
        html += '</div>';
        if (res.risk_bottom_lines || res.four_levels) {
            var levels = res.risk_bottom_lines || res.four_levels;
            html += '<div class="finance-block" style="margin-top:12px;"><div class="finance-label">四档风险底线</div>';
            html += '<div class="risk-lines">';
            if (typeof levels === 'object') {
                Object.keys(levels).forEach(function (k) {
                    html += '<div class="risk-line"><span class="risk-name">' + escapeHtml(k) + '</span><span class="risk-val">' + escapeHtml(String(levels[k])) + '</span></div>';
                });
            }
            html += '</div></div>';
        }
        box.innerHTML = html;
    }

    function loadExpenseSummary() {
        var personId = window._currentPersonId || null;
        call('get_expense_summary', personId, 30).then(function (res) {
            if (!res) return;
            document.getElementById('expenseTotalAmount').textContent = '¥' + (res.total_amount || 0);
            document.getElementById('expenseCount').textContent = (res.count || 0) + ' 笔';
            var avg = res.count > 0 ? (res.total_amount / res.count).toFixed(1) : 0;
            document.getElementById('expenseAvg').textContent = '¥' + avg;
            // 趋势占位
            var trendBox = document.getElementById('expenseTrend');
            var items = res.trend || res.daily || [];
            if (items && items.length) {
                trendBox.innerHTML = items.map(function (d) {
                    return '<div class="trend-bar-item"><div class="trend-bar" style="height:' + Math.min(100, (d.amount || 0)) + '%"></div><div class="trend-date">' + formatDate(d.date || d.day || '') + '</div></div>';
                }).join('');
            } else {
                trendBox.innerHTML = '<p class="placeholder-text">暂无趋势数据</p>';
            }
        }).catch(function (e) { console.error('加载支出汇总失败', e); });
    }

    // ============================================================
    // 阶段三：勋章统计增强
    // ============================================================

    function loadMedalStats() {
        var personId = window._currentPersonId || null;
        call('get_medal_stats', personId).then(function (res) {
            if (!res) return;
            document.getElementById('medalTotalCount').textContent = res.total || '--';
            document.getElementById('medalUnlockedCount').textContent = res.unlocked || 0;
            document.getElementById('medalPoints').textContent = res.points || 0;
            var rate = res.total > 0 ? Math.round((res.unlocked / res.total) * 100) : 0;
            document.getElementById('medalCompletionRate').textContent = rate + '%';
            // 绘制环形图
            drawMedalRing(document.getElementById('medalStatsRing'), rate);
        }).catch(function (e) { console.error('加载勋章统计失败', e); });
    }

    function drawMedalRing(canvas, percent) {
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var w = canvas.width, h = canvas.height;
        var cx = w / 2, cy = h / 2;
        var radius = Math.min(w, h) / 2 - 8;
        ctx.clearRect(0, 0, w, h);
        // 背景环
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.strokeStyle = getCssVar('--border-color');
        ctx.lineWidth = 8;
        ctx.stroke();
        // 进度环
        var grad = ctx.createLinearGradient(0, 0, w, h);
        grad.addColorStop(0, '#f0d060');
        grad.addColorStop(1, '#b8941f');
        ctx.beginPath();
        ctx.arc(cx, cy, radius, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * percent / 100));
        ctx.strokeStyle = grad;
        ctx.lineWidth = 8;
        ctx.lineCap = 'round';
        ctx.stroke();
        // 中心文字
        ctx.fillStyle = getCssVar('--gold-primary');
        ctx.font = 'bold 20px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(percent + '%', cx, cy);
    }

    function loadAllMedalsWithCategory() {
        var personId = window._currentPersonId || null;
        call('get_all_medals', personId).then(function (medals) {
            var grid = document.getElementById('medalGrid');
            if (!medals || !medals.length) {
                grid.innerHTML = '<p class="placeholder-text">暂无勋章数据</p>';
                return;
            }
            renderMedalGrid(medals);
        }).catch(function (e) {
            console.error('加载全部勋章失败', e);
        });
    }

    function renderMedalGrid(medals) {
        var grid = document.getElementById('medalGrid');
        var filter = document.getElementById('medalFilter').value;
        var catFilter = document.getElementById('medalCategoryFilter').value;
        var filtered = medals.filter(function (m) {
            if (filter === 'unlocked' && !m.unlocked) return false;
            if (filter === 'locked' && m.unlocked) return false;
            if (catFilter !== 'all' && m.category !== catFilter) return false;
            return true;
        });
        if (!filtered.length) {
            grid.innerHTML = '<p class="placeholder-text">没有符合条件的勋章</p>';
            return;
        }
        grid.innerHTML = filtered.map(function (m) {
            return '<div class="medal-item ' + (m.unlocked ? 'unlocked' : 'locked') + '" title="' + escapeHtml(m.description || m.name || '') + '">' +
                '<div class="medal-icon">' + (m.icon || '🎖') + '</div>' +
                '<div class="medal-name">' + escapeHtml(m.name || '') + '</div>' +
            '</div>';
        }).join('');
    }

    // ============================================================
    // 阶段三：人物档案增强（照片/立绘/备忘录）
    // ============================================================

    function initProfileEnhancements() {
        // 保存基本信息
        var saveBtn = document.getElementById('saveProfileBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', function () {
                var data = {
                    name: document.getElementById('profileName').value,
                    gender: document.getElementById('profileGender').value,
                    age: parseInt(document.getElementById('profileAge').value) || null,
                    occupation: document.getElementById('profileOccupation').value,
                    note: document.getElementById('profileNote').value,
                    mbti: document.getElementById('psychMbti').value,
                    big_five: document.getElementById('psychBigFive').value,
                    attachment: document.getElementById('psychAttachment').value,
                    comm_style: document.getElementById('psychCommStyle').value,
                    soul_text: document.getElementById('soulEditor').value,
                };
                var personId = window._currentPersonId || null;
                call('update_person_profile', personId, data).then(function () {
                    showToast('档案已保存', 'success');
                }).catch(function (e) { showToast('保存失败: ' + e.message, 'error'); });
            });
        }

        // 上传照片
        var uploadBtn = document.getElementById('uploadPhotoBtn');
        if (uploadBtn) {
            uploadBtn.addEventListener('click', function () {
                // 创建隐藏 file input
                var fi = document.createElement('input');
                fi.type = 'file';
                fi.accept = 'image/*';
                fi.onchange = function () {
                    if (!fi.files || !fi.files[0]) return;
                    var file = fi.files[0];
                    // 先读取为 base64，再调后端保存
                    var reader = new FileReader();
                    reader.onload = function (e) {
                        var dataUrl = e.target.result;
                        var personId = window._currentPersonId || null;
                        call('upload_photo', personId, dataUrl, 'gallery').then(function () {
                            showToast('照片已上传', 'success');
                            loadPhotos();
                        }).catch(function (err) { showToast('上传失败: ' + err.message, 'error'); });
                    };
                    reader.readAsDataURL(file);
                };
                fi.click();
            });
        }

        // 立绘匹配
        var matchBtn = document.getElementById('matchPortraitBtn');
        if (matchBtn) {
            matchBtn.addEventListener('click', function () {
                var emotion = document.getElementById('portraitEmotionSelect').value;
                var personId = window._currentPersonId || null;
                matchBtn.disabled = true; matchBtn.textContent = '匹配中...';
                call('match_portrait_frame', personId, emotion).then(function (res) {
                    matchBtn.disabled = false; matchBtn.textContent = '🎭 匹配立绘';
                    var box = document.getElementById('portraitPreview');
                    if (res && res.frame_path) {
                        box.innerHTML = '<img src="' + res.frame_path + '" style="max-height:300px;border-radius:8px;border:1px solid var(--border-gold);" alt="立绘">';
                    } else {
                        box.innerHTML = '<p class="placeholder-text">匹配结果: ' + escapeHtml(JSON.stringify(res)) + '</p>';
                    }
                }).catch(function (e) {
                    matchBtn.disabled = false; matchBtn.textContent = '🎭 匹配立绘';
                    showToast('匹配失败: ' + e.message, 'error');
                });
            });
        }

        // 手动添加喜恶
        var addPrefBtn = document.getElementById('addPreferenceBtn');
        if (addPrefBtn) {
            addPrefBtn.addEventListener('click', function () {
                var text = prompt('请输入喜恶记录（例如：喜欢喝奶茶三分糖）：');
                if (!text) return;
                var personId = window._currentPersonId || null;
                call('add_preference', personId, text).then(function () {
                    showToast('已添加', 'success');
                    loadPreferences();
                }).catch(function (e) { showToast('添加失败: ' + e.message, 'error'); });
            });
        }
    }

    function loadPhotos() {
        var personId = window._currentPersonId || null;
        call('get_photos', personId, 'gallery').then(function (photos) {
            var grid = document.getElementById('photoGrid');
            if (!photos || !photos.length) {
                grid.innerHTML = '<p class="placeholder-text">暂无照片</p>';
                return;
            }
            grid.innerHTML = photos.map(function (p) {
                return '<div class="photo-item" data-photo-id="' + (p.id || '') + '">' +
                    '<img src="' + (p.path || p.url || '') + '" alt="照片">' +
                    '<div class="photo-actions">' +
                        '<button class="btn-ghost btn-set-avatar" data-id="' + (p.id || '') + '">设为头像</button>' +
                    '</div></div>';
            }).join('');
            // 绑定设为头像
            grid.querySelectorAll('.btn-set-avatar').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var pid = btn.getAttribute('data-id');
                    call('set_profile_photo', personId, pid).then(function () {
                        showToast('已设为头像', 'success');
                    }).catch(function (e) { showToast('设置失败: ' + e.message, 'error'); });
                });
            });
        }).catch(function (e) { console.error('加载照片失败', e); });
    }

    function loadPreferences() {
        var personId = window._currentPersonId || null;
        call('get_preferences', personId).then(function (prefs) {
            var box = document.getElementById('preferenceList');
            if (!prefs || !prefs.length) {
                box.innerHTML = '<p class="placeholder-text">暂无记录</p>';
                return;
            }
            box.innerHTML = prefs.map(function (p) {
                var type = p.type || 'like';
                return '<div class="pref-item pref-' + type + '">' +
                    '<span class="pref-type">' + (type === 'like' ? '👍' : '👎') + '</span>' +
                    '<span class="pref-text">' + escapeHtml(p.content || p.text || '') + '</span>' +
                '</div>';
            }).join('');
        }).catch(function (e) { console.error('加载喜恶失败', e); });
    }

    // ============================================================
    // 阶段三：咨询增强（安全提示/历史/清空）
    // ============================================================

    var _consultMode = 'self';

    function initConsultEnhancements() {
        // 模式切换
        document.querySelectorAll('#tab-counseling [data-mode]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#tab-counseling [data-mode]').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                _consultMode = btn.getAttribute('data-mode');
            });
        });

        // 发送咨询
        var sendBtn = document.getElementById('counselingSendBtn');
        if (sendBtn) {
            sendBtn.addEventListener('click', function () {
                var q = document.getElementById('counselingInput').value.trim();
                if (!q) { showToast('请输入问题', 'warning'); return; }
                // 安全检测关键词
                if (/自杀|自残|不想活|伤害自己|轻生|不想活了|结束生命/.test(q)) {
                    document.getElementById('consultSafetyBanner').style.display = 'block';
                }
                appendConsultMsg('我', q);
                document.getElementById('counselingInput').value = '';
                sendBtn.disabled = true; sendBtn.textContent = '思考中...';
                call('consult', q, _consultMode).then(function (res) {
                    sendBtn.disabled = false; sendBtn.textContent = '发送咨询';
                    var answer = (res && res.answer) || (res && res.reply) || JSON.stringify(res);
                    appendConsultMsg('AI', answer);
                    // 安全检测回答中的关键词
                    if (/自杀|自残|伤害自己|轻生/.test(answer)) {
                        document.getElementById('consultSafetyBanner').style.display = 'block';
                    }
                }).catch(function (e) {
                    sendBtn.disabled = false; sendBtn.textContent = '发送咨询';
                    appendConsultMsg('AI', '抱歉，咨询出错: ' + e.message);
                });
            });
        }

        // 清空历史
        var clearBtn = document.getElementById('clearConsultHistoryBtn');
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                if (!confirm('确定清空所有咨询历史吗？')) return;
                call('clear_consult_history').then(function () {
                    document.getElementById('counselingDialog').innerHTML = '<p class="placeholder-text">历史已清空</p>';
                    showToast('咨询历史已清空', 'success');
                }).catch(function (e) { showToast('清空失败: ' + e.message, 'error'); });
            });
        }

        // 加载历史
        loadConsultHistory();
    }

    function appendConsultMsg(role, text) {
        var box = document.getElementById('counselingDialog');
        var ph = box.querySelector('.placeholder-text');
        if (ph) ph.remove();
        var line = document.createElement('div');
        line.className = 'consult-msg consult-' + (role === '我' ? 'user' : 'ai');
        line.innerHTML = '<span class="consult-role">' + role + ':</span> ' + escapeHtml(text);
        box.appendChild(line);
        box.scrollTop = box.scrollHeight;
    }

    function loadConsultHistory() {
        call('get_consult_history').then(function (history) {
            if (!history || !history.length) return;
            var box = document.getElementById('counselingDialog');
            box.innerHTML = '';
            history.forEach(function (item) {
                if (item.question) appendConsultMsg('我', item.question);
                if (item.answer) appendConsultMsg('AI', item.answer);
            });
        }).catch(function (e) { console.error('加载咨询历史失败', e); });
    }

    // ============ 启动 ============

    document.addEventListener('DOMContentLoaded', function () {
        initNavTabs();
        initSettings();
        initChatAnalysis();
        initSignalListeners();
        initPanelToggle();
        initOnboarding();
        // 阶段三初始化
        initTwin();
        initTasks();
        initGuide();
        initFinance();
        initProfileEnhancements();
        initConsultEnhancements();

        // bridgeReady 后加载所有数据
        window.addEventListener('bridgeReady', function () {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
            loadTwinAccuracy();
            loadActiveTasks();
            loadMedalStats();
            loadPreferences();
            loadPhotos();
            loadExpenseSummary();
        });

        // 如果 bridge 已经 ready（mock 模式下立即触发）
        if (window.bridge && window.bridge.ready && window.bridge.ready()) {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
            loadTwinAccuracy();
            loadActiveTasks();
            loadMedalStats();
            loadPreferences();
            loadPhotos();
            loadExpenseSummary();
        }
    });

})();
