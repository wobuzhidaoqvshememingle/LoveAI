/**
 * app.js
 * 主入口：初始化 PixiJS、加载角色、组装状态机/气泡/情绪/交互/WebSocket/设置面板。
 * 成品级：支持新 WS 事件格式、自然口型、丰富 idle 行为、关系阶段标签、设置面板。
 */

import { CharacterModel } from './model-loader.js';
import { StateMachine, PetState } from './state-machine.js';
import { Bubble } from './bubble.js';
import { EmotionSystem } from './emotion.js';
import { Interaction } from './interaction.js';
import { WsClient } from './ws-client.js';
import { SettingsPanel } from './settings-panel.js';
import { Atmosphere } from './atmosphere.js';
import { VoiceInput } from './voice-input.js';
import { AudioPlayer } from './audio-player.js';
import { DigitalHumanView } from './dh-view.js';

/* ==================== 全局状态 ==================== */
let app = null;            // PIXI.Application
let character = null;     // CharacterModel
let stateMachine = null;
let bubble = null;
let emotion = null;
let interaction = null;
let wsClient = null;
let settings = null;
let characterConfig = null;
let settingsPanel = null;
let relationshipTagEl = null;
let atmosphere = null;

// 语音双向（本任务新增）
let voiceInput = null;     // VoiceInput 实例
let audioPlayer = null;    // AudioPlayer 实例
let audioAmp = 0;          // 由 AudioPlayer 振幅驱动的口型 0..1
let _voiceBallSetState = null;  // 语音球状态设置函数（boot 内赋值）

// 实时数字人（可勾选模块，默认 off=2D）
let dhView = null;          // DigitalHumanView 实例

// 沉浸式元素提升到模块级，供 handleStateChange / handleWsMessage 使用
let isImmersive = false;
let voiceBall = null;
let subtitleText = null;
let immersiveStatus = null;

// 说话口型状态
let speakingMouthT = 0;       // 说话时嘴巴相位
let mouthPatternTimer = 0;    // 口型模式切换计时器
let mouthPattern = { speed: 8, amplitude: 0.8, pause: false }; // 当前口型模式

// idle 行为状态
let idleTimer = 0;            // 累计 idle 时间（秒）
let lastInteractionTime = 0;  // 最后交互时间戳
let headTiltState = { active: false, angle: 0, target: 0, timer: 0 }; // 歪头
let swayState = { phase: 0, amplitude: 0 }; // 左右晃动
let mouseLeaveTimer = 0;      // 鼠标离开后眼神回正计时

/* ==================== 启动 ==================== */
async function boot() {
  // 1. 读取设置（主进程通过 preload 暴露）
  try {
    settings = (window.petAPI && await window.petAPI.getSettings()) || {};
  } catch (e) {
    console.warn('[app] 读取设置失败，使用默认值', e);
    settings = {};
  }

  // 2. 读取角色配置
  characterConfig = await loadCharacterConfig();
  if (!characterConfig) {
    console.error('[app] 角色配置加载失败');
    return;
  }

  // 3. 初始化 PixiJS（透明背景）
  // 角落模式 400x600；沉浸式模式下会由 resize 重新铺满窗口
  const stageEl = document.getElementById('stage');
  const CORNER_W = 400, CORNER_H = 600;
  app = new PIXI.Application({
    width: CORNER_W,
    height: CORNER_H,
    transparent: true,                 // 关键：透明
    antialias: true,
    resolution: Math.min(window.devicePixelRatio || 1, 2),
    autoDensity: true,
    backgroundAlpha: 0,
    resizeTo: null,
  });
  stageEl.appendChild(app.view);

  // 4. 初始化各子系统
  stateMachine = new StateMachine();
  bubble = new Bubble(document.getElementById('bubble'));
  emotion = new EmotionSystem(settings.behavior || {});
  relationshipTagEl = document.getElementById('relationship-tag');

  // 赛博氛围层（先加，在角色下层）
  atmosphere = new Atmosphere(app.stage);
  atmosphere.resize(CORNER_W, CORNER_H);

  character = new CharacterModel(app.stage);
  character.setViewport(CORNER_W, CORNER_H, { alignX: 'center', bottomMargin: 10 });
  await character.load(characterConfig);
  // 暴露到 window 方便外部脚本调试/截图
  window.__character = character;
  window.__app = app;

  // 应用启动时的设置：模型大小（slider 倍率，1.0 = 适配后的原始大小）
  const savedScale = settings.petScale || 1.0;
  if (savedScale !== 1.0) character.setScale(savedScale);

  // 应用启动时的设置：窗口置顶
  if (settings.alwaysOnTop && window.petAPI && typeof window.petAPI.setAlwaysOnTop === 'function') {
    window.petAPI.setAlwaysOnTop(true);
  }

  interaction = new Interaction({
    canvasEl: app.view,
    model: character,
    stateMachine,
    emotion,
    bubble,
    characterConfig,
    // 点击随机到 IDLE（无表情动作）时，给个轻微弹跳保证有反馈
    onTap: () => {
      if (stateMachine.getState() === PetState.IDLE) character.pulse(0.06);
    },
    // R3.3-76：拖动结束 -> 读窗口最新坐标 -> 上行共享配置（双端一致）
    onDragEnd: () => {
      uplinkWindowPosition();
    },
  });

  // 5. 创建设置面板
  settingsPanel = new SettingsPanel({
    container: document.getElementById('settings-mount'),
    settings,
    onSettingChange: handleSettingChange,
    onReload: () => window.location.reload(),
  });

  // 6. WebSocket（连不上也不阻塞）
  wsClient = new WsClient({
    url: (settings.ws && settings.ws.url) || 'ws://localhost:9876',
    reconnectInterval: (settings.ws && settings.ws.reconnectInterval) || 3000,
    onMessage: handleWsMessage,
    onStatus: (connected) => {
      // R3.3-76：连接建立后报到桌宠版本号，供主程序版本对齐校验
      if (connected && wsClient && typeof wsClient.sendHello === 'function') {
        const v = (window.petAPI && typeof window.petAPI.getVersion === 'function')
          ? window.petAPI.getVersion() : '0.0.0';
        wsClient.sendHello(v);
      }
    },
  });
  wsClient.connect();

  // 7. 订阅状态变化 → 驱动表情
  stateMachine.onChange((oldS, newS, params) => {
    handleStateChange(oldS, newS, params);
  });

  // 8. 订阅托盘命令
  if (window.petAPI && typeof window.petAPI.onTrayCommand === 'function') {
    window.petAPI.onTrayCommand((cmd) => handleTrayCommand(cmd));
  }

  // 9. 订阅设置变化
  if (window.petAPI && typeof window.petAPI.onSettingsChanged === 'function') {
    window.petAPI.onSettingsChanged((s) => {
      settings = s || settings;
    });
  }

  // 9.5 沉浸式模式
  const immersivePortrait = document.getElementById('immersive-portrait');
  voiceBall = document.getElementById('voice-ball');
  const subtitleBar = document.getElementById('subtitle-bar');
  subtitleText = document.getElementById('subtitle-text');
  const exitBtn = document.getElementById('exit-immersive');
  immersiveStatus = document.getElementById('immersive-status');

  function setImmersive(on) {
    isImmersive = on;
    document.body.classList.toggle('immersive-mode', on);
    document.body.classList.toggle('corner-mode', !on);
    immersivePortrait.classList.toggle('hidden', !on);
    voiceBall.classList.toggle('hidden', !on);
    subtitleBar.classList.toggle('hidden', !on);
    exitBtn.classList.toggle('hidden', !on);
    immersiveStatus.classList.toggle('hidden', !on);
    if (window.petAPI && window.petAPI.setImmersive) {
      window.petAPI.setImmersive(on);
    }
    // 等主进程把窗口尺寸改完后，重设 PIXI 画布大小并重新适配模型
    setTimeout(() => {
      if (!app) return;
      const w = window.innerWidth;
      const h = window.innerHeight;
      app.renderer.resize(w, h);
      stageEl.style.width = w + 'px';
      stageEl.style.height = h + 'px';
      if (atmosphere) atmosphere.resize(w, h);
      if (character) {
        if (on) {
          // 沉浸式：模型放大到 ~85% 视口高度，偏右显示（左侧留给语音球）
          // bottomMargin 80 给底部字幕条留空间
          character.setViewport(w, h, { alignX: 'right', bottomMargin: 80 });
        } else {
          // 角落模式
          character.setViewport(CORNER_W, CORNER_H, { alignX: 'center', bottomMargin: 10 });
        }
      }
    }, 120);
  }

  // 退出按钮
  exitBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    setImmersive(false);
  });

  // 主进程通知沉浸式变化（托盘菜单触发）
  if (window.petAPI && window.petAPI.onImmersiveChanged) {
    window.petAPI.onImmersiveChanged((on) => {
      if (on !== isImmersive) setImmersive(on);
    });
  }

  // 双击切换沉浸式模式
  let lastClickTime = 0;
  document.getElementById('stage').addEventListener('dblclick', () => {
    setImmersive(!isImmersive);
  });

  // 9.7 语音输入 + AI 语音播放（本任务核心）
  // 语音球状态样式切换：idle / recording / processing / speaking
  function voiceBallSetState(st) {
    if (!voiceBall) return;
    voiceBall.classList.remove('recording', 'processing', 'speaking');
    if (st === 'recording') voiceBall.classList.add('recording');
    else if (st === 'processing') voiceBall.classList.add('processing');
    else if (st === 'speaking') voiceBall.classList.add('speaking');
  }
  _voiceBallSetState = voiceBallSetState;

  audioPlayer = new AudioPlayer({
    onEnded: () => {
      audioAmp = 0;
      if (stateMachine && stateMachine.getState() === PetState.SPEAKING) {
        stateMachine.setState(PetState.IDLE);
      }
      voiceBallSetState('idle');
      if (immersiveStatus) immersiveStatus.textContent = '正在聆听...';
    },
    // 数字人模式：把 audio-player 算好的同一个 RMS 振幅转发给本地驱动 WS
    // （不另起第二套 TTS/音频分析链路）
    onAmp: (amp) => {
      if (dhView && dhView.isActive()) dhView.sendAmplitude(amp);
    },
  });

  // 实时数字人视图（off=不接管画面；local=连 WS 收 frame 画到 canvas）
  dhView = new DigitalHumanView({
    canvasEl: document.getElementById('dh-canvas'),
    badgeEl: document.getElementById('dh-badge'),
    stageEl: document.getElementById('stage'),
  });
  window.__dhView = dhView;  // 调试/CDP 检视用
  dhView.init();

  voiceInput = new VoiceInput({
    sendControl: (obj) => wsClient.send(obj),
    sendBinary: (buf) => wsClient.sendBinary(buf),
    onResult: (text, source) => {
      // 仅键盘降级路径会走到这里；真录音由后端转写后经 asr_result 回显
      const who = source === 'voice' ? '你说' : '你输入';
      bubble.show(who + '：' + text, 3500);
      if (isImmersive && subtitleText) subtitleText.textContent = who + '：' + text;
      wsClient.send({ event: 'user_text', text });
      voiceBallSetState('processing');
      if (immersiveStatus) immersiveStatus.textContent = '思考中...';
    },
    onStateChange: (st) => {
      voiceBallSetState(st === 'recording' ? 'recording' : 'idle');
      if (immersiveStatus) immersiveStatus.textContent = st === 'recording' ? '正在聆听...' : '正在聆听...';
    },
    onUnsupported: () => {
      console.warn('[app] 麦克风不可用，已降级为 prompt 键盘输入');
      if (immersiveStatus) immersiveStatus.textContent = '（点我输入文字）';
    },
  });

  // 点击中间语音球 → 开始一次输入
  voiceBall.addEventListener('click', (e) => {
    e.stopPropagation();
    voiceInput.start();
  });

  // 9.6 连续帧动画（R3.3-82）：
  // 旧的 5 张 PNG 跳切（base/blink/talk1/talk2/tilt → 已隐藏的 #portrait-img）已移除；
  // 嘴型/眨眼/呼吸/表情全部由 PIXI 层 ImageCharacter 内的 PetFrameAnimator 驱动，
  // 本函数保留为空以便主循环结构不变。
  function updateFrameAnimation(dt) { /* 连续帧已移入 ImageCharacter，此处不再操作 DOM 立绘 */ }

  // 10. 主循环
  app.ticker.add((ticker) => {
    const dt = ticker.deltaMS / 1000 || 0.016;
    atmosphere.update(dt);
    character.update(dt);
    character.updatePulse(dt);
    updateFrameAnimation(dt);

    const curState = stateMachine.getState();

    // ===== 口型同步（SPEAKING 状态，或音频正在播放） =====
    const audioPlaying = audioPlayer && audioPlayer.isPlaying();
    if (curState === PetState.SPEAKING || audioPlaying) {
      // 优先用真实音频振幅驱动口型；没有音频时退回随机自然口型
      if (audioPlaying) {
        audioAmp = audioPlayer.sample();
        // 振幅映射到嘴巴开合，留一点下限避免完全闭上
        character.setMouthOpen(Math.min(1, 0.1 + audioAmp * 1.2));
      } else {
        updateSpeakingMouth(dt);
      }
    } else {
      character.setMouthOpen(0);
      speakingMouthT = 0;
    }

    // ===== idle 行为丰富化 =====
    if (curState === PetState.IDLE) {
      idleTimer += dt;
      updateIdleBehaviors(dt);

      // 60 秒无交互进入睡眠
      if (idleTimer > 60 && !stateMachine._sleeping) {
        stateMachine._sleeping = true;
        stateMachine.setState(PetState.SLEEPING);
        bubble.show('Zzz...', 999999); // 睡眠气泡持续显示
      }
    } else if (curState === PetState.SLEEPING) {
      // 睡眠状态下的呼吸更慢
      idleTimer += dt;
    } else {
      // 有动作时重置 idle 计时
      idleTimer = 0;
      stateMachine._sleeping = false;
      if (curState !== PetState.DRAGGING) {
        lastInteractionTime = performance.now();
      }
    }

    // ===== 鼠标离开后眼神回正 =====
    if (curState === PetState.IDLE || curState === PetState.SLEEPING) {
      mouseLeaveTimer += dt;
      // 鼠标 3 秒没动，眼神逐渐回正
      if (mouseLeaveTimer > 3) {
        character.setLookTarget(0, 0);
      }
    }
  });

  // 鼠标移动时重置鼠标离开计时
  window.addEventListener('mousemove', () => {
    mouseLeaveTimer = 0;
  });

  window.addEventListener('resize', () => {
    if (interaction) interaction.resize();
    if (!app) return;
    const w = window.innerWidth;
    const h = window.innerHeight;
    app.renderer.resize(w, h);
    stageEl.style.width = w + 'px';
    stageEl.style.height = h + 'px';
    if (atmosphere) atmosphere.resize(w, h);
    if (character) {
      if (isImmersive) {
        character.setViewport(w, h, { alignX: 'right', bottomMargin: 80 });
      } else {
        character.setViewport(CORNER_W, CORNER_H, { alignX: 'center', bottomMargin: 10 });
      }
    }
  });

  console.log('[app] 桌宠启动完成，当前角色:', characterConfig.name, '缩放:', savedScale);
}

/* ==================== 说话口型自然化 ==================== */
function updateSpeakingMouth(dt) {
  mouthPatternTimer -= dt;

  // 每 0.2~0.5 秒随机切换一次口型模式
  if (mouthPatternTimer <= 0) {
    mouthPatternTimer = 0.2 + Math.random() * 0.3;
    // 随机选择模式：快说 / 慢说 / 停顿
    const r = Math.random();
    if (r < 0.15) {
      // 停顿一下
      mouthPattern = { speed: 0, amplitude: 0.1, pause: true };
    } else if (r < 0.55) {
      // 快说
      mouthPattern = { speed: 10 + Math.random() * 4, amplitude: 0.7 + Math.random() * 0.3, pause: false };
    } else {
      // 慢说
      mouthPattern = { speed: 5 + Math.random() * 3, amplitude: 0.5 + Math.random() * 0.3, pause: false };
    }
  }

  if (mouthPattern.pause) {
    // 停顿：嘴巴微张不动
    character.setMouthOpen(mouthPattern.amplitude * 0.3);
    return;
  }

  speakingMouthT += dt * mouthPattern.speed;
  // 用正弦波 + 高次谐波叠加，让张合不那么死板
  const s1 = (Math.sin(speakingMouthT) + 1) / 2;
  const s2 = (Math.sin(speakingMouthT * 2.7 + 1.3) + 1) / 2;
  let open = s1 * 0.7 + s2 * 0.3;
  // 加一点随机抖动
  open += (Math.random() - 0.5) * 0.08;
  open = Math.max(0, Math.min(1, open));
  character.setMouthOpen(0.15 + open * mouthPattern.amplitude);
}

/* ==================== idle 行为 ==================== */
function updateIdleBehaviors(dt) {
  // 偶尔轻轻歪头（角度随机 ±5度 ≈ ±0.087弧度），2秒恢复
  if (!headTiltState.active && Math.random() < dt * 0.08) {
    // 约每 12 秒触发一次
    headTiltState.active = true;
    headTiltState.target = (Math.random() - 0.5) * 0.15; // ±0.075 弧度 ≈ ±4.3度
    headTiltState.timer = 0;
  }
  if (headTiltState.active) {
    headTiltState.timer += dt;
    // 0~1秒歪过去，1~2秒恢复
    if (headTiltState.timer < 1) {
      headTiltState.angle += (headTiltState.target - headTiltState.angle) * dt * 4;
    } else {
      headTiltState.angle += (0 - headTiltState.angle) * dt * 3;
      if (headTiltState.timer > 2 && Math.abs(headTiltState.angle) < 0.005) {
        headTiltState.active = false;
        headTiltState.angle = 0;
      }
    }
    character.setRotation(headTiltState.angle);
  }

  // 小幅度左右晃动（叠加在呼吸上）
  swayState.phase += dt * 1.5;
  swayState.amplitude = 3; // 像素
  const swayX = Math.sin(swayState.phase) * swayState.amplitude;
  character.setOffsetX(swayX);
}

/* ==================== 工具 ==================== */
async function loadCharacterConfig() {
  try {
    const resp = await fetch('../config/characters.json');
    if (!resp.ok) throw new Error('http ' + resp.status);
    const all = await resp.json();
    // 优先用 settings 里指定的角色
    const curId = (settings && settings.currentCharacter) || all.current || (all.characters && all.characters[0].id);
    const found = (all.characters || []).find(c => c.id === curId) || all.characters[0];
    return found;
  } catch (e) {
    console.warn('[app] 读取 characters.json 失败，使用内置占位配置', e);
    return {
      id: 'placeholder',
      name: '小团',
      modelPath: '',
      tapLines: ['嗯？', '怎么啦~'],
      idleTalk: ['发呆中...'],
      motionGroups: {},
    };
  }
}

/* ==================== 状态变化 → 表情 ==================== */
function handleStateChange(oldS, newS, params = {}) {
  // 从睡眠醒来时，隐藏 Zzz 气泡
  if (oldS === PetState.SLEEPING && newS !== PetState.SLEEPING) {
    bubble.hide();
  }

  switch (newS) {
    case PetState.IDLE:
      character.setExpression('idle');
      character.setRotation(0);
      voiceBall.classList.remove('speaking');
      if (immersiveStatus) immersiveStatus.textContent = '正在聆听...';
      break;
    case PetState.THINKING:
      character.setExpression('thinking');
      voiceBall.classList.remove('speaking');
      if (immersiveStatus) immersiveStatus.textContent = '思考中...';
      break;
    case PetState.SPEAKING:
      character.setExpression('idle');
      speakingMouthT = 0;
      mouthPatternTimer = 0;
      voiceBall.classList.add('speaking');
      if (immersiveStatus) immersiveStatus.textContent = '正在说话...';
      break;
    case PetState.HAPPY:
      character.setExpression('happy');
      character.pulse(0.09);          // 开心时Q弹一下
      voiceBall.classList.remove('speaking');
      break;
    case PetState.SURPRISED:
      character.setExpression('surprised');
      character.pulse(0.05);          // 惊讶时轻微一顿
      voiceBall.classList.remove('speaking');
      break;
    case PetState.SLEEPING:
      character.setExpression('sleeping');
      voiceBall.classList.remove('speaking');
      break;
    case PetState.DRAGGING:
      character.setExpression('surprised');
      break;
  }
}

/* ==================== WebSocket 消息处理 ==================== */
function handleWsMessage(type, data) {
  switch (type) {
    // 思考事件
    case 'thinking':
      stateMachine.setState(PetState.THINKING);
      if (data.text) {
        bubble.show(data.text, 3000);
        if (isImmersive && subtitleText) subtitleText.textContent = data.text;
      } else {
        bubble.show('...');
      }
      break;

    // 说话事件
    case 'speaking':
      stateMachine.setState(PetState.SPEAKING);
      if (data.text) {
        bubble.show(data.text, 4000);
        if (isImmersive && subtitleText) subtitleText.textContent = data.text;
      }
      // 说话时附带情绪
      if (data.emotion) {
        applyEmotion(data.emotion);
      }
      // 说话时附带关系阶段
      if (data.relationship_stage) {
        showRelationshipTag(data.relationship_stage);
      }
      break;

    // 情绪事件
    case 'emotion': {
      // R3.3-78：update 前后比对快照，判定本轮是忠诚涨还是好感涨，给不同桌宠表现
      const prevSnap = emotion.snapshot();
      emotion.update(data);
      const nextSnap = emotion.snapshot();
      // 如果 emotion 字段存在，切换表情
      if (data.emotion) {
        applyEmotion(data.emotion);
      }
      // 双轨表现分流：显式 metric 标记优先；其次看 detectRise 谁在涨
      const rise = emotion.detectRise(prevSnap, nextSnap);
      const tag = data.metric || rise.metric;
      if (tag === 'loyalty') {
        feedbackLoyaltyRise();   // 反制线：顺从姿态 + 顺从文案
      } else if (tag === 'affection') {
        feedbackAffectionRise(); // 引导线：亲昵姿态 + 亲昵文案
      }
      break;
    }

    // 关系阶段（单独作为字段推送时）
    case 'relationship_stage':
      if (data.relationship_stage) {
        showRelationshipTag(data.relationship_stage);
      } else if (typeof data === 'string') {
        showRelationshipTag(data);
      }
      break;

    // 连接确认
    case 'hello':
      console.log('[ws] LoveAI 握手成功:', data);
      break;

    // ---- 本任务新增：双向语音协议 ----
    // AI 说话：播放音频 + 显示文字
    case 'ai_speak': {
      stateMachine.setState(PetState.SPEAKING);
      const text = data.text || '';
      if (text) {
        bubble.show(text, 60000); // 长时间显示，音频结束后由 onEnded 回到 idle
        if (isImmersive && subtitleText) subtitleText.textContent = text;
      }
      if (data.emotion) applyEmotion(data.emotion);
      if (immersiveStatus) immersiveStatus.textContent = '正在说话...';
      // 播放音频（没有 audio_url 也不报错，纯文字）
      if (audioPlayer) {
        audioPlayer.play(data.audio_url);
      }
      _voiceBallSetState && _voiceBallSetState(data.audio_url ? 'speaking' : 'idle');

      // cloud 数字人模式：主进程 spawn client.py 生成；失败 fail-closed 回落 2D（不阻塞正常播放）
      if (data.audio_url && dhView && dhView.mode === 'cloud'
          && window.petAPI && typeof window.petAPI.dhCloudGenerate === 'function') {
        window.petAPI.dhCloudGenerate(data.audio_url).then((r) => {
          if (!r || !r.ok) {
            console.warn('[dh] cloud 生成失败，保持 2D 口型:', r && r.reason);
            bubble.show('（数字人生成失败，已切回 2D）', 2500);
          } else {
            console.log('[dh] cloud 生成完成:', r);
          }
        }).catch((e) => {
          console.warn('[dh] cloud 调用异常:', e);
        });
      }
      break;
    }

    // 后端回传识别到的语音文本
    case 'asr_result': {
      const t = data.text || '';
      if (t) {
        bubble.show('你说：' + t, 3500);
        if (isImmersive && subtitleText) subtitleText.textContent = '你说：' + t;
      }
      break;
    }

    // AI 思考中
    case 'ai_thinking': {
      stateMachine.setState(PetState.THINKING);
      bubble.show(data.text || '...', 999999);
      if (isImmersive && subtitleText) subtitleText.textContent = data.text || '...';
      if (immersiveStatus) immersiveStatus.textContent = '思考中...';
      _voiceBallSetState && _voiceBallSetState('processing');
      break;
    }

    // 后端推送语音球状态
    case 'speech_state': {
      const st = data.state || 'idle';
      _voiceBallSetState && _voiceBallSetState(st);
      if (immersiveStatus) {
        immersiveStatus.textContent =
          st === 'recording' ? '正在聆听...' :
          st === 'processing' ? '思考中...' :
          st === 'speaking' ? '正在说话...' : '正在聆听...';
      }
      break;
    }

    // 旧格式 action 事件
    case 'action':
      if (data.motion) {
        const motionUpper = data.motion.toUpperCase();
        if (PetState[motionUpper]) {
          stateMachine.setState(motionUpper);
        }
        character.playMotion(data.motion);
      }
      break;

    default:
      console.log('[ws] 消息类型:', type, data);
  }
}

/** 根据情绪字符串切换表情 */
function applyEmotion(emotionStr) {
  switch (emotionStr) {
    case 'happy':
      stateMachine.setState(PetState.HAPPY);
      character.setExpression('happy');
      break;
    case 'surprised':
      stateMachine.setState(PetState.SURPRISED);
      character.setExpression('surprised');
      break;
    case 'angry':
      // 没有专门的生气表情，用惊讶代替
      stateMachine.setState(PetState.SURPRISED);
      character.setExpression('surprised');
      break;
    case 'shy':
      // 害羞用开心表情
      stateMachine.setState(PetState.HAPPY);
      character.setExpression('happy');
      break;
    case 'neutral':
    default:
      // 回到 idle
      stateMachine.setState(PetState.IDLE);
      character.setExpression('idle');
      break;
  }
}

/* ==================== R3.3-78 双轨情感反馈（忠诚涨↔顺从 / 好感涨↔亲昵） ====================
 * 两套表达刻意不共用：
 *   忠诚上涨（反制线）→ 克制的低头颔首 + 顺从/服从式短句；
 *   好感上涨（引导线）→ 既有 HAPPY 撒欢姿态 + 亲昵/靠近式短句。
 */
const LOYALTY_RISE_LINES = ['主人说得对，我听你的', '嗯，都按你说的来', '我乖乖照做，不闹脾气'];
const AFFECTION_RISE_LINES = ['想再靠近你一点', '和你待在一起好开心', '悄悄蹭过来啦'];

/** 忠诚度上涨：顺从姿态——安静 IDLE + 小幅颔首低头，不撒欢 */
function feedbackLoyaltyRise() {
  if (!bubble || !stateMachine || !character) return;
  stateMachine.setState(PetState.IDLE);
  character.setExpression('idle');
  character.pulse(0.04); // 比 HAPPY 的 0.09 小一半，克制=顺从
  // 低头颔首：轻偏头后回正，220ms
  try { character.setRotation(-0.08); } catch (e) { /* noop */ }
  setTimeout(() => { try { character.setRotation(0); } catch (e) { /* noop */ } }, 220);
  const line = LOYALTY_RISE_LINES[Math.floor(Math.random() * LOYALTY_RISE_LINES.length)];
  bubble.show(line, 2600);
  if (isImmersive && subtitleText) subtitleText.textContent = line;
}

/** 好感度上涨：亲昵姿态——走既有 HAPPY 状态（Q弹 pulse 由 handleStateChange 触发） */
function feedbackAffectionRise() {
  if (!bubble || !stateMachine || !character) return;
  stateMachine.setState(PetState.HAPPY);
  character.setExpression('happy');
  const line = AFFECTION_RISE_LINES[Math.floor(Math.random() * AFFECTION_RISE_LINES.length)];
  bubble.show(line, 2600);
  if (isImmersive && subtitleText) subtitleText.textContent = line;
}

/** 显示关系阶段标签，3秒后淡出 */
function showRelationshipTag(stage) {
  if (!relationshipTagEl) return;
  relationshipTagEl.textContent = '💕 ' + stage;
  relationshipTagEl.classList.remove('hidden', 'fading');
  // 触发重排重启动画
  void relationshipTagEl.offsetWidth;
  relationshipTagEl.classList.remove('fading');
  // 3秒后淡出（CSS animation relTagOut 3s）
  relationshipTagEl.classList.add('fading');
  // 动画结束后隐藏
  setTimeout(() => {
    relationshipTagEl.classList.add('hidden');
    relationshipTagEl.classList.remove('fading');
  }, 3100);
}

/* ==================== 设置变更处理 ==================== */
// R3.3-76：桌宠侧设置 -> 主程序 config.json pet 段 的点路径映射（白名单）
const UPLINK_KEY_MAP = {
  alwaysOnTop: 'window.alwaysOnTop',
  volume: 'pet.volume',
  currentCharacter: 'pet.currentModel',
  autoLaunch: 'system.autoLaunch',
};

function handleSettingChange(key, value) {
  // 更新本地设置缓存
  settings[key] = value;

  // 持久化到主进程（桌宠本地 userData）
  if (window.petAPI && typeof window.petAPI.setSetting === 'function') {
    window.petAPI.setSetting(key, value);
  }

  // R3.3-76：上行到主程序，回写共享配置，双端一致
  const dot = UPLINK_KEY_MAP[key];
  if (dot && wsClient && typeof wsClient.sendConfigUpdate === 'function') {
    wsClient.sendConfigUpdate({ [dot]: value });
  }

  // 立即生效的设置
  switch (key) {
    case 'petScale':
      character.setScale(value);
      break;
    case 'alwaysOnTop':
      if (window.petAPI && typeof window.petAPI.setAlwaysOnTop === 'function') {
        window.petAPI.setAlwaysOnTop(value);
      }
      break;
    // volume / autoLaunch / currentCharacter 由主进程或页面重载处理
  }

  console.log('[settings]', key, '=', value);
}

// R3.3-76：拖动结束后读取窗口最新屏幕坐标并上行共享配置（window.x / window.y）
async function uplinkWindowPosition() {
  try {
    if (!window.petAPI || typeof window.petAPI.getWindowPosition !== 'function') return;
    const [x, y] = await window.petAPI.getWindowPosition();
    if (wsClient && typeof wsClient.sendConfigUpdate === 'function') {
      wsClient.sendConfigUpdate({ 'window.x': Math.round(x), 'window.y': Math.round(y) });
      console.log('[window-move] uplink', x, y);
    }
  } catch (e) {
    /* 上行失败静默，不影响拖动体验 */
  }
}

/* ==================== 托盘命令 ==================== */
function handleTrayCommand(cmd) {
  switch (cmd) {
    case 'toggle-settings':
      if (settingsPanel) {
        if (settingsPanel.isVisible()) {
          settingsPanel.hide();
        } else {
          settingsPanel.show();
        }
      }
      break;
    case 'toggle-immersive':
      setImmersive(!isImmersive);
      break;
    case 'next-character':
      // 由主进程处理角色切换
      console.log('[tray] 切换角色（由主进程处理）');
      break;
    case 'quit':
      wsClient && wsClient.disconnect();
      break;
    default:
      console.log('[tray] 未知命令:', cmd);
  }
}

/* ==================== 启动 ==================== */
boot().catch((e) => console.error('[app] 启动失败:', e));
