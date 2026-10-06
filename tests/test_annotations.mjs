import {test} from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import vm from 'node:vm';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),c=vm.createContext({});vm.runInContext(html.slice(html.indexOf('// ANNOTATIONS_BEGIN'),html.indexOf('// ANNOTATIONS_END')),c);
const {annotationCanonical,annotationPoint,annotationData,annotationChange,annotationIntervals,annotationSourceEqual,annotationInstanceValid,newAnnotationInstance}=c;
const source=['alpha 🦊','','beta delta'].map(text=>({text})),plain=x=>JSON.parse(JSON.stringify(x)),empty=()=>({version:1,nextId:1,items:[]});
const create=(a,extra={})=>annotationChange(a,source,{action:'create',kind:'highlight',start:{seg:0,offset:6},end:{seg:2,offset:4},label:'',note:'',...extra});
test('canonical source spans cross empty segments without splitting scalars',()=>{
 assert.deepEqual(plain(annotationCanonical(source,{seg:0,offset:8},{seg:2,offset:2})),{start:{seg:2,offset:0},end:{seg:2,offset:2}});
 assert.deepEqual(plain(annotationCanonical(source,{seg:0,offset:0},{seg:2,offset:0})),{start:{seg:0,offset:0},end:{seg:0,offset:8}});
 for(const p of [{seg:0,offset:7},{seg:true,offset:0},{seg:0,offset:NaN},{seg:3,offset:0}])assert.equal(annotationPoint(source,p),false);
 assert.throws(()=>annotationCanonical(source,{seg:0,offset:8},{seg:2,offset:0}),/RANGE/);assert.throws(()=>annotationCanonical(source,{seg:0,offset:6},{seg:0,offset:7}),/RANGE/);
});
test('CRUD preserves other items, monotonic IDs and stale revision refusal',()=>{
 const first=create(empty()),second=create(first,{kind:'bookmark',start:{seg:2,offset:1}});assert.equal(first.items.length,1);assert.equal(second.items.length,2);
 const edited=annotationChange(second,source,{action:'edit',id:1,revision:1,label:'label',note:'note'});assert.equal(edited.items[0].revision,2);assert.equal(edited.items[1].id,2);
 assert.throws(()=>annotationChange(edited,source,{action:'edit',id:1,revision:1,label:'stale',note:''}),/CONFLICT/);
 const deleted=annotationChange(edited,source,{action:'delete',id:1,revision:2});assert.equal(deleted.nextId,3);assert.deepEqual(plain(deleted.items).map(x=>x.id),[2]);assert.equal(create(deleted).items[1].id,3);
 assert.throws(()=>create({version:1,nextId:Number.MAX_SAFE_INTEGER,items:[]}),/LIMIT/);const exhausted=create(empty());exhausted.items[0].revision=Number.MAX_SAFE_INTEGER;assert.throws(()=>annotationChange(exhausted,source,{action:'edit',id:1,revision:Number.MAX_SAFE_INTEGER,label:'',note:''}),/LIMIT/);
});
test('corrupt and unknown records refuse without resetting or reusing IDs',()=>{
 const a=create(empty());for(const bad of [undefined,null,{...a,version:2},{...a,nextId:1},{...a,extra:1},{...a,items:[a.items[0],a.items[0]]},{...a,items:[{...a.items[0],start:{seg:0,offset:7}}]},{...a,items:[{...a.items[0],unexpected:'value'}]}])assert.throws(()=>annotationData(bad,source),/DATA|RANGE/);assert.equal(a.nextId,2);
});
test('label scalar, note UTF16 and item-count bounds never truncate',()=>{
 const a=create(empty(),{label:'🦊'.repeat(120),note:'x'.repeat(8192)});assert.equal(a.items[0].label.length,240);assert.equal(a.items[0].note.length,8192);assert.throws(()=>create(empty(),{label:'🦊'.repeat(121)}),/LIMIT/);assert.throws(()=>create(empty(),{note:'x'.repeat(8193)}),/LIMIT/);
 const full={version:1,nextId:1001,items:Array.from({length:1000},(_,i)=>({id:i+1,revision:1,kind:'bookmark',start:{seg:0,offset:0},label:'',note:''}))};assert.equal(annotationData(full,source).items.length,1000);assert.throws(()=>create(full),/LIMIT/);
});
test('overlap union retains source coverage and separate item identities',()=>{
 const a=create(empty()),b=create(a,{start:{seg:0,offset:0},end:{seg:0,offset:6}});assert.deepEqual(plain(annotationIntervals(b.items,0,8)),[[0,8]]);assert.deepEqual(plain(annotationIntervals(b.items,2,10)),[[0,4]]);assert.equal(b.items.length,2);
 const text=source[0].text;let out='',at=0;for(const [l,r] of annotationIntervals(b.items,0,text.length)){out+=text.slice(at,l)+text.slice(l,r);at=r;}assert.equal(out+text.slice(at),text);
});
test('ordered admitted source and version4 instance validation',()=>{
 assert.equal(annotationSourceEqual(source,source.map(x=>({...x}))),true);assert.equal(annotationSourceEqual(source,[source[2],source[1],source[0]]),false);assert.equal(annotationSourceEqual(source,[...source,{text:''}]),false);
 assert.equal(annotationInstanceValid('12345678-1234-4123-8123-123456789abc'),true);for(const v of ['',true,'12345678-1234-1123-8123-123456789abc','12345678-1234-4123-7123-123456789abc'])assert.equal(annotationInstanceValid(v),false);assert.equal(newAnnotationInstance(),null);
});