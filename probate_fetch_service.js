const http = require('http');
const fs = require('fs');
const cp = require('child_process');
const crypto = require('crypto');

const ORIGIN = 'https://www.southcarolinaprobate.net';
const TARGET = ORIGIN + '/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e';
let result = {status:'starting'};

function pick(body,re){const m=body.match(re);return m?m[1]:null;}
function cookies(headers){const a=headers.getSetCookie?headers.getSetCookie():[];return a.map(x=>x.split(';')[0]).join('; ');}
function xmlDecode(s){return s.replace(/&#x([0-9a-f]+);/gi,(_,h)=>String.fromCodePoint(parseInt(h,16))).replace(/&#(\d+);/g,(_,d)=>String.fromCodePoint(+d)).replace(/&quot;/g,'"').replace(/&apos;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');}
async function buf(url,opts={}){const r=await fetch(url,opts);const b=Buffer.from(await r.arrayBuffer());return {r,b};}

async function inspect(){
 try{
  const common={'user-agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36','accept-language':'en-US,en;q=0.9'};
  const p=await fetch(TARGET,{redirect:'follow',headers:{...common,accept:'text/html,*/*'}}); const body=await p.text(); const ck=cookies(p.headers);
  const c={stateId:pick(body,/stateId:\s*\\?"([^"\\]+)\\?"/),sid:pick(body,/sid:\s*\\?"([^"\\]+)\\?"/),sidParameter:pick(body,/sidParameter:\s*\\?"([^"\\]+)\\?"/),handlerUrl:pick(body,/handlerUrl:\s*\\?"([^"\\]+)\\?"/),cacheInfoKey:pick(body,/cacheInfoKey:\s*\\?"([^"\\]+)\\?"/)};
  const handler=ORIGIN+(c.handlerUrl||'/search/documentviewer.ashx');
  const headers={...common,referer:TARGET,cookie:ck};

  const prepUrl=handler+'/PrepareDocument';
  const prep=await fetch(prepUrl,{method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify({cacheInfoKey:c.cacheInfoKey,stateId:c.stateId})});
  const prepText=await prep.text();
  console.log('PREPARE',prep.status,prepText.slice(0,10000));
  let lastModified=null; try{const j=JSON.parse(prepText); lastModified=j?.Result?.lastModified||j?.result?.lastModified||null;}catch{}

  const q=new URLSearchParams({cacheInfoKey:c.cacheInfoKey,stateId:c.stateId}); if(lastModified) q.set('v',lastModified);
  const dl=await buf(handler+'/DownloadDocument?'+q.toString(),{headers:{...headers,accept:'*/*'}});
  fs.writeFileSync('/tmp/probate.xpz',dl.b);
  console.log('XPZ',JSON.stringify({status:dl.r.status,length:dl.b.length,sha256:crypto.createHash('sha256').update(dl.b).digest('hex'),type:dl.r.headers.get('content-type'),disp:dl.r.headers.get('content-disposition')}));

  const entries=cp.execFileSync('unzip',['-Z1','/tmp/probate.xpz'],{encoding:'utf8',maxBuffer:10*1024*1024}).trim().split(/\r?\n/).filter(Boolean);
  console.log('ZIP_ENTRIES_BEGIN'); console.log(JSON.stringify(entries,null,2)); console.log('ZIP_ENTRIES_END');

  const pages=entries.filter(e=>/^Pages\/\d+\.xaml$/i.test(e)).sort((a,b)=>parseInt(a.match(/\d+/)[0])-parseInt(b.match(/\d+/)[0]));
  const pageText=[];
  for(const e of pages){
    const x=cp.execFileSync('unzip',['-p','/tmp/probate.xpz',e],{encoding:'utf8',maxBuffer:20*1024*1024});
    const strings=[];
    for(const m of x.matchAll(/UnicodeString="([\s\S]*?)"/g)) strings.push(xmlDecode(m[1]));
    for(const m of x.matchAll(/UnicodeString='([\s\S]*?)'/g)) strings.push(xmlDecode(m[1]));
    const joined=strings.join(' ').replace(/\s+/g,' ').trim();
    pageText.push({page:parseInt(e.match(/\d+/)[0]),entry:e,text:joined,xamlLength:x.length,unicodeRuns:strings.length});
  }
  console.log('PAGE_TEXT_BEGIN'); console.log(JSON.stringify(pageText,null,2)); console.log('PAGE_TEXT_END');

  const images=entries.filter(e=>/\.(png|jpe?g|tif?f|bmp|gif)$/i.test(e));
  const imageInfo=[];
  for(const e of images.slice(0,100)){
    const b=cp.execFileSync('unzip',['-p','/tmp/probate.xpz',e],{encoding:null,maxBuffer:30*1024*1024});
    imageInfo.push({entry:e,length:b.length,first16:b.subarray(0,16).toString('hex')});
  }
  console.log('IMAGE_INFO_BEGIN'); console.log(JSON.stringify(imageInfo,null,2)); console.log('IMAGE_INFO_END');

  const alternatives=[];
  for(const method of ['DownloadAsPdf','DownloadSource']){
    try{
      const qq=new URLSearchParams({cacheInfoKey:c.cacheInfoKey,stateId:c.stateId});
      const rr=await buf(handler+'/'+method+'?'+qq.toString(),{headers:{...headers,accept:'*/*'}});
      alternatives.push({method,status:rr.r.status,length:rr.b.length,type:rr.r.headers.get('content-type'),disp:rr.r.headers.get('content-disposition'),first16:rr.b.subarray(0,16).toString('hex'),sha256:crypto.createHash('sha256').update(rr.b).digest('hex')});
      if(rr.r.status===200 && rr.b.length>1000) fs.writeFileSync('/tmp/'+method,rr.b);
    }catch(e){alternatives.push({method,error:String(e)});}
  }
  console.log('ALTERNATIVES',JSON.stringify(alternatives,null,2));
  result={status:'done',config:c,prepare:prepText,pageText,entries,images:imageInfo,alternatives};
 }catch(e){result={status:'error',error:String(e&&e.stack||e)};console.error('FATAL',result);}
}
inspect();
http.createServer((req,res)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(result));}).listen(process.env.PORT||10000,'0.0.0.0',()=>console.log('listening'));
