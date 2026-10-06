import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const a=html.indexOf('// LOCAL_SEARCH_BEGIN'),b=html.indexOf('// LOCAL_SEARCH_END');
const context=vm.createContext({});vm.runInContext(html.slice(a,b),context);
const {searchQuery,searchCursor,cloneSearchCursor,searchSlice,searchExcerpt}=context;
const segs=xs=>xs.map(text=>({text}));
function all(xs,q){const c=searchCursor(searchQuery(q)),hits=[];let slices=0;while(!c.done){const r=searchSlice(segs(xs),c);assert.ok(r.consumed<=16384);hits.push(...r.hits);assert.ok(++slices<10000);}return {hits:JSON.parse(JSON.stringify(hits)),slices};}
test('literal case whitespace punctuation and nonoverlap source locations',()=>{
  assert.deepEqual(all(['banana','A\t \nB!','ana'], 'ana').hits,[{seg:0,start:1,end:4},{seg:2,start:0,end:3}]);
  assert.deepEqual(all(['A\t \nB!'],' a b! ').hits,[{seg:0,start:0,end:6}]);
  assert.equal(all(['ab','cd'],'bc').hits.length,0);
  assert.equal(all(['a.b a?b'],'a.b').hits.length,1);
});
test('complete Unicode source spans and declared lower transform',()=>{
  assert.deepEqual(all(['x😀İz'],'😀i\u0307').hits,[{seg:0,start:1,end:4}]);
  assert.equal(all(['İ'],'i').hits.length,0);
  assert.equal(all(['İ'],'\u0307').hits.length,0);
  assert.equal(all(['😀'],'\uDE00').hits.length,0);
  assert.equal(all(['Straße'],'strasse').hits.length,0);
  assert.equal(all(['ς'],'σ').hits.length,0);
  assert.equal(all(['é'],'e').hits.length,0);
  assert.equal(all(['a\u0085b'],'a b').hits.length,0);
  assert.equal(all(['a\uFEFFb'],'\uFEFFa b\uFEFF').hits.length,1);
});
test('query bounds count source codepoints, without truncating',()=>{
  assert.equal(searchQuery('😀'.repeat(256)).length,512);
  assert.throws(()=>searchQuery('😀'.repeat(257)),/256/);
  assert.equal(searchQuery(' \t\n '),'');
});
test('huge segments yield and boundary matches remain source faithful',()=>{
  const prefix='x'.repeat(16383);
  assert.deepEqual(all([prefix+'😀needle'],'😀needle').hits,[{seg:0,start:16383,end:16391}]);
  const text='a'+' '.repeat(40000)+'b';
  const r=all([text],'a b');assert.ok(r.slices>=3);
  assert.deepEqual(r.hits,[{seg:0,start:0,end:text.length}]);
  const c=searchCursor('missing');assert.equal(searchSlice(segs(['x'.repeat(100000)]),c).consumed,16384);assert.equal(c.done,false);
});
test('fifty plus one batches, cloned cursors and resume do not drop duplicates',()=>{
  const ss=segs(['a '.repeat(600)]),c=searchCursor('a');
  const r=searchSlice(ss,c,51);assert.equal(r.hits.length,51);
  const checkpoint=cloneSearchCursor(c);searchSlice(ss,c,51);
  const resumed=searchSlice(ss,checkpoint,51);assert.equal(resumed.hits[0].start,102);
  const total=all(['a '.repeat(600)],'a');assert.equal(total.hits.length,600);
  assert.equal(new Set(total.hits.map(x=>x.start)).size,600);
});
test('excerpt limits and UTF16 boundaries include beginning of long match',()=>{
  const text='😀'.repeat(500),start=400,end=900,e=searchExcerpt(text,start,end);
  assert.ok(Array.from(e.before+e.match+e.after).length<=120);
  assert.equal(e.match,'😀'.repeat(118));assert.equal(e.trailing,true);
  assert.ok(!/[\uD800-\uDBFF]$/.test(e.match));
});
