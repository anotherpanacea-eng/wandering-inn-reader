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
const names=["GI_LIMIT","genericErrorMessage","decodeGenericText","decodeXmlBytes","txtBook","markdownInline","markdownBook","projectGeneric","crc32","parseZip","readZipEntry","checkXmlDoctype","resolveHref","selectOpfPath","assembleOpfModel","assembleEpubBook","genericDocFromBytes","genericIdentityResult","genericIdentity","genericPositionKey"];
writeFileSync(modPath,html.slice(a,b)+`\nexport {${names.join(",")}};\n`);
after(()=>{try{rmSync(modPath);}catch{}});
const gi=await import(pathToFileURL(modPath).href);

const te=new TextEncoder();
const le16=n=>Uint8Array.of(n&255,(n>>>8)&255);
const le32=n=>Uint8Array.of(n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255);
const cat=(...xs)=>{const n=xs.reduce((s,x)=>s+x.length,0),o=new Uint8Array(n);let p=0;for(const x of xs){o.set(x,p);p+=x.length;}return o;};
async function rawDeflate(bytes){const rd=new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw")).getReader(),cs=[];for(;;){const x=await rd.read();if(x.done)break;cs.push(x.value);}return cat(...cs);}
async function makeZip(specs){const locals=[],centrals=[];let off=0;for(const s of specs){const name=te.encode(s.name),plain=te.encode(s.text),method=s.method||0,comp=method===8?await rawDeflate(plain):plain,crc=gi.crc32(plain),flags=(s.flags||0)|(s.descriptor?8:0),extra=s.localExtra||new Uint8Array(),desc=s.descriptor?(s.descriptor==="nosig"?cat(le32(crc),le32(comp.length),le32(plain.length)):cat(le32(0x08074b50),le32(crc),le32(comp.length),le32(plain.length))):new Uint8Array(),local=cat(le32(0x04034b50),le16(20),le16(flags),le16(method),le16(0),le16(0),le32(s.descriptor?0:crc),le32(s.descriptor?0:comp.length),le32(s.descriptor?0:plain.length),le16(name.length),le16(extra.length),name,extra,comp,desc),gap=new Uint8Array(s.gap||0),centralExtra=s.centralExtra||new Uint8Array();locals.push(cat(local,gap));centrals.push(cat(le32(0x02014b50),le16(20),le16(20),le16(flags),le16(method),le16(0),le16(0),le32(crc),le32(comp.length),le32(plain.length),le16(name.length),le16(centralExtra.length),le16(0),le16(s.diskStart||0),le16(0),le32(0),le32(off),name,centralExtra));off+=local.length+gap.length;}const central=cat(...centrals),eocd=cat(le32(0x06054b50),le16(0),le16(0),le16(specs.length),le16(specs.length),le32(central.length),le32(off),le16(0));return cat(...locals,central,eocd);}

test("strict text decoding and TXT projection",async()=>{
  assert.equal(gi.decodeGenericText(te.encode("a\r\n\r\nb")).text,"a\n\nb");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xef,0xbb,0xbf,0x61)).text,"a");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xff,0xfe,0x41,0,0x42,0)).text,"AB");
  assert.equal(gi.decodeGenericText(Uint8Array.of(0xfe,0xff,0,0x41,0,0x42)).text,"AB");
  assert.throws(()=>gi.decodeGenericText(Uint8Array.of(0,0,0xfe,0xff)),e=>e.code==="ENCODING");
  assert.throws(()=>gi.decodeGenericText(Uint8Array.of(0x2b,0x2f,0x76,0x38,0x2d,0x61)),e=>e.code==="ENCODING");
  assert.throws(()=>gi.decodeGenericText(te.encode("a\0b")),e=>e.code==="ENCODING");
  assert.throws(()=>gi.decodeGenericText(Uint8Array.of(0xff,0)),/invalid/);
  assert.equal([...gi.genericErrorMessage("A".repeat(200)+"\nCONTROL.opf")].length,160);
  assert.equal(gi.genericErrorMessage("bad\npath\u007f.opf").includes("\n"),false);
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
  assert.equal(gi.markdownInline("`a``b``c`"),"`a``b``c`");
  assert.equal(gi.markdownInline("``double``"),"``double``");
  assert.equal(gi.markdownInline("before `one` after"),"before one after");
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

const archivePaths=new Set(["OPS/package.opf","OPS/nav.xhtml","OPS/c1.xhtml","OPS/c2.xhtml","OPS/skip.xhtml"]);
const hasEntry=p=>archivePaths.has(p);
const opfModel=()=>({
  version:"3.0",
  titles:["Fixture Book"],
  metas:[],
  items:[
    {id:"nav",href:"nav.xhtml",media:"application/xhtml+xml",properties:"nav",fallback:false},
    {id:"c1",href:"c1.xhtml",media:"application/xhtml+xml",properties:"",fallback:false},
    {id:"c2",href:"c2.xhtml",media:"application/xhtml+xml",properties:"",fallback:false},
    {id:"skip",href:"skip.xhtml",media:"application/xhtml+xml",properties:"",fallback:false},
  ],
  spine:[
    {idref:"c1",linear:""},
    {idref:"skip",linear:"no"},
    {idref:"c2",linear:""},
  ],
});
const resourceModels=()=>new Map([
  ["OPS/c1.xhtml",{blocks:[
    {text:"One",kind:"heading",resourcePath:"OPS/c1.xhtml"},
    {text:"First synthetic paragraph.",kind:"prose",resourcePath:"OPS/c1.xhtml"},
  ],anchors:[{id:"one",blockIndex:0}]}],
  ["OPS/c2.xhtml",{blocks:[
    {text:"Two",kind:"heading",resourcePath:"OPS/c2.xhtml"},
    {text:"Second synthetic paragraph.",kind:"prose",resourcePath:"OPS/c2.xhtml"},
  ],anchors:[{id:"two",blockIndex:0}]}],
]);

test("pure EPUB semantic assembly yields the complete projected document",()=>{
  const container={rootfiles:[
    {mediaType:"application/unknown",fullPath:"ignored.opf"},
    {mediaType:"application/oebps-package+xml",fullPath:"OPS/package.opf"},
  ]};
  const op=gi.selectOpfPath(container,hasEntry);
  assert.equal(op,"OPS/package.opf");
  const pkg=gi.assembleOpfModel(opfModel(),op,"fallback.epub",hasEntry);
  assert.equal(pkg.warnings.length,1);
  const book=gi.assembleEpubBook(pkg,resourceModels(),{toc:true,candidates:[
    {label:"One",href:"c1.xhtml#one"},
    {label:"Two",href:"c2.xhtml#two"},
  ]});
  assert.deepEqual(gi.projectGeneric(book),{
    title:"Fixture Book",
    audio:"",
    chapters:[{title:"One",start:0,seg:0},{title:"Two",start:1,seg:1}],
    segments:[
      {id:0,start:0,end:1,text:"First synthetic paragraph."},
      {id:1,start:1,end:2,text:"Second synthetic paragraph."},
    ],
  });
});

test("pure container and OPF semantics fail closed across the complete structural boundary",()=>{
  assert.throws(()=>gi.selectOpfPath({rootfiles:[]},hasEntry),e=>e.code==="EPUB_CONTAINER");
  assert.throws(()=>gi.selectOpfPath({rootfiles:[{mediaType:"application/oebps-package+xml",fullPath:" "}]},hasEntry),e=>e.code==="EPUB_CONTAINER");
  assert.throws(()=>gi.selectOpfPath({rootfiles:[{mediaType:"application/oebps-package+xml",fullPath:"OPS/missing.opf"}]},hasEntry),e=>e.code==="EPUB_RESOURCE");
  const cases=[
    ["non-3 version",m=>{m.version="2.0";},"EPUB_VERSION"],
    ["fixed package",m=>{m.metas.push({property:"rendition:layout",text:"pre-paginated"});},"EPUB_SPINE"],
    ["blank manifest id",m=>{m.items[1].id="";},"EPUB_MANIFEST"],
    ["duplicate manifest id",m=>{m.items[2].id="c1";},"EPUB_MANIFEST"],
    ["blank href",m=>{m.items[1].href="";},"EPUB_MANIFEST"],
    ["duplicate resource",m=>{m.items[2].href="c1.xhtml";},"EPUB_MANIFEST"],
    ["fallback chain",m=>{m.items[1].fallback=true;},"EPUB_FALLBACK"],
    ["scripted resource",m=>{m.items[1].properties="scripted";},"EPUB_MANIFEST"],
    ["missing nav",m=>{m.items[0].properties="";},"EPUB_NAV"],
    ["multiple nav",m=>{m.items[1].properties="nav";},"EPUB_NAV"],
    ["wrong nav media",m=>{m.items[0].media="text/html";},"EPUB_NAV"],
    ["missing spine idref",m=>{m.spine[0].idref="missing";},"EPUB_SPINE"],
    ["duplicate spine idref",m=>{m.spine[2].idref="c1";},"EPUB_SPINE"],
    ["unsupported spine media",m=>{m.items[1].media="text/plain";},"EPUB_SPINE"],
    ["fixed-layout item",m=>{m.items[1].properties="rendition:layout-pre-paginated";},"EPUB_SPINE"],
    ["empty linear spine",m=>{m.spine.forEach(x=>x.linear="no");},"EPUB_SPINE"],
  ];
  for(const [label,mutate,code] of cases){
    const m=opfModel();mutate(m);
    assert.throws(()=>gi.assembleOpfModel(m,"OPS/package.opf","fallback.epub",hasEntry),e=>e.code===code,label);
  }
});

test("pure spine/nav assembly rejects missing models and preserves warning semantics",()=>{
  const pkg=gi.assembleOpfModel(opfModel(),"OPS/package.opf","fallback.epub",hasEntry);
  assert.throws(()=>gi.assembleEpubBook(pkg,new Map(),{toc:true,candidates:[]}),e=>e.code==="EPUB_RESOURCE");
  const empty=resourceModels();empty.set("OPS/c1.xhtml",{blocks:[],anchors:[]});empty.set("OPS/c2.xhtml",{blocks:[],anchors:[]});
  assert.throws(()=>gi.assembleEpubBook(pkg,empty,{toc:true,candidates:[]}),e=>e.code==="EMPTY");
  assert.throws(()=>gi.assembleEpubBook(pkg,resourceModels(),{toc:false,candidates:[]}),e=>e.code==="EPUB_NAV");
  assert.throws(()=>gi.assembleEpubBook(pkg,resourceModels(),{toc:true,candidates:[{label:"escape",href:"../../x"}]}),e=>e.code==="HREF");
  const book=gi.assembleEpubBook(pkg,resourceModels(),{toc:true,candidates:[
    {label:"external",href:"https://example.invalid/x"},
    {label:"nonlinear",href:"skip.xhtml"},
    {label:"broken",href:"c1.xhtml#missing"},
  ]});
  assert.deepEqual(book.chapterCandidates,[]);
  assert.deepEqual(book.warnings,[
    "skipped non-linear spine item",
    "ignored external TOC link",
    "ignored non-linear TOC link",
    "ignored unresolved TOC link",
    "navigation has no usable chapters",
  ]);
});

test("pure EPUB assembly preserves aliases, Unicode targets, fallback titles, and chapter order",()=>{
  const aliased=resourceModels();
  aliased.get("OPS/c1.xhtml").anchors.push({id:"uno",blockIndex:0});
  const pkg=gi.assembleOpfModel(opfModel(),"OPS/package.opf","fallback.epub",hasEntry);
  const ordered=gi.projectGeneric(gi.assembleEpubBook(pkg,aliased,{toc:true,candidates:[
    {label:"Second first",href:"c2.xhtml"},
    {label:"First alias",href:"c1.xhtml#uno"},
    {label:"First duplicate",href:"c1.xhtml#one"},
  ]}));
  assert.deepEqual(ordered.chapters,[
    {title:"First alias",start:0,seg:0},
    {title:"Second first",start:2,seg:2},
  ]);

  const unicodeModel=opfModel();
  unicodeModel.titles=[" "];
  unicodeModel.items=unicodeModel.items.filter(x=>x.id!=="c2"&&x.id!=="skip");
  unicodeModel.items.find(x=>x.id==="c1").href="caf%C3%A9.xhtml";
  unicodeModel.spine=[{idref:"c1",linear:""}];
  const unicodePaths=new Set(["OPS/package.opf","OPS/nav.xhtml","OPS/café.xhtml"]);
  const unicodePkg=gi.assembleOpfModel(unicodeModel,"OPS/package.opf","fallback.epub",p=>unicodePaths.has(p));
  assert.equal(unicodePkg.title,"fallback");
  const unicodeResources=new Map([["OPS/café.xhtml",{blocks:[
    {text:"Unicode prose.",kind:"prose",resourcePath:"OPS/café.xhtml"},
  ],anchors:[{id:"é",blockIndex:0}]}]]);
  const unicodeDoc=gi.projectGeneric(gi.assembleEpubBook(unicodePkg,unicodeResources,{toc:true,candidates:[
    {label:"Unicode",href:"caf%C3%A9.xhtml#%C3%A9"},
  ]}));
  assert.deepEqual(unicodeDoc.chapters,[{title:"Unicode",start:0,seg:0}]);

  const duplicateAnchors=resourceModels();
  duplicateAnchors.get("OPS/c1.xhtml").anchors.push({id:"one",blockIndex:1});
  assert.throws(()=>gi.assembleEpubBook(pkg,duplicateAnchors,{toc:true,candidates:[]}),e=>e.code==="ANCHOR");
});

test("XML ceiling is enforced on source bytes before decoding",()=>{
  const old=gi.GI_LIMIT.xml;
  try{gi.GI_LIMIT.xml=4;assert.throws(()=>gi.decodeXmlBytes(te.encode("12345")),e=>e.code==="LIMIT");}
  finally{gi.GI_LIMIT.xml=old;}
});

test("additional ZIP and XML structural negatives fail with stable categories",async()=>{
  const trailing=cat(await makeZip([{name:"mimetype",text:"application/epub+zip"}]),Uint8Array.of(0));
  assert.throws(()=>gi.parseZip(trailing),e=>e.code==="ZIP_EOCD");
  const multidisk=await makeZip([{name:"mimetype",text:"application/epub+zip"}]);
  new DataView(multidisk.buffer).setUint16(multidisk.length-18,1,true);
  assert.throws(()=>gi.parseZip(multidisk),e=>e.code==="ZIP_MULTI");
  await assert.rejects(async()=>gi.parseZip(await makeZip([{name:"mimetype",text:"application/epub+zip",diskStart:1}])),e=>e.code==="ZIP_MULTI");
  assert.throws(()=>gi.decodeXmlBytes(te.encode('<?xml version="1.0" encoding="UTF-16"?><x/>')),e=>e.code==="XML_ENCODING");
  assert.throws(()=>gi.decodeXmlBytes(te.encode('<?xml encoding="UTF-8" encoding="UTF-8"?><x/>')),e=>e.code==="XML_DECL");
});

test("cancellation is checked after awaited decompression and stops the reader",async()=>{
  const bytes=await makeZip([{name:"mimetype",text:"application/epub+zip"},{name:"x",text:"cancel ".repeat(5000),method:8}]);
  const zip=gi.parseZip(bytes),stale=Object.assign(new Error("stale"),{code:"STALE_IMPORT"});let checks=0;
  await assert.rejects(()=>gi.readZipEntry(zip,zip.names.get("x"),{n:0},()=>{if(++checks>1)throw stale;}),e=>e===stale);
  assert.ok(checks>1);
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
  const fallback=await gi.genericIdentityResult(te.encode("one"),"txt",null);
  assert.equal(fallback.stable,false);assert.match(fallback.identity,/^ephemeral:[0-9a-f]{32}$/);
});
