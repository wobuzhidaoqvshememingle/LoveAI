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

    // 全局 Tab 切换：左侧导航与"设置中心→高级功能"入口共用。
    // 对没有对应导航项的 tab（高级功能页）也能正常显示面板。
    function switchTab(tab) {
        document.querySelectorAll('.sidebar .nav-item[data-tab]').forEach(function (i) {
            i.classList.remove('active');
        });
        var navItem = document.querySelector('.sidebar .nav-item[data-tab="' + tab + '"]');
        if (navItem) navItem.classList.add('active');

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
                loadPersonProfile();
            }, 50);
        }
        // 阶段三：切换到对应Tab时加载数据
        if (tab === 'twin') { loadTwinAccuracy(); }
        if (tab === 'tasks') { loadActiveTasks(); }
        if (tab === 'medal-wall') { loadMedalStats(); loadAllMedalsWithCategory(); }
        if (tab === 'finance') { loadExpenseSummary(); }
        if (tab === 'guide') { loadGuideHistory(document.getElementById('guideHistoryFilter') ? document.getElementById('guideHistoryFilter').value : 'all'); }
        if (tab === 'counseling') { loadConsultHistory(); }
        // 阶段四：切换Tab时懒加载
        if (tab === 'stats') { loadStatsDashboard(); loadMessageStats(_statsDays); loadAffectionStats(_statsDays); loadRelationComparison(); loadWeeklyReport(); }
        if (tab === 'voice') { refreshVoiceStatus(); }
        if (tab === 'ocr') { refreshOcrStatus(); }
        if (tab === 'plugins') { loadSkills(); loadPlugins(); }
        if (tab === 'memory') { loadMemoryStats(); loadMemories(_memFilter); }
        if (tab === 'backup') { loadBackupStats(); loadBackupList(); }
    }
    // 暴露给内联 onclick 等场景
    window.switchTab = switchTab;

    function initNavTabs() {
        document.querySelectorAll('.sidebar .nav-item[data-tab]').forEach(function (item) {
            item.addEventListener('click', function () {
                switchTab(item.getAttribute('data-tab'));
            });
        });

        // 底部栏：数据统计
        var statsBtn = document.getElementById('openDataStatsBtn');
        if (statsBtn) statsBtn.addEventListener('click', function () { switchTab('stats'); });

        // 设置中心：高级功能入口（切 tab 并关闭设置模态框）
        document.querySelectorAll('.adv-feature-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                switchTab(btn.getAttribute('data-tab'));
                hideModal('settingsModal');
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

            // 填充聊天分析页面的人物选择下拉框
            var select = document.getElementById('chatPersonSelect');
            if (select) {
                var currentVal = select.value;
                select.innerHTML = '<option value="">选择聊天对象...</option>';
                list.forEach(function (person) {
                    var opt = document.createElement('option');
                    opt.value = person.id;
                    opt.textContent = person.name || '未命名';
                    select.appendChild(opt);
                });
                // 恢复之前的选择，或默认选第一个
                if (currentVal && list.some(function (x) { return x.id === currentVal; })) {
                    select.value = currentVal;
                } else if (list.length > 0) {
                    select.value = list[0].id;
                    window._currentPersonId = list[0].id;
                }
            }
        }).catch(function (e) { console.error(e); });
    }

    function loadChatMessages(personId, limit) {
        if (!personId) {
            document.getElementById('chatRecordCount').textContent = '请先选择聊天对象';
            return;
        }
        var countEl = document.getElementById('chatRecordCount');
        if (countEl) countEl.textContent = '加载中...';
        call('get_chat_messages', personId, limit || 50).then(function (data) {
            var messages = (data && data.messages) || [];
            var input = document.getElementById('chatInput');
            if (input && messages.length > 0) {
                var lines = messages.map(function (m) {
                    var prefix = m.direction === 'out' ? '我：' : '对方：';
                    return prefix + m.content;
                });
                input.value = lines.join('\n');
            } else if (input) {
                input.value = '';
            }
            if (countEl) {
                countEl.textContent = messages.length > 0
                    ? '已加载 ' + messages.length + ' 条记录'
                    : '暂无聊天记录';
            }
        }).catch(function (e) {
            console.error('加载聊天记录失败:', e);
            if (countEl) countEl.textContent = '加载失败';
        });
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
        call('get_all_medals', window._currentPersonId || null).then(function (medals) {
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

        // 人物选择下拉框：切换时自动加载聊天记录
        var personSelect = document.getElementById('chatPersonSelect');
        if (personSelect) {
            personSelect.addEventListener('change', function () {
                var pid = personSelect.value;
                window._currentPersonId = pid || null;
                if (pid) {
                    loadChatMessages(pid, 50);
                }
            });
        }

        // "加载最近50条"按钮
        var loadBtn = document.getElementById('loadChatBtn');
        if (loadBtn) {
            loadBtn.addEventListener('click', function () {
                var pid = (personSelect && personSelect.value) || window._currentPersonId;
                if (pid) {
                    loadChatMessages(pid, 50);
                } else {
                    alert('请先选择聊天对象');
                }
            });
        }

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

        // 6. 回复建议（单条兜底；后端返回 replies 数组 >=3 时渲染 3 候选并排）
        var singleReply = d.suggested_reply || (d.llm_analysis && d.llm_analysis.reply) || '';
        var replyList = d.replies
            || (d.llm_analysis && d.llm_analysis.replies)
            || (d.llm_analysis && d.llm_analysis.possible_replies)
            || null;
        renderSuggestedReply(singleReply, replyList);

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
        // intent 可能是字符串或 {category, confidence, implied_meaning, true_intent}
        var cat = typeof intent === 'string' ? intent : (intent.category || intent.summary || '日常');
        var conf = typeof intent === 'object' ? intent.confidence : null;
        box.innerHTML = '<span class="intent-badge ib-' + escapeHtml(cat) + '">' + escapeHtml(cat) + '</span>';
        var cBox = document.getElementById('intentConfidence');
        if (cBox && conf != null) {
            cBox.innerHTML = '置信度 <b>' + Math.round(conf * 100) + '%</b>';
        }
        // 显示言外之意
        var implied = typeof intent === 'object' ? (intent.implied_meaning || '') : '';
        var impliedBox = document.getElementById('intentImplied');
        var impliedText = document.getElementById('intentImpliedText');
        if (impliedBox && impliedText) {
            if (implied) {
                impliedText.textContent = implied;
                impliedBox.style.display = 'block';
                appendDanmaku('💭 言外之意: ' + implied.substring(0, 30), 'info');
            } else {
                impliedBox.style.display = 'none';
            }
        }
        // 显示真实意图
        var trueIntent = typeof intent === 'object' ? (intent.true_intent || '') : '';
        var trueBox = document.getElementById('intentTrue');
        var trueText = document.getElementById('intentTrueText');
        if (trueBox && trueText) {
            if (trueIntent) {
                trueText.textContent = trueIntent;
                trueBox.style.display = 'block';
                appendDanmaku('🎯 真实意图: ' + trueIntent.substring(0, 30), 'info');
            } else {
                trueBox.style.display = 'none';
            }
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

    /** 6. 渲染回复建议（单条兜底；replies 数组 >=3 时渲染 3 候选并排） */
    function renderSuggestedReply(reply, replies) {
        var box = document.getElementById('suggestedReply');
        if (!box) return;

        // 收集候选：优先用 replies 数组，否则退回单条 reply
        var candidates = [];
        if (Array.isArray(replies) && replies.length > 0) {
            candidates = replies
                .map(function (r) { return String(r == null ? '' : r); })
                .filter(function (s) { return s.trim().length > 0; });
        }
        if (candidates.length === 0 && reply) {
            candidates = [String(reply)];
        }

        if (candidates.length === 0) {
            box.innerHTML = '<p class="placeholder-text">未生成回复</p>';
            window._lastSuggestedReply = '';
            return;
        }

        // 默认填入/复制目标：第一条（保留原单条行为）
        window._lastSuggestedReply = candidates[0];

        // —— 3 候选并排分支 ——
        if (candidates.length >= 3) {
            box.innerHTML = '';
            var row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:8px;align-items:stretch;';
            candidates.slice(0, 3).forEach(function (text, idx) {
                var card = document.createElement('div');
                card.style.cssText = (
                    'flex:1;min-width:0;background:#16213e;'
                    + 'border:1px solid rgba(212,175,55,0.35);border-radius:6px;'
                    + 'padding:8px;display:flex;flex-direction:column;gap:6px;'
                );
                var head = document.createElement('div');
                head.style.cssText = 'font-size:11px;color:#d4af37;font-weight:600;';
                head.textContent = '候选 ' + (idx + 1);
                var body = document.createElement('div');
                body.style.cssText = (
                    'font-size:12px;line-height:1.5;color:#e8e8f0;'
                    + 'white-space:pre-wrap;word-break:break-word;flex:1;'
                );
                body.textContent = text;
                var useBtn = document.createElement('button');
                useBtn.textContent = '用这条';
                useBtn.style.cssText = (
                    'padding:4px 6px;border:none;border-radius:4px;cursor:pointer;'
                    + 'background:#1f2b4d;color:#f0d060;font-size:11px;'
                );
                useBtn.onclick = function () {
                    window._lastSuggestedReply = text;
                    appendDanmaku('已选中候选 ' + (idx + 1), 'info');
                };
                card.appendChild(head);
                card.appendChild(body);
                card.appendChild(useBtn);
                row.appendChild(card);
            });
            box.appendChild(row);
            appendDanmaku('✅ 已生成 3 条候选回复', 'success');
            return;
        }

        // —— 单条兜底（保持原样式）——
        box.textContent = candidates[0];
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
            var wechatNick = p.wechat_nickname || p.name || '';
            document.getElementById('topPersonName').textContent = wechatNick || '未选择';
            document.getElementById('topStageBadge').textContent = p.stage || '初识';

            // 根据微信昵称查找匹配的人物，自动选中并加载聊天记录
            if (wechatNick) {
                call('list_persons').then(function (list) {
                    if (!list || !list.length) return;
                    // 优先精确匹配昵称，其次模糊匹配
                    var matched = list.find(function (x) {
                        return x.name === wechatNick || x.wechat_nickname === wechatNick;
                    });
                    if (!matched) {
                        matched = list.find(function (x) {
                            return x.name && wechatNick &&
                                (x.name.indexOf(wechatNick) >= 0 || wechatNick.indexOf(x.name) >= 0);
                        });
                    }
                    if (matched) {
                        window._currentPersonId = matched.id;
                        // 在聊天分析下拉框中选中
                        var select = document.getElementById('chatPersonSelect');
                        if (select) select.value = matched.id;
                        // 自动加载聊天记录
                        loadChatMessages(matched.id, 50);
                        // 如果开启了自动分析，延迟1秒后自动分析（等记录加载完）
                        var autoToggle = document.getElementById('autoAnalysisToggle');
                        if (autoToggle && autoToggle.checked) {
                            setTimeout(function () {
                                startChatAnalysis();
                            }, 1200);
                        }
                        loadAffection();
                        loadPersonProfile();
                    }
                }).catch(function (e) { console.error('自动匹配人物失败:', e); });
            }
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

        // 一键导入微信聊天记录
        var importBtn = document.getElementById('importWechatBtn');
        if (importBtn) {
            importBtn.addEventListener('click', function () {
                var personId = window._currentPersonId || null;
                if (!personId) {
                    showToast('请先选择人物', 'warning');
                    return;
                }
                var name = document.getElementById('profileName').value || '该联系人';
                if (!confirm('确定导入「' + name + '」的微信聊天记录吗？\n（会自动打开微信搜索该联系人并拉取最近200条消息）')) {
                    return;
                }
                importBtn.disabled = true;
                importBtn.textContent = '⏳ 导入中...';
                showToast('正在打开微信并拉取聊天记录...', 'info');
                call('import_wechat_chat', personId, 200).then(function (res) {
                    var count = res.count || 0;
                    showToast('导入完成：' + count + ' 条消息', 'success');
                    appendDanmaku('📥 已导入「' + name + '」聊天记录 ' + count + ' 条', 'success');
                }).catch(function (e) {
                    showToast('导入失败: ' + (e.message || e), 'error');
                }).finally(function () {
                    importBtn.disabled = false;
                    importBtn.textContent = '📥 实时拉取';
                });
            });
        }

        // 从文件导入聊天记录
        var fileBtn = document.getElementById('importFileBtn');
        if (fileBtn) {
            fileBtn.addEventListener('click', function () {
                var personId = window._currentPersonId || null;
                if (!personId) {
                    showToast('请先选择人物', 'warning');
                    return;
                }
                var fi = document.createElement('input');
                fi.type = 'file';
                fi.accept = '.txt,.csv,.json';
                fi.onchange = function () {
                    if (!fi.files || !fi.files[0]) return;
                    var file = fi.files[0];
                    fileBtn.disabled = true;
                    fileBtn.textContent = '⏳ 导入中...';
                    showToast('正在解析文件: ' + file.name, 'info');
                    // 读取文件内容为文本，传给后端解析
                    var reader = new FileReader();
                    reader.onload = function (e) {
                        var content = e.target.result;
                        // 先保存到临时文件，后端解析
                        call('import_chat_from_text', personId, content, file.name).then(function (res) {
                            var newCount = res.new_count || 0;
                            var total = res.total || 0;
                            showToast('导入完成：新增' + newCount + '条，累计' + total + '条', 'success');
                            appendDanmaku('📂 文件导入：新增' + newCount + '条消息', 'success');
                        }).catch(function (err) {
                            showToast('导入失败: ' + (err.message || err), 'error');
                        }).finally(function () {
                            fileBtn.disabled = false;
                            fileBtn.textContent = '📥 一键导入';
                        });
                    };
                    reader.readAsText(file, 'UTF-8');
                };
                fi.click();
            });
        }

        // 生成人物档案
        var genProfileBtn = document.getElementById('genProfileBtn');
        if (genProfileBtn) {
            genProfileBtn.addEventListener('click', function () {
                var personId = window._currentPersonId || null;
                if (!personId) {
                    showToast('请先选择人物', 'warning');
                    return;
                }
                genProfileBtn.disabled = true;
                genProfileBtn.textContent = '⏳ 分析中...';
                showToast('正在分析聊天记录，生成人物档案...', 'info');
                call('generate_person_profile', personId).then(function (res) {
                    showToast('档案生成完成：分析了' + res.message_count + '条消息', 'success');
                    appendDanmaku('🧠 人物档案已生成（' + res.message_count + '条样本）', 'success');
                    // 显示 SOUL 内容
                    if (res.soul) {
                        var soulArea = document.getElementById('soulText');
                        if (soulArea) soulArea.value = res.soul;
                    }
                }).catch(function (e) {
                    showToast('生成失败: ' + (e.message || e), 'error');
                }).finally(function () {
                    genProfileBtn.disabled = false;
                    genProfileBtn.textContent = '🧠 生成档案';
                });
            });
        }

        // 生理周期 - 添加记录按钮
        var addCycleBtn = document.getElementById('addCycleBtn');
        if (addCycleBtn) {
            addCycleBtn.addEventListener('click', function () {
                var form = document.getElementById('cycleAddForm');
                if (form) {
                    form.style.display = form.style.display === 'none' ? 'block' : 'none';
                    // 默认日期为今天
                    var dateInput = document.getElementById('cycleStartDate');
                    if (dateInput && !dateInput.value) {
                        var today = new Date().toISOString().split('T')[0];
                        dateInput.value = today;
                    }
                }
            });
        }

        // 生理周期 - 保存记录
        var saveCycleBtn = document.getElementById('saveCycleBtn');
        if (saveCycleBtn) {
            saveCycleBtn.addEventListener('click', function () {
                var personId = window._currentPersonId || null;
                if (!personId) {
                    showToast('请先选择人物', 'warning');
                    return;
                }
                var startDate = document.getElementById('cycleStartDate').value;
                var cycleLength = parseInt(document.getElementById('cycleLength').value) || 28;
                var periodLength = parseInt(document.getElementById('periodLength').value) || 5;
                var symptoms = document.getElementById('cycleSymptoms').value || '';

                if (!startDate) {
                    showToast('请选择开始日期', 'warning');
                    return;
                }

                call('add_menstrual_cycle', personId, startDate, cycleLength, periodLength, symptoms, '')
                    .then(function () {
                        showToast('记录成功', 'success');
                        document.getElementById('cycleAddForm').style.display = 'none';
                        loadCycleData(personId);
                    }).catch(function (e) {
                        showToast('保存失败: ' + (e.message || e), 'error');
                    });
            });
        }

        // 生理周期 - 取消
        var cancelCycleBtn = document.getElementById('cancelCycleBtn');
        if (cancelCycleBtn) {
            cancelCycleBtn.addEventListener('click', function () {
                document.getElementById('cycleAddForm').style.display = 'none';
            });
        }

        // 心情状态 - 设置心情按钮
        var changeMoodBtn = document.getElementById('changeMoodBtn');
        if (changeMoodBtn) {
            changeMoodBtn.addEventListener('click', function () {
                var selector = document.getElementById('moodSelector');
                if (selector) {
                    var isVisible = selector.style.display !== 'none';
                    selector.style.display = isVisible ? 'none' : 'block';
                    if (!isVisible) {
                        // 加载心情标签列表
                        loadMoodTags();
                    }
                }
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

    function loadPersonProfile() {
        var personId = window._currentPersonId || null;
        if (!personId) return;
        call('get_person', personId).then(function (p) {
            if (!p) return;
            function setVal(id, val) {
                var el = document.getElementById(id);
                if (el && val !== null && val !== undefined) el.value = val;
            }
            setVal('profileName', p.nickname || '');
            setVal('profileGender', p.gender || '');
            setVal('profileAge', p.age || '');
            setVal('profileOccupation', p.occupation || '');
            setVal('profileNote', p.note || '');
            setVal('psychMbti', p.mbti || '');
            setVal('psychBigFive', p.big_five || '');
            setVal('psychAttachment', p.attachment || '');
            setVal('psychCommStyle', p.comm_style || '');
            setVal('soulEditor', p.soul_text || '');
            // 加载生理周期
            loadCycleData(personId);
            // 加载心情状态
            loadMoodData(personId);
        }).catch(function (e) { console.error('加载档案失败', e); });
    }

    // ===== 生理周期 =====
    function loadCycleData(personId) {
        if (!personId) return;
        // 加载当前阶段
        call('get_current_cycle_phase', personId).then(function (data) {
            var phaseEl = document.getElementById('cycleCurrentPhase');
            var adviceEl = document.getElementById('cycleAdvice');
            var adviceContent = document.getElementById('cycleAdviceContent');
            if (!phaseEl) return;

            if (data && data.has_data) {
                var color = data.color || '#a855f7';
                phaseEl.innerHTML =
                    '<div style="padding:12px; background:rgba(' + hexToRgb(color) + ',0.1); border-radius:8px; border-left:3px solid ' + color + ';">' +
                    '<div style="font-size:18px; font-weight:bold; color:' + color + ';">' + data.phase_name +
                    ' <span style="font-size:13px; font-weight:normal;">（第' + data.day_in_phase + '天）</span></div>' +
                    '<div style="margin-top:6px; font-size:13px; color:#999;">距离下次姨妈：' + data.days_until_next_period + '天 | ' +
                    '下次姨妈：' + data.next_period_date + '</div>' +
                    '<div style="margin-top:6px; font-size:13px;">典型情绪：' + (data.emotion_traits || []).join('、') + '</div>' +
                    '</div>';
                // 显示攻略
                if (adviceEl && adviceContent) {
                    adviceEl.style.display = 'block';
                    adviceContent.innerHTML =
                        '<div><strong>相处建议：</strong>' + (data.advice || '') + '</div>' +
                        '<div style="margin-top:6px;"><strong>攻略策略：</strong>' + (data.strategy || '') + '</div>';
                }
            } else {
                phaseEl.innerHTML = '<p class="placeholder-text">暂无周期记录，点击上方按钮添加</p>';
                if (adviceEl) adviceEl.style.display = 'none';
            }
        }).catch(function (e) { console.error('加载周期阶段失败', e); });

        // 加载历史记录和预测
        call('get_menstrual_cycles', personId).then(function (data) {
            var listEl = document.getElementById('cycleHistoryList');
            if (!listEl) return;
            var cycles = (data && data.cycles) || [];
            if (cycles.length === 0) {
                listEl.innerHTML = '<p class="placeholder-text">暂无记录</p>';
                return;
            }
            var html = cycles.map(function (c) {
                return '<div style="padding:8px; border-bottom:1px solid rgba(255,255,255,0.05); font-size:13px;">' +
                    '📅 ' + c.start_date + ' ~ ' + c.end_date +
                    ' <span style="color:#666;">（周期' + c.cycle_length + '天 / 经期' + c.period_length + '天）</span>' +
                    (c.symptoms ? ' <span style="color:#f59e0b;">' + c.symptoms + '</span>' : '') +
                    '</div>';
            }).join('');
            listEl.innerHTML = html;
        }).catch(function (e) { console.error('加载周期历史失败', e); });
    }

    function hexToRgb(hex) {
        var r = parseInt(hex.slice(1, 3), 16);
        var g = parseInt(hex.slice(3, 5), 16);
        var b = parseInt(hex.slice(5, 7), 16);
        return r + ',' + g + ',' + b;
    }

    // ===== 心情状态 =====
    function loadMoodTags() {
        // 加载心情标签列表
        call('get_all_mood_tags').then(function (data) {
            var listEl = document.getElementById('moodTagList');
            if (!listEl || !data || !data.tags) return;
            var personId = window._currentPersonId || null;
            listEl.innerHTML = data.tags.map(function (tag) {
                return '<button class="mood-tag-btn" data-mood="' + tag.tag + '" style="padding:6px 12px; border:1px solid ' + tag.color + '; border-radius:16px; background:transparent; color:' + tag.color + '; cursor:pointer; font-size:13px;">' +
                    tag.emoji + ' ' + tag.name + '</button>';
            }).join('');
            // 绑定点击事件
            listEl.querySelectorAll('.mood-tag-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    if (!personId) {
                        showToast('请先选择人物', 'warning');
                        return;
                    }
                    var mood = btn.getAttribute('data-mood');
                    call('set_mood', personId, mood).then(function () {
                        showToast('心情已设置', 'success');
                        document.getElementById('moodSelector').style.display = 'none';
                        loadMoodData(personId);
                    }).catch(function (e) { showToast('设置失败: ' + e.message, 'error'); });
                });
            });
        }).catch(function (e) { console.error('加载心情标签失败', e); });
    }

    function loadMoodData(personId) {
        if (!personId) return;
        // 加载当前心情
        call('get_current_mood', personId).then(function (data) {
            var displayEl = document.getElementById('currentMoodDisplay');
            if (!displayEl) return;
            if (data && data.has_mood) {
                var color = data.color || '#a855f7';
                var sourceText = data.source === 'manual' ? '手动设置' : '自动检测';
                displayEl.innerHTML =
                    '<div style="padding:16px; background:rgba(' + hexToRgb(color) + ',0.1); border-radius:8px; border-left:3px solid ' + color + ';">' +
                    '<div style="font-size:24px;">' + data.emoji + ' <span style="font-size:18px; font-weight:bold; color:' + color + ';">' + data.name + '</span></div>' +
                    '<div style="margin-top:6px; font-size:12px; color:#999;">来源：' + sourceText + ' | 置信度：' + Math.round(data.confidence * 100) + '%</div>' +
                    (data.context ? '<div style="margin-top:4px; font-size:12px; color:#666;">' + data.context + '</div>' : '') +
                    '</div>';
            } else {
                displayEl.innerHTML = '<p class="placeholder-text">暂无心情状态，聊天时会自动检测</p>';
            }
        }).catch(function (e) { console.error('加载心情失败', e); });

        // 加载惯例模式
        call('get_mood_patterns', personId).then(function (data) {
            var listEl = document.getElementById('moodPatternList');
            if (!listEl) return;
            var patterns = (data && data.patterns) || [];
            if (patterns.length === 0) {
                listEl.innerHTML = '<p class="placeholder-text">暂无记录，聊多了会自动识别</p>';
                return;
            }
            listEl.innerHTML = patterns.map(function (p) {
                return '<div style="padding:6px 0; font-size:13px; border-bottom:1px solid rgba(255,255,255,0.05);">' +
                    p.emoji + ' <strong>' + p.mood_name + '</strong>' +
                    ' <span style="color:#999;">' + p.pattern_desc + '</span>' +
                    ' <span style="color:#666;">（' + p.frequency + '次）</span>' +
                    '</div>';
            }).join('');
        }).catch(function (e) { console.error('加载惯例模式失败', e); });

        // 加载心情历史
        call('get_mood_history', personId).then(function (data) {
            var listEl = document.getElementById('moodHistoryList');
            if (!listEl) return;
            var history = (data && data.history) || [];
            if (history.length === 0) {
                listEl.innerHTML = '<p class="placeholder-text">暂无记录</p>';
                return;
            }
            listEl.innerHTML = history.slice(0, 10).map(function (h) {
                return '<span style="display:inline-block; margin:2px; padding:4px 8px; background:rgba(255,255,255,0.05); border-radius:12px; font-size:12px;" title="' + h.created_at + '">' +
                    h.emoji + ' ' + h.name + '</span>';
            }).join('');
        }).catch(function (e) { console.error('加载心情历史失败', e); });
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

    // ============================================================
    // 阶段四：Canvas 图表工具（原生2D，无图表库）
    // ============================================================

    /** 高清适配 canvas */
    function setupCanvas(canvas) {
        if (!canvas) return null;
        var dpr = window.devicePixelRatio || 1;
        var cssW = canvas.clientWidth || canvas.width;
        var cssH = canvas.clientHeight || 220;
        canvas.width = cssW * dpr;
        canvas.height = cssH * dpr;
        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, cssW, cssH);
        return { ctx: ctx, w: cssW, h: cssH };
    }

    /** 空状态：在 canvas 上画一行提示文字 */
    function drawEmptyChart(canvas, text) {
        var c = setupCanvas(canvas);
        if (!c) return;
        c.ctx.fillStyle = getCssVar('--text-muted');
        c.ctx.font = '13px sans-serif';
        c.ctx.textAlign = 'center';
        c.ctx.textBaseline = 'middle';
        c.ctx.fillText(text || '暂无数据', c.w / 2, c.h / 2);
    }

    /**
     * 双序列柱状图（sent vs received）
     * @param {HTMLCanvasElement} canvas
     * @param {Array} labels  x 轴标签（日期）
     * @param {Array<number>} sent 发送数
     * @param {Array<number>} received 接收数
     */
    function drawBarChart(canvas, labels, sent, received) {
        if (!labels || !labels.length) { drawEmptyChart(canvas, '暂无消息数据'); return; }
        var c = setupCanvas(canvas);
        if (!c) return;
        var ctx = c.ctx, w = c.w, h = c.h;
        var padL = 36, padR = 12, padT = 16, padB = 28;
        var chartW = w - padL - padR, chartH = h - padT - padB;

        var maxV = 1;
        sent.concat(received).forEach(function (v) { if (v > maxV) maxV = v; });
        // y 轴网格
        ctx.strokeStyle = getCssVar('--border-color');
        ctx.lineWidth = 1;
        ctx.fillStyle = getCssVar('--text-muted');
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        for (var g = 0; g <= 4; g++) {
            var y = padT + chartH * (1 - g / 4);
            ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
            ctx.fillText(Math.round(maxV * g / 4), padL - 4, y);
        }
        // 柱子
        var n = labels.length;
        var slot = chartW / n;
        var barW = Math.min(14, slot * 0.32);
        for (var i = 0; i < n; i++) {
            var cx = padL + slot * i + slot / 2;
            var sH = (sent[i] || 0) / maxV * chartH;
            var rH = (received[i] || 0) / maxV * chartH;
            // sent (金色)
            ctx.fillStyle = getCssVar('--gold-primary');
            ctx.fillRect(cx - barW - 1, padT + chartH - sH, barW, sH);
            // received (蓝灰)
            ctx.fillStyle = '#5a7fb8';
            ctx.fillRect(cx + 1, padT + chartH - rH, barW, rH);
            // x 轴标签（稀疏显示）
            if (n <= 14 || i % Math.ceil(n / 14) === 0) {
                ctx.fillStyle = getCssVar('--text-muted');
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillText(String(labels[i]).slice(5), cx, padT + chartH + 4);
            }
        }
        // 图例
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.font = '11px sans-serif';
        ctx.fillStyle = getCssVar('--gold-primary'); ctx.fillRect(padL, 4, 10, 10);
        ctx.fillStyle = getCssVar('--text-secondary'); ctx.fillText('发送', padL + 14, 9);
        ctx.fillStyle = '#5a7fb8'; ctx.fillRect(padL + 60, 4, 10, 10);
        ctx.fillStyle = getCssVar('--text-secondary'); ctx.fillText('接收', padL + 74, 9);
    }

    /**
     * 折线图（好感度趋势）
     */
    function drawLineChart(canvas, labels, values) {
        if (!values || !values.length) { drawEmptyChart(canvas, '暂无好感度数据'); return; }
        var c = setupCanvas(canvas);
        if (!c) return;
        var ctx = c.ctx, w = c.w, h = c.h;
        var padL = 36, padR = 12, padT = 16, padB = 28;
        var chartW = w - padL - padR, chartH = h - padT - padB;
        var minV = Math.min.apply(null, values);
        var maxV = Math.max.apply(null, values);
        if (maxV === minV) { maxV = minV + 10; }
        // y 网格
        ctx.strokeStyle = getCssVar('--border-color');
        ctx.fillStyle = getCssVar('--text-muted');
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        for (var g = 0; g <= 4; g++) {
            var y = padT + chartH * (1 - g / 4);
            ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
            ctx.fillText(Math.round(minV + (maxV - minV) * g / 4), padL - 4, y);
        }
        // 折线
        var n = values.length;
        var grad = ctx.createLinearGradient(0, padT, 0, padT + chartH);
        grad.addColorStop(0, 'rgba(212,175,55,0.25)');
        grad.addColorStop(1, 'rgba(212,175,55,0)');
        ctx.beginPath();
        for (var i = 0; i < n; i++) {
            var x = padL + (n === 1 ? chartW / 2 : chartW * i / (n - 1));
            var y = padT + chartH * (1 - (values[i] - minV) / (maxV - minV));
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        // 填充区域
        var lineEnd = ctx.currentPoint;
        ctx.lineTo(padL + chartW * (n - 1) / (n === 1 ? 1 : (n - 1)), padT + chartH);
        ctx.lineTo(padL, padT + chartH);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();
        // 描边
        ctx.beginPath();
        for (var i2 = 0; i2 < n; i2++) {
            var x2 = padL + (n === 1 ? chartW / 2 : chartW * i2 / (n - 1));
            var y2 = padT + chartH * (1 - (values[i2] - minV) / (maxV - minV));
            if (i2 === 0) ctx.moveTo(x2, y2); else ctx.lineTo(x2, y2);
        }
        ctx.strokeStyle = getCssVar('--gold-primary');
        ctx.lineWidth = 2;
        ctx.stroke();
        // 点
        ctx.fillStyle = getCssVar('--gold-light');
        for (var i3 = 0; i3 < n; i3++) {
            var x3 = padL + (n === 1 ? chartW / 2 : chartW * i3 / (n - 1));
            var y3 = padT + chartH * (1 - (values[i3] - minV) / (maxV - minV));
            ctx.beginPath(); ctx.arc(x3, y3, 2.5, 0, Math.PI * 2); ctx.fill();
        }
        // x 标签
        ctx.fillStyle = getCssVar('--text-muted');
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        if (labels && labels.length) {
            var step = Math.ceil(n / 7);
            for (var i4 = 0; i4 < n; i4 += step) {
                var x4 = padL + (n === 1 ? chartW / 2 : chartW * i4 / (n - 1));
                ctx.fillText(String(labels[i4]).slice(5), x4, padT + chartH + 4);
            }
        }
    }

    /**
     * 饼图
     * @param {HTMLCanvasElement} canvas
     * @param {Array<{label:string, value:number, color?:string}>} slices
     */
    function drawPieChart(canvas, slices) {
        if (!slices || !slices.length) { drawEmptyChart(canvas, '暂无数据'); return []; }
        var c = setupCanvas(canvas);
        if (!c) return [];
        var ctx = c.ctx, w = c.w, h = c.h;
        var cx = w / 2, cy = h / 2;
        var radius = Math.min(w, h) / 2 - 8;
        var total = slices.reduce(function (s, x) { return s + (x.value || 0); }, 0);
        if (total <= 0) { drawEmptyChart(canvas, '暂无数据'); return []; }
        var palette = ['#d4af37', '#5a7fb8', '#4ade80', '#c084fc', '#f87171', '#fbbf24', '#60a5fa', '#94a3b8'];
        var angle = -Math.PI / 2;
        var legend = [];
        slices.forEach(function (s, i) {
            var sliceAngle = (s.value / total) * Math.PI * 2;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.arc(cx, cy, radius, angle, angle + sliceAngle);
            ctx.closePath();
            ctx.fillStyle = s.color || palette[i % palette.length];
            ctx.fill();
            angle += sliceAngle;
            legend.push({ label: s.label, value: s.value, pct: Math.round(s.value / total * 100), color: s.color || palette[i % palette.length] });
        });
        return legend;
    }

    // ============================================================
    // 阶段四：数据统计
    // ============================================================

    var _statsDays = 30;
    var _memFilter = 'all';

    function initStats() {
        // 时间范围切换
        document.querySelectorAll('#statsRangeSwitch .range-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#statsRangeSwitch .range-btn').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                _statsDays = parseInt(btn.getAttribute('data-days')) || 30;
                loadStatsDashboard();
                loadMessageStats(_statsDays);
                loadAffectionStats(_statsDays);
                loadWeeklyReport();
            });
        });
        var refreshBtn = document.getElementById('refreshWeeklyBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', loadWeeklyReport);
    }

    function loadStatsDashboard() {
        var pid = window._currentPersonId || null;
        call('get_dashboard', pid).then(function (res) {
            res = res || {};
            setText('dashMsgTotal', res.msg_total != null ? res.msg_total : '--');
            setText('dashTaskDone', res.task_done != null ? res.task_done : '--');
            setText('dashTaskActive', res.task_active != null ? res.task_active : '--');
            setText('dashAffection', res.affection != null ? res.affection : '--');
            setText('dashMedal', res.medals_unlocked != null ? res.medals_unlocked : '--');
            setText('dashDays', res.used_days != null ? res.used_days : '--');
        }).catch(function (e) { console.warn('get_dashboard', e); });
    }

    function loadMessageStats(days) {
        var pid = window._currentPersonId || null;
        call('get_message_stats', pid, days).then(function (res) {
            res = res || {};
            var labels = res.dates || [];
            drawBarChart(document.getElementById('msgBarChart'), labels, res.sent || [], res.received || []);
            // 方向饼图
            var sent = res.sent_total || 0, received = res.received_total || 0;
            var dirLegend = drawPieChart(document.getElementById('msgDirPie'), [
                { label: '我发送', value: sent, color: '#d4af37' },
                { label: '对方回复', value: received, color: '#5a7fb8' },
            ]);
            renderLegend('msgDirLegend', dirLegend);
            // 时段饼图
            var hourSlices = [];
            var hourMap = res.hour_distribution || {};
            Object.keys(hourMap).forEach(function (h) {
                hourSlices.push({ label: h + '点', value: hourMap[h] });
            });
            var hourLegend = drawPieChart(document.getElementById('msgHourPie'), hourSlices);
            renderLegend('msgHourLegend', hourLegend);
        }).catch(function (e) {
            console.warn('get_message_stats', e);
            drawEmptyChart(document.getElementById('msgBarChart'), '暂无消息数据');
            drawEmptyChart(document.getElementById('msgDirPie'), '暂无方向数据');
            drawEmptyChart(document.getElementById('msgHourPie'), '暂无时段数据');
        });
    }

    function renderLegend(elId, legend) {
        var el = document.getElementById(elId);
        if (!el) return;
        if (!legend || !legend.length) { el.innerHTML = '<span class="placeholder-text">--</span>'; return; }
        el.innerHTML = legend.map(function (x) {
            return '<span class="legend-item"><i style="background:' + x.color + '"></i>' +
                escapeHtml(x.label) + ' · ' + x.value + ' (' + x.pct + '%)</span>';
        }).join('');
    }

    function loadAffectionStats(days) {
        var pid = window._currentPersonId || null;
        call('get_affection_stats', pid, days).then(function (res) {
            res = res || {};
            drawLineChart(document.getElementById('affLineChart'), res.dates || [], res.values || []);
            setText('affDelta7', (res.delta_7d != null ? (res.delta_7d > 0 ? '+' : '') + res.delta_7d : '--'));
            setText('affDelta30', (res.delta_30d != null ? (res.delta_30d > 0 ? '+' : '') + res.delta_30d : '--'));
            setText('affHigh', res.max != null ? res.max : '--');
            setText('affLow', res.min != null ? res.min : '--');
        }).catch(function (e) {
            console.warn('get_affection_stats', e);
            drawEmptyChart(document.getElementById('affLineChart'), '暂无好感度数据');
        });
    }

    function loadRelationComparison() {
        call('get_relation_comparison').then(function (rows) {
            var tbody = document.querySelector('#relationCompareTable tbody');
            if (!tbody) return;
            if (!rows || !rows.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="placeholder-cell">暂无对比数据</td></tr>';
                return;
            }
            tbody.innerHTML = rows.map(function (r) {
                return '<tr>' +
                    '<td>' + escapeHtml(r.nickname || r.name || '--') + '</td>' +
                    '<td class="stat-gold">' + (r.affection != null ? r.affection : '--') + '</td>' +
                    '<td>' + (r.task_count != null ? r.task_count : '--') + '</td>' +
                    '<td>' + (r.msg_count != null ? r.msg_count : '--') + '</td>' +
                    '<td>' + (r.medal_count != null ? r.medal_count : '--') + '</td>' +
                '</tr>';
            }).join('');
        }).catch(function (e) {
            var tbody = document.querySelector('#relationCompareTable tbody');
            if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="placeholder-cell">加载失败</td></tr>';
        });
    }

    function loadWeeklyReport() {
        var pid = window._currentPersonId || null;
        call('get_weekly_report', pid).then(function (res) {
            var box = document.getElementById('weeklyReportBox');
            if (!box) return;
            if (!res) { box.innerHTML = '<p class="placeholder-text">暂无周报</p>'; return; }
            var html = '<div class="weekly-grid">';
            if (res.metrics) {
                Object.keys(res.metrics).forEach(function (k) {
                    var m = res.metrics[k];
                    html += '<div class="weekly-item"><div class="weekly-label">' + escapeHtml(k) + '</div>' +
                        '<div class="weekly-value">' + escapeHtml(String(m.value != null ? m.value : '--')) +
                        (m.delta != null ? ' <span class="weekly-delta ' + (m.delta >= 0 ? 'up' : 'down') + '">' + (m.delta >= 0 ? '↑' : '↓') + Math.abs(m.delta) + '</span>' : '') +
                        '</div></div>';
                });
            }
            html += '</div>';
            if (res.highlights) html += '<div class="weekly-section"><div class="block-title">亮点</div><div>' + escapeHtml(res.highlights) + '</div></div>';
            if (res.suggestions) html += '<div class="weekly-section"><div class="block-title">建议</div><div>' + escapeHtml(res.suggestions) + '</div></div>';
            box.innerHTML = html || '<p class="placeholder-text">暂无周报</p>';
        }).catch(function (e) {
            var box = document.getElementById('weeklyReportBox');
            if (box) box.innerHTML = '<p class="placeholder-text">周报加载失败</p>';
        });
    }

    function setText(id, v) {
        var el = document.getElementById(id);
        if (el) el.textContent = v;
    }

    // ============================================================
    // 阶段四：语音
    // ============================================================

    function initVoice() {
        var micBtn = document.getElementById('micBtn');
        var stopBtn = document.getElementById('stopRecognizeBtn');
        var fillBtn = document.getElementById('recognizeFillBtn');
        var transcribeBtn = document.getElementById('transcribeFileBtn');

        if (micBtn) micBtn.addEventListener('click', function () {
            micBtn.classList.add('recording');
            setText('micTip', '录音中... 再次点击或点停止');
            call('start_voice_recording').then(function () {
                showToast('开始录音', 'success');
            }).catch(function (e) {
                micBtn.classList.remove('recording');
                setText('micTip', '开始失败：' + e.message);
            });
        });

        if (stopBtn) stopBtn.addEventListener('click', function () {
            var micBtn2 = document.getElementById('micBtn');
            if (micBtn2) micBtn2.classList.remove('recording');
            setText('micTip', '识别中...');
            call('stop_voice_and_recognize').then(function (res) {
                setText('micTip', '识别完成');
                var box = document.getElementById('voiceResult');
                box.style.display = 'block';
                setText('voiceRecognizedText', (res && (res.text || res.result)) ? (res.text || res.result) : JSON.stringify(res));
            }).catch(function (e) {
                setText('micTip', '识别失败：' + e.message);
                showToast(e.message, 'error');
            });
        });

        if (fillBtn) fillBtn.addEventListener('click', function () {
            fillBtn.disabled = true; fillBtn.textContent = '识别并填入中...';
            call('voice_recognize_and_fill').then(function (res) {
                fillBtn.disabled = false; fillBtn.textContent = '识别并填入微信';
                var box = document.getElementById('voiceResult');
                box.style.display = 'block';
                setText('voiceRecognizedText', (res && (res.text || res.result)) ? (res.text || res.result) : JSON.stringify(res));
                showToast('已填入微信', 'success');
            }).catch(function (e) {
                fillBtn.disabled = false; fillBtn.textContent = '识别并填入微信';
                showToast(e.message, 'error');
            });
        });

        if (transcribeBtn) transcribeBtn.addEventListener('click', function () {
            var path = document.getElementById('voiceFilePath').value.trim();
            if (!path) { showToast('请输入文件路径', 'warning'); return; }
            transcribeBtn.disabled = true; transcribeBtn.textContent = '识别中...';
            call('transcribe_audio_file', path).then(function (res) {
                transcribeBtn.disabled = false; transcribeBtn.textContent = '识别文件';
                var box = document.getElementById('fileTranscribeResult');
                box.style.display = 'block';
                setText('fileTranscribeText', (res && (res.text || res.result)) ? (res.text || res.result) : JSON.stringify(res));
            }).catch(function (e) {
                transcribeBtn.disabled = false; transcribeBtn.textContent = '识别文件';
                showToast(e.message, 'error');
            });
        });
    }

    function refreshVoiceStatus() {
        call('get_speech_status').then(function (res) {
            var box = document.getElementById('voiceStatusBox');
            if (!box) return;
            res = res || {};
            var ok = !!res.available;
            box.innerHTML =
                '<div class="svc-row"><span class="svc-dot ' + (ok ? 'on' : 'off') + '"></span>' +
                '<span>' + (ok ? '服务可用' : '服务不可用') + '</span></div>' +
                '<div class="svc-row"><span class="svc-k">模型</span><span>' + escapeHtml(res.model || '--') + '</span></div>' +
                '<div class="svc-row"><span class="svc-k">模型大小</span><span>' + escapeHtml(res.model_size || '--') + '</span></div>' +
                '<div class="svc-row"><span class="svc-k">录音库</span><span>' + escapeHtml(res.library || '--') + '</span></div>';
            document.getElementById('voiceFallback').style.display = ok ? 'none' : 'block';
        }).catch(function (e) {
            var box = document.getElementById('voiceStatusBox');
            if (box) box.innerHTML = '<p class="placeholder-text">状态获取失败：' + escapeHtml(e.message) + '</p>';
            document.getElementById('voiceFallback').style.display = 'block';
        });
    }

    // ============================================================
    // 阶段四：OCR
    // ============================================================

    function initOCR() {
        var capBtn = document.getElementById('captureOcrBtn');
        var capWxBtn = document.getElementById('captureWechatOcrBtn');
        var fileBtn = document.getElementById('ocrFileBtn');

        if (capBtn) capBtn.addEventListener('click', function () {
            capBtn.disabled = true; capBtn.textContent = '截屏识别中...';
            call('capture_and_ocr').then(function (res) {
                capBtn.disabled = false; capBtn.textContent = '🖼 截屏并识别';
                renderOcrResult(res);
            }).catch(function (e) {
                capBtn.disabled = false; capBtn.textContent = '🖼 截屏并识别';
                showToast(e.message, 'error');
            });
        });

        if (capWxBtn) capWxBtn.addEventListener('click', function () {
            capWxBtn.disabled = true; capWxBtn.textContent = '识别中...';
            call('capture_wechat_and_ocr').then(function (res) {
                capWxBtn.disabled = false; capWxBtn.textContent = '💬 微信区域截屏识别';
                renderOcrResult(res);
            }).catch(function (e) {
                capWxBtn.disabled = false; capWxBtn.textContent = '💬 微信区域截屏识别';
                showToast(e.message, 'error');
            });
        });

        if (fileBtn) fileBtn.addEventListener('click', function () {
            var path = document.getElementById('ocrImagePath').value.trim();
            if (!path) { showToast('请输入图片路径', 'warning'); return; }
            fileBtn.disabled = true; fileBtn.textContent = '识别中...';
            call('ocr_image_file', path).then(function (res) {
                fileBtn.disabled = false; fileBtn.textContent = '识别图片';
                var box = document.getElementById('ocrFileResult');
                box.style.display = 'block';
                setText('ocrFileText', (res && (res.full_text || res.text)) ? (res.full_text || res.text) : JSON.stringify(res));
            }).catch(function (e) {
                fileBtn.disabled = false; fileBtn.textContent = '识别图片';
                showToast(e.message, 'error');
            });
        });

        // 聊天分析输入区：截图分析按钮
        var capAnalyzeBtn = document.getElementById('captureAnalyzeBtn');
        if (capAnalyzeBtn) capAnalyzeBtn.addEventListener('click', captureAndFillOCR);
    }

    // 截屏 OCR 并把识别文本填入聊天分析输入框
    function captureAndFillOCR() {
        var btn = document.getElementById('captureAnalyzeBtn');
        var input = document.getElementById('chatInput');
        if (!input) return;
        if (btn) { btn.disabled = true; btn.textContent = '识别中...'; }
        call('capture_and_ocr').then(function (res) {
            if (btn) { btn.disabled = false; btn.textContent = '📷 截图分析'; }
            res = res || {};
            if (res.error) { showToast('OCR 错误：' + res.error, 'error'); return; }
            var text = (res.text || '').trim();
            var n = (res.lines && res.lines.length) ? res.lines.length : (text ? text.split('\n').length : 0);
            if (!text) { showToast('未识别到文字', 'warning'); return; }
            var cur = input.value;
            input.value = cur ? (cur + '\n' + text) : text;
            showToast('已识别 ' + n + ' 行文字', 'success');
            input.focus();
        }).catch(function (e) {
            if (btn) { btn.disabled = false; btn.textContent = '📷 截图分析'; }
            showToast('OCR 失败：' + ((e && e.message) ? e.message : String(e)), 'error');
        });
    }

    function renderOcrResult(res) {
        res = res || {};
        var box = document.getElementById('ocrResultBox');
        box.style.display = 'block';
        setText('ocrFullText', res.full_text || res.text || '--');
        var linesEl = document.getElementById('ocrLines');
        if (res.lines && res.lines.length) {
            linesEl.innerHTML = res.lines.map(function (l) {
                var txt = l.text || l;
                var conf = l.confidence != null ? ' <span class="ocr-conf">' + Math.round(l.confidence * 100) + '%</span>' : '';
                return '<div class="ocr-line">' + escapeHtml(txt) + conf + '</div>';
            }).join('');
        } else {
            linesEl.innerHTML = '<p class="placeholder-text">无分行数据</p>';
        }
        // 截图预览
        var prevWrap = document.getElementById('ocrPreviewWrap');
        var prevImg = document.getElementById('ocrPreviewImg');
        if (res.image_path || res.preview_path) {
            prevWrap.style.display = 'block';
            prevImg.src = res.image_path || res.preview_path;
        } else {
            prevWrap.style.display = 'none';
        }
    }

    function refreshOcrStatus() {
        call('get_ocr_status').then(function (res) {
            var box = document.getElementById('ocrStatusBox');
            if (!box) return;
            res = res || {};
            var ok = !!res.available;
            box.innerHTML =
                '<div class="svc-row"><span class="svc-dot ' + (ok ? 'on' : 'off') + '"></span>' +
                '<span>' + (ok ? 'OCR 可用' : 'OCR 不可用') + '</span></div>' +
                '<div class="svc-row"><span class="svc-k">引擎</span><span>' + escapeHtml(res.engine || '--') + '</span></div>' +
                '<div class="svc-row"><span class="svc-k">语言</span><span>' + escapeHtml(res.lang || '--') + '</span></div>';
            document.getElementById('ocrFallback').style.display = ok ? 'none' : 'block';
        }).catch(function (e) {
            var box = document.getElementById('ocrStatusBox');
            if (box) box.innerHTML = '<p class="placeholder-text">状态获取失败：' + escapeHtml(e.message) + '</p>';
            document.getElementById('ocrFallback').style.display = 'block';
        });
    }

    // ============================================================
    // 阶段四：插件中心
    // ============================================================

    function initPlugins() {
        var refreshSkills = document.getElementById('refreshSkillsBtn');
        if (refreshSkills) refreshSkills.addEventListener('click', loadSkills);
        var refreshPlugins = document.getElementById('refreshPluginsBtn');
        if (refreshPlugins) refreshPlugins.addEventListener('click', loadPlugins);
    }

    function loadSkills() {
        call('list_skills').then(function (list) {
            var grid = document.getElementById('skillGrid');
            if (!grid) return;
            if (!list || !list.length) {
                grid.innerHTML = '<p class="placeholder-text">暂无技能</p>';
                return;
            }
            grid.innerHTML = list.map(function (s) {
                return '<div class="skill-card">' +
                    '<div class="skill-name">' + escapeHtml(s.name || '') + '</div>' +
                    '<div class="skill-desc">' + escapeHtml(s.description || s.desc || '') + '</div>' +
                    '<div class="skill-meta"><span class="tag">' + escapeHtml(s.category || s.group || '通用') + '</span></div>' +
                    '<button class="btn-primary btn-sm exec-skill-btn" data-name="' + escapeHtml(s.name || '') + '">执行</button>' +
                '</div>';
            }).join('');
            grid.querySelectorAll('.exec-skill-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var name = btn.getAttribute('data-name');
                    btn.disabled = true; btn.textContent = '执行中...';
                    call('execute_skill', name).then(function (res) {
                        btn.disabled = false; btn.textContent = '执行';
                        var box = document.getElementById('skillExecResult');
                        box.style.display = 'block';
                        document.getElementById('skillExecContent').textContent =
                            (res && (res.output || res.result || res.message)) ? (res.output || res.result || res.message) : JSON.stringify(res, null, 2);
                    }).catch(function (e) {
                        btn.disabled = false; btn.textContent = '执行';
                        showToast(e.message, 'error');
                    });
                });
            });
        }).catch(function (e) {
            var grid = document.getElementById('skillGrid');
            if (grid) grid.innerHTML = '<p class="placeholder-text">技能加载失败</p>';
        });
    }

    function loadPlugins() {
        call('list_plugins').then(function (list) {
            var grid = document.getElementById('pluginGrid');
            if (!grid) return;
            if (!list || !list.length) {
                grid.innerHTML = '<p class="placeholder-text">暂无插件（plugins/ 目录为空）</p>';
                return;
            }
            grid.innerHTML = list.map(function (p) {
                var on = !!p.enabled;
                return '<div class="plugin-card">' +
                    '<div class="plugin-head"><span class="plugin-name">' + escapeHtml(p.name || '') + '</span>' +
                    '<label class="switch-toggle ' + (on ? 'on' : '') + '" data-plugin="' + escapeHtml(p.name || '') + '"></label></div>' +
                    '<div class="plugin-desc">' + escapeHtml(p.description || p.desc || '') + '</div>' +
                    '<div class="plugin-meta">v' + escapeHtml(p.version || '?') + ' · ' + escapeHtml(p.author || '未知') + '</div>' +
                '</div>';
            }).join('');
            grid.querySelectorAll('.switch-toggle[data-plugin]').forEach(function (sw) {
                sw.addEventListener('click', function () {
                    var name = sw.getAttribute('data-plugin');
                    var turningOn = !sw.classList.contains('on');
                    var fn = turningOn ? 'enable_plugin' : 'disable_plugin';
                    call(fn, name).then(function () {
                        sw.classList.toggle('on', turningOn);
                        showToast((turningOn ? '已启用 ' : '已禁用 ') + name, 'success');
                    }).catch(function (e) { showToast(e.message, 'error'); });
                });
            });
        }).catch(function (e) {
            var grid = document.getElementById('pluginGrid');
            if (grid) grid.innerHTML = '<p class="placeholder-text">插件加载失败</p>';
        });
    }

    // ============================================================
    // 阶段四：长期记忆
    // ============================================================

    function initMemory() {
        var addBtn = document.getElementById('addMemoryBtn');
        if (addBtn) addBtn.addEventListener('click', function () {
            var type = document.getElementById('memAddType').value;
            var category = document.getElementById('memAddCategory').value.trim();
            var content = document.getElementById('memAddContent').value.trim();
            if (!content) { showToast('请输入记忆内容', 'warning'); return; }
            addBtn.disabled = true;
            call('add_memory', type, content, category).then(function () {
                addBtn.disabled = false;
                document.getElementById('memAddContent').value = '';
                showToast('已添加记忆', 'success');
                loadMemoryStats();
                loadMemories(_memFilter);
            }).catch(function (e) {
                addBtn.disabled = false;
                showToast(e.message, 'error');
            });
        });

        // 类型筛选
        document.querySelectorAll('#memFilterSwitch .range-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                document.querySelectorAll('#memFilterSwitch .range-btn').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                _memFilter = btn.getAttribute('data-mem-type');
                loadMemories(_memFilter);
            });
        });

        // 搜索
        var searchBtn = document.getElementById('memorySearchBtn');
        if (searchBtn) searchBtn.addEventListener('click', doMemorySearch);
        var searchInput = document.getElementById('memorySearchInput');
        if (searchInput) searchInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') doMemorySearch(); });
    }

    function doMemorySearch() {
        var q = document.getElementById('memorySearchInput').value.trim();
        if (!q) { loadMemories(_memFilter); return; }
        call('search_memories', q).then(function (list) {
            renderMemoryList(list);
        }).catch(function (e) { showToast(e.message, 'error'); });
    }

    function loadMemoryStats() {
        call('get_memory_stats').then(function (res) {
            res = res || {};
            setText('memTotal', res.total != null ? res.total : '--');
            setText('memActive', res.active != null ? res.active : '--');
            setText('memForgot', res.forgotten != null ? res.forgotten : '--');
            var box = document.getElementById('memoryTypeStats');
            if (box && res.by_type) {
                box.innerHTML = Object.keys(res.by_type).map(function (k) {
                    return '<span class="tag">' + escapeHtml(k) + '：' + res.by_type[k] + '</span>';
                }).join(' ');
            } else if (box) {
                box.innerHTML = '';
            }
        }).catch(function (e) { console.warn('get_memory_stats', e); });
    }

    function loadMemories(type) {
        call('get_memories', type === 'all' ? null : type).then(function (list) {
            renderMemoryList(list);
        }).catch(function (e) {
            var box = document.getElementById('memoryList');
            if (box) box.innerHTML = '<p class="placeholder-text">记忆加载失败</p>';
        });
    }

    function renderMemoryList(list) {
        var box = document.getElementById('memoryList');
        if (!box) return;
        if (!list || !list.length) {
            box.innerHTML = '<p class="placeholder-text">暂无记忆</p>';
            return;
        }
        box.innerHTML = list.map(function (m) {
            var t = m.type || 'fact';
            var tLabel = t === 'lesson' ? '教训' : (t === 'conclusion' ? '结论' : '事实');
            return '<div class="memory-card mem-' + t + '">' +
                '<div class="memory-head">' +
                    '<span class="tag mem-type-tag">' + tLabel + '</span>' +
                    (m.category ? '<span class="tag">' + escapeHtml(m.category) + '</span>' : '') +
                    '<span class="memory-time">' + escapeHtml(m.created_at || m.time || '') + '</span>' +
                '</div>' +
                '<div class="memory-content">' + escapeHtml(m.content || m.text || '') + '</div>' +
                '<div class="memory-foot">' +
                    (m.importance != null ? '<span class="memory-importance">重要性 ' + m.importance + '</span>' : '') +
                    '<button class="btn-ghost btn-sm forget-mem-btn" data-id="' + (m.id || '') + '">🗑 遗忘</button>' +
                '</div>' +
            '</div>';
        }).join('');
        box.querySelectorAll('.forget-mem-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var id = btn.getAttribute('data-id');
                if (!id) return;
                if (!confirm('确定遗忘这条记忆吗？')) return;
                call('forget_memory', id).then(function () {
                    showToast('已遗忘', 'success');
                    loadMemoryStats();
                    loadMemories(_memFilter);
                }).catch(function (e) { showToast(e.message, 'error'); });
            });
        });
    }

    // ============================================================
    // 阶段四：备份中心
    // ============================================================

    function initBackup() {
        var createBtn = document.getElementById('createBackupBtn');
        if (createBtn) createBtn.addEventListener('click', function () {
            createBtn.disabled = true; createBtn.textContent = '创建中...';
            call('create_backup').then(function (res) {
                createBtn.disabled = false; createBtn.textContent = '🆕 创建备份';
                var box = document.getElementById('backupActionResult');
                box.style.display = 'block';
                document.getElementById('backupActionText').textContent =
                    '备份成功：' + (res ? (res.path || res.id || JSON.stringify(res)) : 'OK');
                showToast('备份完成', 'success');
                loadBackupStats();
                loadBackupList();
            }).catch(function (e) {
                createBtn.disabled = false; createBtn.textContent = '🆕 创建备份';
                showToast(e.message, 'error');
            });
        });

        var exportBtn = document.getElementById('exportAllDataBtn');
        if (exportBtn) exportBtn.addEventListener('click', function () {
            exportBtn.disabled = true;
            call('export_all_data').then(function (res) {
                exportBtn.disabled = false;
                var box = document.getElementById('backupActionResult');
                box.style.display = 'block';
                document.getElementById('backupActionText').textContent =
                    '导出完成：' + (res ? (res.path || res.file || JSON.stringify(res)) : 'OK');
                showToast('导出完成', 'success');
            }).catch(function (e) {
                exportBtn.disabled = false;
                showToast(e.message, 'error');
            });
        });

        var refreshBtn = document.getElementById('refreshBackupBtn');
        if (refreshBtn) refreshBtn.addEventListener('click', loadBackupList);
    }

    function loadBackupStats() {
        call('get_backup_stats').then(function (res) {
            res = res || {};
            setText('bakTotal', res.total != null ? res.total : '--');
            setText('bakSize', res.total_size || res.size || '--');
            setText('bakLatest', res.latest_time || res.latest || '--');
        }).catch(function (e) { console.warn('get_backup_stats', e); });
    }

    function loadBackupList() {
        call('list_backups').then(function (list) {
            var tbody = document.querySelector('#backupListTable tbody');
            if (!tbody) return;
            if (!list || !list.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="placeholder-cell">暂无备份</td></tr>';
                return;
            }
            tbody.innerHTML = list.map(function (b) {
                var id = b.id != null ? b.id : (b.name || '');
                return '<tr>' +
                    '<td>' + escapeHtml(String(id)) + '</td>' +
                    '<td>' + escapeHtml(b.time || b.created_at || '--') + '</td>' +
                    '<td>' + escapeHtml(String(b.size != null ? b.size : '--')) + '</td>' +
                    '<td>' + escapeHtml(b.note || b.description || '') + '</td>' +
                    '<td class="backup-ops">' +
                        '<button class="btn-secondary btn-sm restore-btn" data-id="' + escapeHtml(String(id)) + '">恢复</button>' +
                        '<button class="btn-secondary btn-sm danger del-btn" data-id="' + escapeHtml(String(id)) + '">删除</button>' +
                    '</td>' +
                '</tr>';
            }).join('');
            tbody.querySelectorAll('.restore-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var id = btn.getAttribute('data-id');
                    if (!confirm('确定恢复备份 ' + id + ' 吗？当前数据将被覆盖！')) return;
                    call('restore_backup', id).then(function () {
                        showToast('恢复完成', 'success');
                        loadBackupStats();
                        loadBackupList();
                    }).catch(function (e) { showToast(e.message, 'error'); });
                });
            });
            tbody.querySelectorAll('.del-btn').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var id = btn.getAttribute('data-id');
                    if (!confirm('确定删除备份 ' + id + ' 吗？')) return;
                    // 走 list_backups 对应删除：复用 restore 同款 call；后端若提供 delete_backup 可替换
                    showToast('删除接口待后端提供', 'warning');
                });
            });
        }).catch(function (e) {
            var tbody = document.querySelector('#backupListTable tbody');
            if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="placeholder-cell">备份列表加载失败</td></tr>';
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
        // 阶段三初始化
        initTwin();
        initTasks();
        initGuide();
        initFinance();
        initProfileEnhancements();
        initConsultEnhancements();
        // 阶段四初始化
        initStats();
        initVoice();
        initOCR();
        initPlugins();
        initMemory();
        initBackup();

        // bridgeReady 后加载所有数据
        window.addEventListener('bridgeReady', function () {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
            loadMedalStats();
            loadPreferences();
            loadPhotos();
        });

        // 如果 bridge 已经 ready（mock 模式下立即触发）
        if (window.bridge && window.bridge.ready && window.bridge.ready()) {
            loadConfig();
            loadPersons();
            loadAffection();
            loadTasks();
            loadMedals();
            loadMedalStats();
            loadPreferences();
            loadPhotos();
        }
    });

    // ========== 代理功能 ==========

    function testProxy() {
        var statusEl = document.getElementById('proxyStatus');
        if (statusEl) statusEl.textContent = '测试中...';
        call('test_proxy').then(function (r) {
            if (statusEl) {
                if (r.ok) {
                    statusEl.textContent = '✓ 代理可用，延迟 ' + (r.response_time || '?') + 's';
                    statusEl.style.color = 'var(--success-color)';
                } else {
                    statusEl.textContent = '✗ 代理失败: ' + (r.error || '未知错误');
                    statusEl.style.color = 'var(--danger-color)';
                }
            }
        }).catch(function (e) {
            if (statusEl) {
                statusEl.textContent = '✗ 测试异常: ' + e;
                statusEl.style.color = 'var(--danger-color)';
            }
        });
    }

    function enableSystemProxy() {
        call('enable_system_proxy').then(function (r) {
            showToast(r.ok ? '系统代理已启用' : '启用失败: ' + (r.error || ''));
        });
    }

    function disableSystemProxy() {
        call('disable_system_proxy').then(function (r) {
            showToast(r.ok ? '系统代理已关闭' : '关闭失败: ' + (r.error || ''));
        });
    }

    // 暴露到全局
    window.testProxy = testProxy;
    window.enableSystemProxy = enableSystemProxy;
    window.disableSystemProxy = disableSystemProxy;

})();
