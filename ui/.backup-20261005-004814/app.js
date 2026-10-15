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

    // ============ 聊天分析 ============

    function initChatAnalysis() {
        var btn = document.getElementById('analyzeBtn');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var text = document.getElementById('chatInput').value.trim();
            if (!text) { showToast('请先粘贴对话内容', 'error'); return; }
            btn.disabled = true;
            btn.textContent = '分析中...';
            call('analyze_chat', text, null).then(function (result) {
                renderAnalysis(result);
            }).catch(function (e) {
                showToast('分析失败: ' + e.message, 'error');
            }).finally(function () {
                btn.disabled = false;
                btn.textContent = '开始分析';
            });
        });
    }

    function renderAnalysis(result) {
        var container = document.getElementById('analysisResult');
        if (!result) { container.innerHTML = '<p class="placeholder-text">无结果</p>'; return; }
        var modules = [
            '情绪分析', '关系阶段', '好感度变化', '回复建议',
            '风险预警', '话题建议', '行为倾向', '心理学侧写更新'
        ];
        var html = '';
        modules.forEach(function (name, idx) {
            var key = 'module_' + idx;
            var content = result[key] || '等待分析...';
            html += '<div class="analysis-module">' +
                '<div class="module-header"><span>' + name + '</span><span>▾</span></div>' +
                '<div class="module-body">' + content + '</div>' +
            '</div>';
        });
        container.innerHTML = html;

        // 折叠交互
        container.querySelectorAll('.module-header').forEach(function (h) {
            h.addEventListener('click', function () {
                h.parentElement.classList.toggle('expanded');
            });
        });
    }

    // ============ 信号监听（bridge:xxx 自定义事件） ============

    function initSignalListeners() {
        window.addEventListener('bridge:new_message', function (e) {
            var data = e.detail || {};
            showToast('💬 新消息: ' + (data.sender || ''), 'info');
            // 顶部头像闪烁
            var av = document.getElementById('topAvatar');
            if (av) { av.classList.add('animate-pulse'); setTimeout(function(){ av.classList.remove('animate-pulse'); }, 2000); }
        });

        window.addEventListener('bridge:affection_changed', function (e) {
            var data = e.detail || {};
            if (data.total != null) {
                drawAffectionRing(document.getElementById('topAffectionRing'), data.total);
            }
            if (data.dimensions) {
                window._radarScores = data.dimensions;
                drawRadarChart(document.getElementById('radarChartCanvas'), data.dimensions);
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
            loadAffection();
        });

        window.addEventListener('bridge:analysis_ready', function (e) {
            renderAnalysis(e.detail);
        });

        window.addEventListener('bridge:wechat_status', function (e) {
            var s = e.detail || {};
            showToast('微信状态: ' + (s.connected ? '已连接' : '断开'));
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
