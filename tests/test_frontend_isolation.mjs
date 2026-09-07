import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../web/feihou_easy_h3_ui.js', import.meta.url), 'utf8');
const classifier = source.slice(source.indexOf('function nodeMatchesClass('), source.indexOf('function canonicalOption('));
const scope = vm.createContext({ NODE_CLASS: 'FeiHouEasyH3', LOADER_CLASS: 'FeiHouEasyH3Loader', REMIX_LOADER_CLASS: 'FeiHouEasyH3RemixLoader', ADAPTER_CLASS: 'FeiHouEasyH3ModelAdapter', OUTPUT_CLASS: 'FeiHouEasyH3Output', TEXT: {} });
vm.runInContext(classifier, scope);
assert.ok(!/__h3Easy\w*Installed|__h3MediaSource\w*Installed/.test(source));

// Minimal foreign ownership predicate verified against nkxx188's d00fd814.
// This is a transport-order simulation, not a full ComfyUI browser execution.
function foreignTarget(n) {
    return Boolean(n.constructor.prototype.__h3EasyNodeInstalled)
        || [n.comfyClass, n.type, n.constructor.comfyClass, n.constructor.type,
            n.constructor.nodeData?.name, n.constructor.nodeData?.display_name, n.title]
            .some((v) => ['MiniMaxH3Easy', 'MiniMax H3 Easy'].includes(v));
}
class FeiHou {}
class MiniMax {}
FeiHou.prototype.__feihouStandardH3EasyNodeInstalled = true;
MiniMax.prototype.__h3EasyNodeInstalled = true;
const own = Object.assign(new FeiHou(), { id: 1, comfyClass: 'FeiHouEasyH3', title: 'Renamed node' });
const other = Object.assign(new MiniMax(), { id: 2, comfyClass: 'MiniMaxH3Easy', title: 'ComfyUI-FeiHou-Easy-H3' });
assert.equal(scope.isTarget(own), true);
assert.equal(scope.isTarget(other), false);
assert.equal(foreignTarget(own), false);
assert.equal(foreignTarget(other), true);
for (const [type, check] of [['FeiHouEasyH3Loader', 'isLoader'], ['FeiHouEasyH3RemixLoader', 'isRemixLoader'], ['FeiHouEasyH3ModelAdapter', 'isAdapter'], ['FeiHouEasyH3Output', 'isOutput']]) {
    assert.equal(scope[check]({ comfyClass: type }), true);
    assert.equal(scope[check]({ comfyClass: type.replace('FeiHou', 'MiniMax'), title: type }), false);
}
assert.equal(scope.isTarget({ comfyClass: 'FeiHouEasyH3RH', title: 'ComfyUI-FeiHou-Easy-H3' }), false);
assert.equal(scope.isTarget({ title: 'ComfyUI-FeiHou-Easy-H3' }), false);

const wrap = (original, target, filename) => async () => {
    const result = await original();
    for (const node of [own, other]) {
        if (!target(node)) continue;
        const input = result[node.id];
        for (let i = 1; i <= 15; i++) { delete input[`media_${i}`]; delete input[`media_type_${i}`]; }
        input.media_1 = filename;
        input.media_type_1 = 'image';
        input.prompt = `<Picture 1> ${filename}`;
    }
    return result;
};
for (const reversed of [false, true]) {
    const patches = [[scope.isTarget, 'feihou.png'], [foreignTarget, 'minimax.png']];
    if (reversed) patches.reverse();
    let queue = async () => ({ 1: {}, 2: {} });
    for (const [target, filename] of patches) queue = wrap(queue, target, filename);
    const result = await queue();
    assert.equal(result[1].media_1, 'feihou.png');
    assert.equal(result[2].media_1, 'minimax.png');
}
console.log('PASS: strict class ownership, renamed nodes, RH exclusion, all loader/output classes, both transport wrapper orders');
