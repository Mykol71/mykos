// Deterministic documentation generation; no runtime state or production access.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {Marked}=await import(pathToFileURL(require.resolve('marked')));
const {instance}=await import(pathToFileURL(require.resolve('@viz-js/viz')));
const here=path.dirname(fileURLToPath(import.meta.url)),docs=path.resolve(here,'../../docs');
const check=process.argv.includes('--check');
const css=(await fs.readFile(path.join(here,'theme.css'),'utf8')).trimEnd();
const models=JSON.parse(await fs.readFile(path.join(here,'diagrams.json'),'utf8'));
const viz=await instance();
let count=0;
async function output(relative,data){
 const target=path.join(docs,relative);count++;
 if(check){if(await fs.readFile(target,'utf8')!==data)throw Error('Generated file drift: '+relative);}
 else{await fs.mkdir(path.dirname(target),{recursive:true});await fs.writeFile(target,data);}
}
const q=JSON.stringify;
const wrap=(s,n=30)=>s.split(/\s+/).reduce((lines,w)=>{if(lines.at(-1).length+w.length+1>n)lines.push(w);else lines[lines.length-1]+=(lines.at(-1)?" ":"")+w;return lines;},[""]).join("\n");
for(const model of models){
 const nodes=new Map(model.nodes.map(([id,title,detail])=>[id,`${id} [label=${q(wrap(title,26)+'\n'+wrap(detail,32))}];`]));
 let body='';
 for(const [id,title,ids] of model.groups||[]){body+=`subgraph cluster_${id} {label=${q(title)};color="#a7b7c8";style="rounded,filled";fillcolor="#f8fafc";fontsize=14;fontcolor="#243e58";margin=18;\n${ids.map(id=>{const s=nodes.get(id);nodes.delete(id);return s;}).join('\n')}\n}\n`;}
 body+=[...nodes.values()].join('\n');
 for(const [a,b,label] of model.edges){const kind=model.edgeStyles?.[a+'->'+b]||'control';const style={control:'color="#075dab",fontcolor="#075dab"',data:'color="#217362",fontcolor="#216454"',observe:'color="#6a7785",fontcolor="#596877",style=dashed,constraint=false',return:'color="#217362",fontcolor="#216454",style=dashed,constraint=false',failure:'color="#a23c3c",fontcolor="#913535",style=dashed,constraint=false'}[kind];body+=`\n${a} -> ${b} [label=${q(wrap(label,25))},${style}];`;}
 const dot=`digraph G {graph [rankdir=TB,bgcolor="white",pad=.25,nodesep=.5,ranksep=.65,fontname="Arial",label=${q(wrap(model.title,90)+'\n\n'+wrap(model.note,100))},labelloc=t,fontsize=18,compound=true];node [shape=box,style="rounded,filled",fillcolor="white",color="#8aa4bc",fontcolor="#17324d",fontname="Arial",fontsize=12,margin=".16,.12"];edge [fontname="Arial",fontsize=10,arrowsize=.65,penwidth=1.2];${body}}`;
 await output('diagrams/'+model.name+'.svg',viz.renderString(dot,{format:'svg'}));
 let mmd='flowchart TD\n';
 for(const [id,title,ids] of model.groups||[]){mmd+=` subgraph ${id}[${q(title)}]\n`+ids.map(id=>{const n=model.nodes.find(n=>n[0]===id);return `  ${id}[${q(n[1]+' / '+n[2])}]`;}).join('\n')+'\n end\n';}
 for(const [a,b,label] of model.edges)mmd+=` ${a} ${['return','failure','observe'].includes(model.edgeStyles?.[a+'->'+b])?'-.->':'-->'}|${q(label)}| ${b}\n`;
 await output('diagrams/'+model.name+'.mmd',mmd);
}
const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const slug=s=>s.replace(/<[^>]+>/g,'').toLowerCase().replaceAll('`','').replace(/[^\p{L}\p{N}_\- ]/gu,'').replaceAll(' ','-');
const guides=JSON.parse(await fs.readFile(path.join(here,'guides.json'),'utf8'));
for(const file of guides){
 const seen=new Map();
 const md=new Marked({gfm:true,renderer:{heading({tokens,depth}){
  const text=this.parser.parseInline(tokens),base=slug(text),count=seen.get(base)||0;seen.set(base,count+1);
  return `<h${depth} id="${base}${count?'-'+count:''}">${text}</h${depth}>\n`;
 },image({href,title,text}){
  const image=`<img src="${esc(href)}" alt="${esc(text||'')}"${title?' title="'+esc(title)+'"':''}>`;
  return href.startsWith('diagrams/')?`<a href="${esc(href)}" title="Open full-size diagram">${image}</a><a href="${esc(href)}">Open full-size diagram</a>`:image;
 },link({href,title,tokens}){
  return `<a href="${esc(href.replace(/\.md(?=$|#)/,'.html'))}"${title?' title="'+esc(title)+'"':''}>${this.parser.parseInline(tokens)}</a>`;
 }}});
 const text=await fs.readFile(path.join(docs,file),'utf8'),title=text.match(/^# (.+)/m)?.[1]||file;
 const html=`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="color-scheme" content="only light"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title><style>${css}</style><header>MYKOS / DOCUMENTATION · <a href="/mykos/">Dashboard</a> · <a href="/mykos/api/v2/docs">API</a></header><main>${md.parse(text)}<footer>MYKOS documentation</footer></main></html>`;
 await output('rendered/'+file.replace(/\.md$/,'.html'),html);
}
console.log(`${check?'Verified':'Generated'} ${count} files (${models.length} diagrams); permanent light theme.`);
