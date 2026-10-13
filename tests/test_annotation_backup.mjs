import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto,createHash} from 'node:crypto';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8'),c=vm.createContext({TextEncoder,TextDecoder,crypto:webcrypto});
vm.runInContext(html.slice(html.indexOf('// ANNOTATIONS_BEGIN'),html.indexOf('// ANNOTATIONS_END')),c);
vm.runInContext(html.slice(html.indexOf('// ANNOTATION_BACKUP_BEGIN'),html.indexOf('// ANNOTATION_BACKUP_END')),c);
const {annotationSourceDigest,annotationBackupData,annotationBackupParse,annotationBackupBytes,annotationBackupPreview,annotationBackupMerge}=c;
const source=['fox 🦊','','second line'].map(text=>({text})),identity='generic-v1:txt:'+'a'.repeat(64),hash='b'.repeat(64),clone=x=>JSON.parse(JSON.stringify(x));
const item=(id=1,note='note',extra={})=>({id,revision:1,kind:'bookmark',start:{seg:0,offset:0},label:'label',note,...extra});
const data=(items=[],nextId=Math.max(0,...items.map(x=>x.id))+1)=>({version:1,nextId,items});
const file=(a=data())=>({format:'inn-reader-annotations',version:1,bookIdentity:identity,sourceSha256:hash,annotations:a});
const preview=(a,b)=>clone(annotationBackupPreview(a,b,source));
const merge=(a,b,choices,expected=a)=>clone(annotationBackupMerge(a,source,expected,b,choices));
test('portable backup round-trips exact Unicode, notes and source coordinates',async()=>{
 const a=data([item(4,'<img src=x>\u0000🦊'),item(8,'',{kind:'highlight',start:{seg:0,offset:4},end:{seg:2,offset:6},label:'🦊'})],12),digest=await annotationSourceDigest(source);
 assert.equal(digest,createHash('sha256').update(JSON.stringify(source.map(x=>x.text))).digest('hex'));
 const bytes=annotationBackupBytes(identity,digest,a,source),decoded=annotationBackupParse(bytes);assert.deepEqual(clone(decoded.annotations),a);assert.equal(Object.keys(decoded).length,5);assert.equal(JSON.stringify(decoded).includes('second line'),false);
 assert.equal(annotationBackupData(decoded,identity,digest,source),decoded);assert.deepEqual(clone(annotationBackupParse(annotationBackupBytes(identity,digest,data(),source)).annotations),data());
});
test('projection digest changes for boundaries, order and exact text; unavailable digest refuses',async()=>{
 const h=await annotationSourceDigest(source);for(const changed of [[...source].reverse(),[{text:'fox 🦊second line'}],source.map((x,i)=>({text:x.text+(i?'':' ')}))])assert.notEqual(await annotationSourceDigest(changed),h);
 await assert.rejects(annotationSourceDigest(source,null),/DIGEST/);await assert.rejects(annotationSourceDigest(source,{digest:async()=>{throw Error('no');}}),/DIGEST/);
});
test('invalid UTF8, oversize and malformed/unknown format refuse before restore',()=>{
 assert.throws(()=>annotationBackupParse({byteLength:64*1048576+1}),/FILE_LIMIT/);for(const bytes of [new Uint8Array([255]),new TextEncoder().encode('{')])assert.throws(()=>annotationBackupParse(bytes),/FILE/);
 for(const bad of [null,[],{...file(),extra:1},{...file(),version:2},{...file(),format:'other'},{...file(),bookIdentity:''},{...file(),bookIdentity:'x'.repeat(1025)},{...file(),sourceSha256:hash.toUpperCase()},{...file(),sourceSha256:'c'}])assert.throws(()=>annotationBackupData(bad,identity,hash,source),/FILE/);
});
test('same title or fitting offsets never override identity or projection mismatch',()=>{
 assert.throws(()=>annotationBackupData(file(),identity+'other',hash,source),/BOOK/);assert.throws(()=>annotationBackupData(file(),identity,'c'.repeat(64),source),/SOURCE/);
 const fallback='ephemeral:example',f={...file(),bookIdentity:fallback};assert.equal(annotationBackupData(f,fallback,hash,source),f);assert.throws(()=>annotationBackupData(f,'ephemeral:new',hash,source),/BOOK/);
});
test('malformed annotations never coerce, clamp, normalize or truncate',()=>{
 for(const a of [{...data(),version:2},data([item(1,'',{start:{seg:0,offset:5}})]),data([item(1,'',{kind:'highlight',end:{seg:0,offset:0}})]),data([item(1,'x'.repeat(8193))]),data([item(1,'',{label:'🦊'.repeat(121)})]),data([item(),item()]),data([item(2)],2),data([item(1,'',{extra:1})])])assert.throws(()=>annotationBackupData(file(a),identity,hash,source),/DATA|RANGE|LIMIT/);
});
test('duplicates match one-to-one; full restore preserves multiplicity and is idempotent',()=>{
 const local=data([item(7)],20),incoming=data([item(1),item(2)]),rows=preview(local,incoming);assert.deepEqual(rows.map(x=>x.type),['duplicate','new']);
 const restored=merge(local,incoming,rows.map(x=>x.selected));assert.equal(restored.items.length,2);assert.equal(restored.items[1].id,20);assert.equal(restored.items[1].revision,1);assert.equal(restored.nextId,21);assert.equal(local.items.length,1);
 assert.deepEqual(preview(restored,incoming).map(x=>x.type),['duplicate','duplicate']);assert.deepEqual(merge(restored,incoming,[false,false]),restored);
});
test('partial selections remain visible and eligible on later previews',()=>{
 const a=data(),twins=data([item(1),item(2)]),first=merge(a,twins,[false,true]);assert.deepEqual(preview(first,twins).map(x=>[x.type,x.selected]),[['duplicate',false],['new',true]]);
 const distinct=data([item(1,'A'),item(2,'B',{start:{seg:2,offset:0}})]),chosen=merge(a,distinct,[true,false]);assert.deepEqual(preview(chosen,distinct).map(x=>[x.type,x.selected]),[['duplicate',false],['new',true]]);
});
test('same-anchor conflicts expose all differing local values and default to keep',()=>{
 const a=data([item(5,'A'),item(6,'B')],10),incoming=data([item(1,'C')]),rows=preview(a,incoming);assert.equal(rows[0].type,'conflict');assert.equal(rows[0].selected,false);assert.deepEqual(rows[0].existing.map(x=>x.note),['A','B']);
 assert.deepEqual(merge(a,incoming,[false]),a);const added=merge(a,incoming,[true]);assert.deepEqual(added.items.slice(0,2),a.items);assert.deepEqual(added.items.map(x=>x.note),['A','B','C']);assert.equal(added.items[2].id,10);
 const mixed=preview(a,data([item(1,'A'),item(2,'A')]));assert.deepEqual(mixed.map(x=>x.type),['duplicate','conflict']);assert.deepEqual(mixed[1].existing.map(x=>x.note),['B']);
});
test('every annotation snapshot change refuses; JSON property order does not',()=>{
 const a=data([item()],5),incoming=data([item(1,'new')]);
 for(const changed of [data([],5),data([item(1,'edited')],5),data([item(1,'note',{revision:2})],5),data([item(2)],5),data([item()],6),data([item(),item(2)],5)])assert.throws(()=>merge(changed,incoming,[true],a),/CONFLICT/);
 const reordered={items:a.items.map(x=>({note:x.note,label:x.label,start:{offset:0,seg:0},kind:x.kind,revision:x.revision,id:x.id})),nextId:5,version:1};assert.equal(merge(reordered,incoming,[true],a).items[1].id,5);
});
test('limits and invalid choices refuse the whole batch; fresh IDs never copy source IDs',()=>{
 const full=data(Array.from({length:1000},(_,i)=>item(i+1)),1001),incoming=data([item(30,'different')],40);assert.throws(()=>merge(full,incoming,[true]),/LIMIT/);assert.equal(full.items.length,1000);
 assert.throws(()=>merge(data([],Number.MAX_SAFE_INTEGER),incoming,[true]),/LIMIT/);const restored=merge(data([],Number.MAX_SAFE_INTEGER-1),incoming,[true]);assert.equal(restored.nextId,Number.MAX_SAFE_INTEGER);
 for(const choices of [[],[1],[true,true]])assert.throws(()=>merge(data(),incoming,choices),/DATA/);assert.throws(()=>merge(data([item()]),data([item()]),[true]),/DATA/);
});
