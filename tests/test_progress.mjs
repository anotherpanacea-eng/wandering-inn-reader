import {test} from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';import {readFileSync} from 'node:fs';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),ctx=vm.createContext({});
vm.runInContext(html.match(/function genericRestoreSeg\([^\n]+/)[0],ctx);
for(const tag of ['ANNOTATIONS','PROGRESS'])vm.runInContext(html.slice(html.indexOf('// '+tag+'_BEGIN'),html.indexOf('// '+tag+'_END')),ctx);
const {progressMeasure,progressRestore,progressEnd,progressPoint}=ctx,source=texts=>texts.map(text=>({text})),plain=x=>JSON.parse(JSON.stringify(x));
test('one long segment advances by original source position',()=>{const s=source(['a'.repeat(1000)]);assert.equal(progressMeasure(s,{seg:0,offset:500}).percent,50);assert.equal(progressMeasure(s,{seg:0,offset:900}).percent,90);assert.equal(progressMeasure(s,{seg:0,offset:999}).percent,99);});
test('Unicode scalars and no inserted segment separators determine percentage',()=>{const s=source(['a🦊','', 'bc']);assert.equal(progressMeasure(s,{seg:2,offset:0}).percent,50);assert.equal(progressMeasure(s,{seg:0,offset:1}).percent,25);assert.equal(progressPoint(s,{seg:0,offset:2}),false);assert.throws(()=>progressMeasure(s,{seg:0,offset:2}),/POSITION/);});
test('legacy invalid offsets are approximate without mutating raw records',()=>{const s=source(['alpha','beta']);for(const raw of [{seg:1},{seg:1,offset:-1},{seg:1,offset:true},{seg:1,offset:Infinity},{seg:1,offset:8}]){const old={...raw},r=progressRestore(s,raw);assert.equal(r.precise,false);assert.deepEqual(plain(r.point),{seg:1,offset:0});assert.deepEqual(raw,old);}assert.equal(progressRestore(s,{seg:1,offset:2}).precise,true);assert.equal(progressRestore(s,{seg:true,offset:0}).precise,false);});
test('partial word stays remaining and fixed pace rounds up',()=>{const s=source(['alpha beta']);assert.equal(progressMeasure(s,{seg:0,offset:3}).minutes,1);assert.equal(progressMeasure(s,{seg:0,offset:10}).minutes,0);assert.equal(progressMeasure(source(['x '.repeat(201)]),{seg:0,offset:0}).minutes,2);});
test('explicit source end differs from opening and last source character',()=>{const s=source(['alpha','','']);assert.deepEqual(plain(progressEnd(s)),{seg:0,offset:5});assert.equal(progressMeasure(s,{seg:0,offset:0}).finished,false);assert.equal(progressMeasure(s,{seg:0,offset:4}).percent,80);const end=progressMeasure(s,progressEnd(s));assert.equal(end.finished,true);assert.equal(end.percent,100);assert.equal(end.minutes,0);});
test('empty and whitespace source never fabricate a reading estimate',()=>{assert.equal(progressEnd(source(['',''])),null);const empty=progressMeasure(source(['']),{seg:0,offset:0});assert.equal(empty.percent,null);assert.equal(empty.minutes,null);const s=source(['  ']);assert.equal(progressMeasure(s,{seg:0,offset:0}).minutes,null);assert.equal(progressMeasure(s,progressEnd(s)).minutes,0);});

test('remaining estimates handle partial words and independent document generations',()=>{
  const first=source(['one two ', 'three🦊 four', '', 'five ']);
  assert.equal(progressMeasure(first,{seg:1,offset:2}).percent,41);
  assert.equal(progressMeasure(first,{seg:1,offset:2}).minutes,1);
  const second=source(['x '.repeat(401), 'tail']);
  assert.equal(progressMeasure(second,{seg:0,offset:0}).minutes,3);
  assert.equal(progressMeasure(second,{seg:1,offset:0}).minutes,1);
  assert.equal(progressMeasure(first,progressEnd(first)).minutes,0);
});
