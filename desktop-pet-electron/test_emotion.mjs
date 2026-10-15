/**
 * test_emotion.mjs — R3.3-78 EmotionSystem 忠诚度(detectRise) 单元测试
 * 运行：node test_emotion.mjs（在 desktop-pet-electron/ 目录下）
 * 纯断言，无第三方依赖；EmotionSystem 内部 setInterval 需 destroy() 收尾。
 */
import { EmotionSystem } from './renderer/js/emotion.js';

let passed = 0;
let failed = 0;
function assert(cond, msg) {
  if (cond) { passed++; console.log('  ok  -', msg); }
  else { failed++; console.error('  FAIL-', msg); }
}

console.log('[1] loyalty 初始化');
{
  const e = new EmotionSystem();
  assert(e.loyalty === 50, '默认 loyalty = 50，实际 ' + e.loyalty);
  assert(typeof e.loyalty === 'number', 'loyalty getter 返回 number');
  e.destroy();
}
{
  const e = new EmotionSystem({ loyalty: 80, affection: 30 });
  assert(e.loyalty === 80, '自定义 loyalty=80 生效，实际 ' + e.loyalty);
  assert(e.affection === 30, 'affection 不受影响 = 30，实际 ' + e.affection);
  e.destroy();
}

console.log('[2] update({loyalty}) 更新 + 0~100 钳制');
{
  const e = new EmotionSystem({ loyalty: 50 });
  e.update({ loyalty: 72 });
  assert(e.loyalty === 72, 'update loyalty 50->72，实际 ' + e.loyalty);
  e.update({ loyalty: 150 });
  assert(e.loyalty === 100, '上限钳制 150->100，实际 ' + e.loyalty);
  e.update({ loyalty: -10 });
  assert(e.loyalty === 0, '下限钳制 -10->0，实际 ' + e.loyalty);
  e.update({ affection: 60 });
  assert(e.loyalty === 0, '只更新 affection 不动 loyalty = 0，实际 ' + e.loyalty);
  e.destroy();
}

console.log('[3] _notify 快照含 loyalty');
{
  const e = new EmotionSystem();
  let snapSeen = null;
  e.onChange((s) => { snapSeen = s; });
  e.update({ loyalty: 66 });
  assert(snapSeen && snapSeen.loyalty === 66, 'onChange 快照带 loyalty=66，实际 ' + JSON.stringify(snapSeen));
  const s = e.snapshot();
  assert('loyalty' in s && 'affection' in s && 'mood' in s && 'satiety' in s,
    'snapshot() 四元齐全：' + JSON.stringify(s));
  e.destroy();
}

console.log('[4] detectRise 三态判定');
{
  const e = new EmotionSystem();
  // 4a 仅 loyalty 涨
  let r = e.detectRise({ affection: 50, loyalty: 50 }, { affection: 50, loyalty: 55 });
  assert(r.metric === 'loyalty' && r.delta === 5,
    '仅忠诚涨 -> {loyalty,5}，实际 ' + JSON.stringify(r));
  // 4b 仅 affection 涨
  r = e.detectRise({ affection: 50, loyalty: 50 }, { affection: 62, loyalty: 50 });
  assert(r.metric === 'affection' && r.delta === 12,
    '仅好感涨 -> {affection,12}，实际 ' + JSON.stringify(r));
  // 4c 两者同涨 -> null（交给 data.metric 显式标记）
  r = e.detectRise({ affection: 50, loyalty: 50 }, { affection: 55, loyalty: 55 });
  assert(r.metric === null && r.delta === 5,
    '同涨 -> {null,5}，实际 ' + JSON.stringify(r));
  // 4d 都不涨 -> null/0
  r = e.detectRise({ affection: 50, loyalty: 50 }, { affection: 50, loyalty: 50 });
  assert(r.metric === null && r.delta === 0,
    '都不涨 -> {null,0}，实际 ' + JSON.stringify(r));
  // 4e 下降不算涨
  r = e.detectRise({ affection: 50, loyalty: 50 }, { affection: 50, loyalty: 40 });
  assert(r.metric === null, '忠诚下降不算涨 -> null，实际 ' + JSON.stringify(r));
  e.destroy();
}

console.log('[5] 端到端：update 前后 snapshot + detectRise 联动');
{
  const e = new EmotionSystem({ affection: 50, loyalty: 50 });
  const prev = e.snapshot();
  e.update({ loyalty: 61, affection: 50 });
  const next = e.snapshot();
  const r = e.detectRise(prev, next);
  assert(r.metric === 'loyalty', '桥推 loyalty=61 后 detectRise 判 loyalty，实际 ' + JSON.stringify(r));
  e.destroy();
}

console.log('----------------------------------------');
console.log(`PASS=${passed}  FAIL=${failed}`);
process.exit(failed === 0 ? 0 : 1);
