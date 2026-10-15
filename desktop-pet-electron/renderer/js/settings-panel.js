/**
 * settings-panel.js
 * 设置面板：从托盘菜单点"设置"弹出，叠加在桌宠窗口上。
 * 可调：桌宠大小、音量、置顶、开机启动、角色切换。
 * 窗口只有 300x420，面板小而精致。
 */

export class SettingsPanel {
  /**
   * @param {object} opts
   * @param {HTMLElement} opts.container   挂载到哪个 DOM 元素
   * @param {object} opts.settings          当前设置对象
   * @param {(key: string, value: any) => void} opts.onSettingChange  设置变更回调
   * @param {() => void} [opts.onClose]    关闭回调
   * @param {() => void} [opts.onReload]   角色切换后需要重载页面
   */
  constructor({ container, settings, onSettingChange, onClose = () => {}, onReload = () => {} }) {
    this.container = container;
    this.settings = settings || {};
    this._onSettingChange = onSettingChange;
    this._onClose = onClose;
    this._onReload = onReload;
    this._visible = false;
    this._el = null;

    this._buildDOM();
  }

  /** 构建面板 DOM */
  _buildDOM() {
    const el = document.createElement('div');
    el.id = 'settings-panel';
    el.className = 'settings-panel hidden';
    el.innerHTML = `
      <div class="sp-header">
        <span class="sp-title">桌宠设置</span>
        <button class="sp-close" title="关闭">×</button>
      </div>
      <div class="sp-body">
        <!-- 桌宠大小 -->
        <div class="sp-row">
          <label class="sp-label">大小</label>
          <div class="sp-slider-wrap">
            <input type="range" class="sp-slider" id="sp-size" min="0.08" max="0.30" step="0.01" value="${this.settings.petScale ?? 0.15}">
            <span class="sp-val" id="sp-size-val">${Math.round((this.settings.petScale ?? 0.15) * 100) / 100}</span>
          </div>
        </div>
        <!-- 音量 -->
        <div class="sp-row">
          <label class="sp-label">音量</label>
          <div class="sp-slider-wrap">
            <input type="range" class="sp-slider" id="sp-volume" min="0" max="1" step="0.1" value="${this.settings.volume ?? 0.5}">
            <span class="sp-val" id="sp-volume-val">${this.settings.volume ?? 0.5}</span>
          </div>
        </div>
        <!-- 置顶开关 -->
        <div class="sp-row">
          <label class="sp-label">窗口置顶</label>
          <label class="sp-switch">
            <input type="checkbox" id="sp-topmost" ${this.settings.alwaysOnTop ? 'checked' : ''}>
            <span class="sp-slider-switch"></span>
          </label>
        </div>
        <!-- 开机启动 -->
        <div class="sp-row">
          <label class="sp-label">开机启动</label>
          <label class="sp-switch">
            <input type="checkbox" id="sp-autolaunch" ${this.settings.autoLaunch ? 'checked' : ''}>
            <span class="sp-slider-switch"></span>
          </label>
        </div>
        <!-- 角色切换 -->
        <div class="sp-row">
          <label class="sp-label">角色</label>
          <select class="sp-select" id="sp-character">
            <option value="cybergf" ${(this.settings.currentCharacter || 'cybergf') === 'cybergf' ? 'selected' : ''}>合欢宗小妖女</option>
            <option value="haru" ${this.settings.currentCharacter === 'haru' ? 'selected' : ''}>Haru</option>
            <option value="placeholder" ${this.settings.currentCharacter === 'placeholder' ? 'selected' : ''}>小团（占位）</option>
          </select>
        </div>

        <!-- 实时数字人（实验）：可勾选模块，默认 off=2D 桌宠 -->
        <div class="sp-dh-section">
          <div class="sp-dh-title">实时数字人（实验）</div>
          <div class="sp-row">
            <label class="sp-label">启用</label>
            <label class="sp-switch">
              <input type="checkbox" id="sp-dh-enabled">
              <span class="sp-slider-switch"></span>
            </label>
          </div>
          <div class="sp-dh-radio-row">
            <label><input type="radio" name="sp-dh-mode" value="local" checked> 本地驱动</label>
            <label><input type="radio" name="sp-dh-mode" value="cloud"> 云端数字人</label>
          </div>
          <div class="sp-dh-hint hidden" id="sp-dh-hint"></div>
          <div class="sp-dh-hint" style="color:#9aa" id="sp-dh-restart-tip">保存后重启桌宠生效</div>
        </div>
      </div>
    `;
    this.container.appendChild(el);
    this._el = el;

    // 绑定事件
    this._bindEvents();
  }

  _bindEvents() {
    // 关闭按钮
    this._el.querySelector('.sp-close').addEventListener('click', () => this.hide());

    // 点击面板外部关闭
    this._outsideHandler = (e) => {
      if (this._visible && !this._el.contains(e.target)) {
        this.hide();
      }
    };
    document.addEventListener('mousedown', this._outsideHandler);

    // 桌宠大小滑块
    const sizeSlider = this._el.querySelector('#sp-size');
    sizeSlider.addEventListener('input', (e) => {
      const v = parseFloat(e.target.value);
      this._el.querySelector('#sp-size-val').textContent = v.toFixed(2);
      this._onSettingChange('petScale', v);
    });

    // 音量滑块
    const volSlider = this._el.querySelector('#sp-volume');
    volSlider.addEventListener('input', (e) => {
      const v = parseFloat(e.target.value);
      this._el.querySelector('#sp-volume-val').textContent = v.toFixed(1);
      this._onSettingChange('volume', v);
    });

    // 置顶开关
    this._el.querySelector('#sp-topmost').addEventListener('change', (e) => {
      this._onSettingChange('alwaysOnTop', e.target.checked);
    });

    // 开机启动
    this._el.querySelector('#sp-autolaunch').addEventListener('change', (e) => {
      this._onSettingChange('autoLaunch', e.target.checked);
    });

    // 角色切换
    this._el.querySelector('#sp-character').addEventListener('change', (e) => {
      this._onSettingChange('currentCharacter', e.target.value);
      // 切换角色后需要重载页面
      setTimeout(() => this._onReload(), 200);
    });

    // ---- 实时数字人（实验）区块 ----
    this._bindDigitalHuman();
  }

  /** 数字人区块：读现有配置回显 + 保存写 digital-human/config.json（重启生效） */
  _bindDigitalHuman() {
    const enabledEl = this._el.querySelector('#sp-dh-enabled');
    const hintEl = this._el.querySelector('#sp-dh-hint');
    if (!enabledEl) return;

    let dhCfg = { mode: 'off' };
    let gpu = null;

    // 回显当前配置
    if (window.petAPI && window.petAPI.dhGetConfig) {
      window.petAPI.dhGetConfig().then((cfg) => {
        dhCfg = cfg || { mode: 'off' };
        const on = dhCfg.mode && dhCfg.mode !== 'off';
        enabledEl.checked = !!on;
        const radio = this._el.querySelector('input[name="sp-dh-mode"][value="' + (dhCfg.mode === 'cloud' ? 'cloud' : 'local') + '"]');
        if (radio) radio.checked = true;
        this._refreshDhHint(hintEl, gpu, on, dhCfg.mode);
      }).catch(() => {});
    }
    if (window.petAPI && window.petAPI.dhGetGPU) {
      window.petAPI.dhGetGPU().then((g) => {
        gpu = g;
        this._refreshDhHint(hintEl, gpu, enabledEl.checked, dhCfg.mode);
      }).catch(() => {});
    }

    // 勾选/切模式即时刷新提示
    enabledEl.addEventListener('change', () => {
      const mode = this._selectedDhMode();
      this._refreshDhHint(hintEl, gpu, enabledEl.checked, mode);
      this._saveDh();
    });
    this._el.querySelectorAll('input[name="sp-dh-mode"]').forEach((r) => {
      r.addEventListener('change', () => {
        this._refreshDhHint(hintEl, gpu, enabledEl.checked, this._selectedDhMode());
        this._saveDh();
      });
    });
  }

  _selectedDhMode() {
    const r = this._el.querySelector('input[name="sp-dh-mode"]:checked');
    return r ? r.value : 'local';
  }

  /** 1050 档提示：选 local 且显存<6G → 逐字「本地帧率低、建议云端」 */
  _refreshDhHint(hintEl, gpu, enabled, mode) {
    if (!hintEl) return;
    if (!enabled) { hintEl.classList.add('hidden'); hintEl.textContent = ''; return; }
    if (mode === 'local' && gpu && gpu.tier === 'low') {
      hintEl.textContent = gpu.hint || '本地帧率低、建议云端';
      hintEl.classList.remove('hidden');
    } else if (mode === 'local' && gpu && gpu.tier === 'none') {
      hintEl.textContent = gpu.hint || '未检测到NVIDIA显卡，建议使用云端数字人或保持2D';
      hintEl.classList.remove('hidden');
    } else {
      hintEl.classList.add('hidden');
    }
  }

  /** 保存到 digital-human/config.json（主进程落盘；契约要求重启生效） */
  _saveDh() {
    const enabled = this._el.querySelector('#sp-dh-enabled').checked;
    const mode = enabled ? this._selectedDhMode() : 'off';
    if (window.petAPI && window.petAPI.dhSetConfig) {
      window.petAPI.dhSetConfig({ mode }).then((r) => {
        console.log('[dh-settings] mode =', mode, '（重启生效）');
      }).catch((e) => console.warn('[dh-settings] 保存失败:', e));
    }
  }

  show() {
    this._visible = true;
    this._el.classList.remove('hidden');
    // 触发动画
    void this._el.offsetWidth;
    this._el.classList.add('sp-show');
  }

  hide() {
    this._visible = false;
    this._el.classList.add('sp-hiding');
    setTimeout(() => {
      this._el.classList.add('hidden');
      this._el.classList.remove('sp-show', 'sp-hiding');
    }, 200);
    this._onClose();
  }

  isVisible() {
    return this._visible;
  }

  destroy() {
    document.removeEventListener('mousedown', this._outsideHandler);
    if (this._el && this._el.parentNode) {
      this._el.parentNode.removeChild(this._el);
    }
  }
}
