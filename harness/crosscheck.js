// Checks a mod's scripts against vanilla before the game ever boots, because every
// unresolved name in B42 is a boot-time error rather than a quiet fallback. It indexes the
// vanilla scripts once and then verifies that everything the mod names -- items, models,
// icons, timed actions, categories, sprites, translation keys -- actually exists somewhere.
//
//   node harness/crosscheck.js --mod <mod>/common/media --game "C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid/media" \
//        [--icon-prefix FP] [--key-marker FP] [--langs EN,KO]
//
// --icon-prefix: Icon = names starting with it must have textures/Item_<name>.png in the mod
//                (vanilla icons live in texture packs and are not indexed).
// --key-marker:  getText("IGUI_...") keys containing it must be in IG_UI.json.
// Sprite names are checked against the mod's own .tiles sheets (every *.tiles under --mod).
const fs = require('fs'), path = require('path');
const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const MOD = arg('--mod'), VAN = arg('--game');
if (!MOD || !VAN) { console.error('usage: crosscheck.js --mod <media dir> --game <vanilla media dir>'); process.exit(2); }
const ICON_PREFIX = arg('--icon-prefix', ''), KEY_MARKER = arg('--key-marker', ''), LANGS = arg('--langs', 'EN,KO').split(',');
const problems = [];
const note = (m) => problems.push(m);

const walk = (dir, out = []) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out); else out.push(p);
  }
  return out;
};
const read = (p) => fs.readFileSync(p, 'utf8');

// --- index vanilla --------------------------------------------------------------
const vanScripts = walk(VAN + '/scripts').filter(p => p.endsWith('.txt'));
const vanItems = new Set(), vanModels = new Set(), vanFoodTypes = new Set();
for (const p of vanScripts) {
  let mod = 'Base';
  for (const line of read(p).split('\n')) {
    const m = line.match(/^\s*module\s+(\S+)/); if (m) mod = m[1];
    const i = line.match(/^\s*item\s+([A-Za-z0-9_]+)\s*$/); if (i) vanItems.add(mod + '.' + i[1]);
    const o = line.match(/^\s*model\s+([A-Za-z0-9_]+)\s*$/); if (o) vanModels.add(o[1]);
    const f = line.match(/^\s*FoodType\s*=\s*([A-Za-z0-9_ ]+),/); if (f) vanFoodTypes.add(f[1].trim());
  }
}
const vanTimedActions = new Set();
for (const p of vanScripts) for (const m of read(p).matchAll(/timedAction\s*=\s*([A-Za-z0-9_]+)/g)) vanTimedActions.add(m[1]);
const vanCategories = new Set();
for (const p of vanScripts) for (const m of read(p).matchAll(/^\s*category\s*=\s*([^,\n]+),/gm)) vanCategories.add(m[1].trim());

// --- index the mod --------------------------------------------------------------
const modTextures = new Set(fs.existsSync(MOD + '/textures') ? fs.readdirSync(MOD + '/textures').map(f => f.replace(/\.png$/, '')) : []);

// tile sheets are binary; the sprite names an entity may use, and each tile's properties,
// are read back out of them
const tileNames = new Set(), sheetPrefixes = new Set(), containerTypes = new Set();
const readTiles = (file) => {
  const buf = fs.readFileSync(file);
  let o = 4;
  const r32 = () => { const v = buf.readInt32LE(o); o += 4; return v; };
  const rnl = () => { const e = buf.indexOf(0x0a, o); const s = buf.toString('latin1', o, e); o = e + 1; return s; };
  r32(); const sheets = r32();
  for (let s = 0; s < sheets; s++) {
    const name = rnl(); rnl(); r32(); r32(); r32(); const n = r32();
    sheetPrefixes.add(name.replace(/_[0-9]+$/, '') + '_');
    for (let i = 0; i < n; i++) {
      tileNames.add(name + '_' + i);
      const pc = r32();
      for (let q = 0; q < pc; q++) { const k = rnl(), v = rnl(); if (k === 'container') containerTypes.add(v); }
    }
  }
};
for (const f of walk(MOD).filter(p => p.endsWith('.tiles'))) readTiles(f);

const modScripts = walk(MOD + '/scripts').filter(p => p.endsWith('.txt'));
const modItems = new Set();
for (const p of modScripts) {
  let mod = 'Base';
  for (const line of read(p).split('\n')) {
    const m = line.match(/^\s*module\s+(\S+)/); if (m) mod = m[1];
    const i = line.match(/^\s*item\s+([A-Za-z0-9_]+)\s*$/); if (i) modItems.add(mod + '.' + i[1]);
  }
}
const known = (t) => modItems.has(t) || vanItems.has(t);
const modSprite = (name) => { for (const pre of sheetPrefixes) if (name.startsWith(pre)) return true; return false; };

// --- checks ---------------------------------------------------------------------
for (const p of modScripts) {
  const rel = path.basename(p), text = read(p);
  const at = (needle) => text.slice(0, text.indexOf(needle)).split('\n').length;

  if (ICON_PREFIX)
    for (const m of text.matchAll(/^\s*Icon\s*=\s*([A-Za-z0-9_]+),/gm))
      if (m[1].startsWith(ICON_PREFIX) && !modTextures.has('Item_' + m[1]) && !modTextures.has(m[1]))
        note(`${rel}:${at(m[0])} Icon ${m[1]} has no textures/Item_${m[1]}.png`);

  for (const m of text.matchAll(/^\s*(?:Static|WorldStatic)Model\s*=\s*([A-Za-z0-9_]+),/gm))
    if (!vanModels.has(m[1])) note(`${rel}:${at(m[0])} model ${m[1]} is not a vanilla model`);

  for (const m of text.matchAll(/^\s*ReplaceOnCooked\s*=\s*([A-Za-z0-9_.]+),/gm))
    if (!known(m[1])) note(`${rel}:${at(m[0])} ReplaceOnCooked ${m[1]} is not a known item`);

  for (const m of text.matchAll(/^\s*FoodType\s*=\s*([^,\n]+),/gm))
    if (!vanFoodTypes.has(m[1].trim())) note(`${rel}:${at(m[0])} FoodType ${m[1].trim()} is not used by vanilla`);

  for (const m of text.matchAll(/timedAction\s*=\s*([A-Za-z0-9_]+)/g))
    if (!vanTimedActions.has(m[1])) note(`${rel}:${at(m[0])} timedAction ${m[1]} does not exist`);

  for (const m of text.matchAll(/^\s*category\s*=\s*([^,\n]+),/gm))
    if (!vanCategories.has(m[1].trim())) note(`${rel}:${at(m[0])} category ${m[1].trim()} is not a vanilla category`);

  // recipe inputs/outputs: [Module.Item] references
  for (const m of text.matchAll(/\[([A-Za-z0-9_]+\.[A-Za-z0-9_;.]+)\]/g))
    for (const t of m[1].split(';'))
      if (!known(t)) note(`${rel}:${at(m[0])} references unknown item ${t}`);

  // sprite rows on the mod's own sheets must exist in them
  for (const m of text.matchAll(/^\s*row\s*=\s*(?:\w+\s+)?([a-z0-9_]+),/gm))
    if (modSprite(m[1]) && !tileNames.has(m[1])) note(`${rel}:${at(m[0])} sprite ${m[1]} is not in the mod's .tiles`);
}

// --- translations ---------------------------------------------------------------
const trPath = (lang, file) => `${MOD}/lua/shared/Translate/${lang}/${file}`;
const tr = (lang, file) => fs.existsSync(trPath(lang, file)) ? JSON.parse(read(trPath(lang, file))) : null;
const [BASE, ...OTHERS] = LANGS;
for (const file of ['ItemName.json', 'Recipes.json', 'Tooltip.json', 'IG_UI.json']) {
  const base = tr(BASE, file);
  if (!base) continue;
  for (const lang of OTHERS) {
    const other = tr(lang, file);
    if (!other) { note(`${lang}/${file} is missing`); continue; }
    for (const k of Object.keys(base)) if (!(k in other)) note(`${lang}/${file} is missing ${k}`);
    for (const k of Object.keys(other)) if (!(k in base)) note(`${BASE}/${file} is missing ${k}`);
  }
}
{
  const names = tr(BASE, 'ItemName.json') || {};
  for (const t of modItems) if (!(t in names)) note(`${BASE}/ItemName.json is missing ${t}`);
  for (const k of Object.keys(names)) if (!modItems.has(k)) note(`${BASE}/ItemName.json names ${k}, which no longer exists`);
}
{
  const tips = tr(BASE, 'Tooltip.json') || {};
  const ig = tr(BASE, 'IG_UI.json') || {};
  const used = new Set();
  for (const p of modScripts) for (const m of read(p).matchAll(/Tooltip\s*=\s*([A-Za-z0-9_]+),/g)) used.add(m[1]);
  // lua reads strings too: getText("Tooltip_...") lives in Tooltip.json, getText("IGUI_...") in IG_UI.json
  if (fs.existsSync(MOD + '/lua'))
    for (const p of walk(MOD + '/lua').filter(f => f.endsWith('.lua')))
      for (const m of read(p).matchAll(/getText\("((?:Tooltip|IGUI)_[A-Za-z0-9_]+)"/g)) {
        if (m[1].startsWith('Tooltip_')) used.add(m[1]);
        else if (KEY_MARKER && m[1].indexOf(KEY_MARKER) !== -1 && !(m[1] in ig)) note(`${BASE}/IG_UI.json is missing ${m[1]}`);
      }
  for (const k of used) if (!(k in tips)) note(`${BASE}/Tooltip.json is missing ${k}`);
  for (const k of Object.keys(tips)) if (!used.has(k)) note(`${BASE}/Tooltip.json defines unused ${k}`);
  // the loot window titles a container by its type: IGUI_ContainerTitle_<type> must exist
  for (const v of containerTypes) for (const lang of LANGS) {
    const t = tr(lang, 'IG_UI.json') || {};
    if (!(('IGUI_ContainerTitle_' + v) in t)) note(`${lang}/IG_UI.json is missing IGUI_ContainerTitle_${v}`);
  }
}
console.log(`indexed ${vanItems.size} vanilla items, ${vanModels.size} models; mod defines ${modItems.size} items, ${tileNames.size} sprites`);
if (!problems.length) console.log('OK - nothing unresolved');
else { console.log(problems.length + ' problem(s):'); problems.forEach(p => console.log('  ' + p)); process.exitCode = 1; }
