const http=require('http'); const crypto=require('crypto');
const O='https://www.southcarolinaprobate.net', T=O+'/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e'; let result={status:'starting'};
function pick(s,r){const m=s.match(r);return m?m[1]:null;} function ck(h){const a=h.getSetCookie?h.getSetCookie():[];return a.map(x=>x.split(';')[0]).join('; ');} async function getb(u,o){const r=await fetch(u,o);return {r,b:Buffer.from(await r.arrayBuffer())};}
async function main(){try{
 const common={'user-agent':'Mozilla/5.0 Chrome/140 Safari/537.36','accept-language':'en-US,en;q=0.9'}; const p=await fetch(T,{headers:common}); const body=await p.text(), cookie=ck(p.headers);
 const c={stateId:pick(body,/stateId:\s*\\?"([^"\\]+)\\?"/),sid:pick(body,/sid:\s*\\?"([^"\\]+)\\?"/),sidParameter:pick(body,/sidParameter:\s*\\?"([^"\\]+)\\?"/),handlerUrl:pick(body,/handlerUrl:\s*\\?"([^"\\]+)\\?"/),cacheInfoKey:pick(body,/cacheInfoKey:\s*\\?"([^"\\]+)\\?"/)}; const H=O+c.handlerUrl; const headers={...common,referer:T,cookie,accept:'*/*'};
 const prep=await fetch(H+'/PrepareDocument',{method:'POST',headers:{...headers,'content-type':'application/json'},body:JSON.stringify({cacheInfoKey:c.cacheInfoKey,stateId:c.stateId})}); console.log('PREP',prep.status,await prep.text());
 const out=[]; for(const method of ['DownloadAsPdf','DownloadSource','DownloadDocument']){const q=new URLSearchParams({cacheInfoKey:c.cacheInfoKey,stateId:c.stateId}); const rr=await getb(H+'/'+method+'?'+q.toString(),{headers}); const info={method,status:rr.r.status,length:rr.b.length,type:rr.r.headers.get('content-type'),disp:rr.r.headers.get('content-disposition'),first64:rr.b.subarray(0,64).toString('hex'),sha256:crypto.createHash('sha256').update(rr.b).digest('hex')}; out.push(info); console.log('DOWNLOAD_RESULT',JSON.stringify(info));}
 result={status:'done',config:c,downloads:out};
}catch(e){result={status:'error',error:String(e&&e.stack||e)};console.error('FATAL',result);}}
main(); http.createServer((q,s)=>{s.writeHead(200,{'content-type':'application/json'});s.end(JSON.stringify(result));}).listen(process.env.PORT||10000,'0.0.0.0',()=>console.log('listening'));
