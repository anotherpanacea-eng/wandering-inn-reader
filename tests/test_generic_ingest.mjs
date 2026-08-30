// Behavioral gate for the shipped Phase-0 generic-ingest block in index.html.
import {test,after} from "node:test";
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,rmSync} from "node:fs";
import {dirname,join} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath,pathToFileURL} from "node:url";

const root=join(dirname(fileURLToPath(import.meta.url)),"..");
const html=readFileSync(join(root,"index.html"),"utf8");
const a=html.indexOf("// GENERIC_INGEST_BEGIN"),b=html.indexOf("// GENERIC_INGEST_END");
assert.ok(a>=0&&b>a,"shipped generic-ingest block missing");
const modPath=join(tmpdir(),`generic_ingest_${process.pid}.mjs`);
const names=["GI_LIMIT","decodeGenericText","txtBook","markdownInline","markdownBook","projectGeneric","crc32","parseZip","readZipEntry","checkXmlDoctype","resolveHref","genericDocFromBytes","genericIdentity","genericPositionKey"];
writeFileSync(modPath,html.slice(a,b)+`\nexport {${names.join(",")}};\n`);
after(()=>{try{rmSync(modPath);}catch{}});
const gi=await import(pathToFileURL(modPath).href);

const te=new TextEncoder();
const le16=n=>Uint8Array.of(n&255,(n>>>8)&255);
const le32=n=>Uint8Array.of(n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255);
const cat=(...xs)=>{const n=xs.reduce((s,x)=>s+x.length,0),o=new Uint8Array(n);let p=0;for(const x of xs){o.set(x,p);p+=x.length;}return o;};
async function rawDeflate(bytes){const rd=new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw")).getReader(),cs=[];for(;;){const x=await rd.read();if(x.done)break;cs.push(x.value);}return cat(...cs);}
async function makeZip(specs){const locals=[],centrals=[];let off=0;for(const s of specs){const name=te.encode(s.name),plain=te.encode(s.text),method=s.method||0,comp=method===8?await rawDeflate(plain):plain,crc=gi.crc32(plain),flags=(s.flags||0)|(s.descriptor?8:0),extra=s.localExtra||new Uint8Array(),desc=s.descriptor?(s.descriptor==="nosig"?cat(le32(crc),le32(comp.length),le32(plain.length)):cat(le32(0x08074b50),le32(crc),le32(comp.length),le32(plain.length))):new Uint8Array(),local=cat(le32(0x04034b50),le16(20),le16(flags),le16(method),le16(0),le16(0),le32(s.descriptor?0:crc),le32(s.descriptor?0:comp.length),le32(s.descriptor?0:plain.length),le16(name.length),le16(extra.length),name,extra,comp,desc),gap=new Uint8Array(s.gap||0),centralExtra=s.centralExtra||new Uint8Array();locals.push(cat(local,gap));centrals.push(cat(le32(0x02014b50),le16(20),le16(20),le16(flags),le16(method),le16(0),le16(0),le32(crc),le32(comp.length),le32(plain.length),le16(name.length),le16(centralExtra.length),le16(0),le16(0),le16(0),le32(0),le32(off),name,centralExtra));off+=local.length+gap.length;}const central=cat(...centrals),eocd=cat(le32(0x06054b50),le16(0),le16(0),le16(specs.length),le16(specs.length),le32(central.length),le32(off),le16(0));return cat(...locals,central,eocd);}

test("strict text decoding and TXT projection",async()=>{
  assert.equal(gi.decodeGenericText(te.encode("a\r\n\r\nb")).text,"a\n\nb");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xef,0xbb,0xbf,0x61)).text,"a");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xff,0xfe,0x41,0,0x42,0)).text,"AB");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xfe,0xff,0,0x41,0,0x42)).text,"AB");
  assert.throws(()=>gi.decodeGenericText(Uint8Array.of(0,0,0xfe,0xff)),e=>e.code==="ENCODING");
  assert.throws(()=>gi.decodeGenericText(te.encode("a\0b")),e=>e.code==="ENCODING");
  assert.throws(()=>gi.decodeGenericText(Uint8Array.of(0xff,0)),/invalid/);
  await assert.rejects(()=>gi.genericDocFromBytes(te.encode(" \n\t"),"blank.txt"),e=>e.code==="EMPTY");
  const r=await gi.genericDocFromBytes(te.encode("First line\ncontinued\n\nSecond."),"same.txt");
  assert.deepEqual(r.doc,{title:"same",audio:"",chapters:[],segments:[{id:0,start:0,end:1,text:"First line continued"},{id:1,start:1,end:2,text:"Second."}]});
});

test("Markdown scanner has frozen nonrecursive/literal behavior",()=>{
  assert.equal(gi.markdownInline("**strong** and [label](dest)"),"strong and label");
  assert.equal(gi.markdownInline("\\*literal\\*"),"*literal*");
  assert.equal(gi.markdownInline("[outer [inner](x)]"),"[outer [inner](x)]");
  assert.equal(gi.markdownInline("***overlap***"),"***overlap***");
  assert.equal(gi.markdownInline("[a](nested(x))"),"[a](nested(x))");
  assert.equal(gi.markdownInline("![alt]() [name][ref]"),"alt name");
  assert.equal(gi.markdownInline("~single~"),"~single~");
  assert.equal(gi.markdownInline("`a``b``c`"),"a``b``c");
  assert.equal(gi.markdownInline("`*code*`"),"*code*");
  assert.equal(gi.markdownInline("tail\\"),"tail\\");
});

test("Markdown headings map without dropping terminal content",()=>{
  const mid=gi.projectGeneric(gi.markdownBook(te.encode("# One\n\nBody\n\n# Last"),"b.md"));
  assert.deepEqual(mid.segments.map(x=>x.text),["Body","Last"]);
  assert.deepEqual(mid.chapters,[{title:"One",start:0,seg:0}]);
  const structural=gi.projectGeneric(gi.markdownBook(te.encode("# H\n\n- item\n  continuation\n\n> first\n> second\n\n```js\n<a> raw\n```"),"b.md"));
  assert.deepEqual(structural.segments.map(x=>x.text),["item continuation","first second","<a> raw"]);
});

test("stored, descriptor, and raw-deflate ZIP entries round-trip",async()=>{
  for(const cfg of [{method:0},{method:0,descriptor:true},{method:8}]){
    const bytes=await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"OPS/a.xhtml",text:"hello",...cfg}]);
    const z=gi.parseZip(bytes),out=await gi.readZipEntry(z,z.names.get("OPS/a.xhtml"),{n:0});assert.equal(new TextDecoder().decode(out),"hello");
  }
});

test("ZIP traversal, flags, gaps, ZIP64 extras, and CRC fail closed",async()=>{
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"../x",text:"x"}])),e=>e.code==="ZIP_PATH");
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"x",flags:1}])),e=>e.code==="ZIP_FLAGS");
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip",gap:1},{name:"x",text:"x"}])),e=>e.code==="ZIP_GAP");
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"x",localExtra:Uint8Array.of(1,0,0,0)}])),e=>e.code==="ZIP64");
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"x",centralExtra:Uint8Array.of(1,0,0,0)}])),e=>e.code==="ZIP64");
  const bytes=await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"hello",method:8}]),z=gi.parseZip(bytes);z.bytes[z.names.get("x").data]^=1;
  await assert.rejects(()=>gi.readZipEntry(z,z.names.get("x"),{n:0}));
});

test("href and lexical DOCTYPE boundaries are exact",()=>{
  assert.deepEqual(gi.resolveHref("OPS/a.xhtml","#here"),{path:"OPS/a.xhtml",fragment:"here"});
  assert.throws(()=>gi.resolveHref("OPS/a.xhtml","b#x#y"),e=>e.code==="HREF");
  assert.throws(()=>gi.resolveHref("OPS/a.xhtml","../../x"),e=>e.code==="HREF");
  assert.throws(()=>gi.resolveHref("OPS/a.xhtml","x%2Fy"),e=>e.code==="HREF");
  gi.checkXmlDoctype("<!-- <!DOCTYPE bad> --><html/>",true);
  gi.checkXmlDoctype("<!DoCtYpE html><html/>",true);
  assert.throws(()=>gi.checkXmlDoctype("<!DOCTYPE html [<!ENTITY x 'y'>]><html/>",true),e=>e.code==="XML_DOCTYPE");
  assert.throws(()=>gi.checkXmlDoctype("<!DOCTYPE html><!DOCTYPE html><html/>",true),e=>e.code==="XML_DOCTYPE");
});

test("resource ceilings reject declared expansion before decompression",async()=>{
  const bytes=await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"x",method:8}]);
  const view=new DataView(bytes.buffer);let e=bytes.length-22,co=view.getUint32(e+16,true);co+=46+view.getUint16(co+28,true)+view.getUint16(co+30,true)+view.getUint16(co+32,true);view.setUint32(co+24,gi.GI_LIMIT.entry+1,true);
  assert.throws(()=>gi.parseZip(bytes),e=>e.code==="ZIP_BOMB");
});

test("stable identity and position keys isolate same-title generic books",async()=>{
  const a=await gi.genericIdentity(te.encode("one"),"txt"),a2=await gi.genericIdentity(te.encode("one"),"txt"),b=await gi.genericIdentity(te.encode("two"),"txt");
  assert.equal(a,a2);assert.notEqual(a,b);assert.match(a,/^generic-v1:txt:[0-9a-f]{64}$/);
  assert.notEqual(gi.genericPositionKey("text",a,"Shared"),gi.genericPositionKey("text",b,"Shared"));
  assert.equal(gi.genericPositionKey("audio","","Shared"),"inn-reader-pos:Shared");
});
