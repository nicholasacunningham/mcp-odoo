const http = require('http');
const crypto = require('crypto');

const ORIGIN = 'https://www.southcarolinaprobate.net';
const TARGET = ORIGIN + '/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e';
let result = { status: 'starting' };

function pick(body, re) {
  const m = body.match(re);
  return m ? m[1] : null;
}
function cookieHeader(headers) {
  const setCookie = headers.getSetCookie ? headers.getSetCookie() : [];
  return setCookie.map(x => x.split(';')[0]).join('; ');
}
function snippets(text, terms) {
  const out = {};
  for (const term of terms) {
    const hits = [];
    let i = 0;
    while ((i = text.indexOf(term, i)) !== -1 && hits.length < 8) {
      hits.push(text.slice(Math.max(0, i - 450), Math.min(text.length, i + term.length + 650)));
      i += term.length;
    }
    out[term] = hits;
  }
  return out;
}

async function fetchBuf(url, headers = {}) {
  const r = await fetch(url, { redirect: 'follow', headers });
  const ab = await r.arrayBuffer();
  const buf = Buffer.from(ab);
  return {
    url: r.url,
    status: r.status,
    headers: Object.fromEntries(r.headers.entries()),
    buf,
    text: (() => { try { return buf.toString('utf8'); } catch { return ''; } })()
  };
}

async function inspect() {
  try {
    const common = {
      'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
      'accept-language': 'en-US,en;q=0.9'
    };
    const p = await fetch(TARGET, { redirect:'follow', headers:{...common, accept:'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'} });
    const body = await p.text();
    const cookies = cookieHeader(p.headers);
    const config = {
      stateId: pick(body, /stateId:\s*\\?"([^"\\]+)\\?"/),
      sid: pick(body, /sid:\s*\\?"([^"\\]+)\\?"/),
      sidParameter: pick(body, /sidParameter:\s*\\?"([^"\\]+)\\?"/),
      path: pick(body, /path:\s*\\?"([^"\\]+)\\?"/),
      handlerUrl: pick(body, /handlerUrl:\s*\\?"([^"\\]+)\\?"/),
      cacheInfoKey: pick(body, /cacheInfoKey:\s*\\?"([^"\\]+)\\?"/)
    };
    console.log('CONFIG', JSON.stringify({config, cookies}, null, 2));

    const viewerJsUrl = ORIGIN + '/search/resource.ashx/638178877840000000/viewer.js';
    const jsr = await fetchBuf(viewerJsUrl, {...common, referer:TARGET, cookie:cookies});
    const trace = snippets(jsr.text, ['DownloadDocument','PrepareDocument','cacheInfoKey','sidParameter','GetPage','PageText','SearchDocument','DownloadFile']);
    console.log('VIEWER_JS_TRACE_BEGIN');
    console.log(JSON.stringify({status:jsr.status, length:jsr.buf.length, trace}, null, 2));
    console.log('VIEWER_JS_TRACE_END');

    const baseHandler = ORIGIN + (config.handlerUrl || '/search/documentviewer.ashx');
    const q = new URLSearchParams();
    if (config.stateId) q.set('stateId', config.stateId);
    if (config.cacheInfoKey) q.set('cacheInfoKey', config.cacheInfoKey);
    if (config.sidParameter && config.sid) q.set(config.sidParameter, config.sid);
    q.set('_', Date.now().toString());
    const endpoints = ['DownloadDocument','PrepareDocument'];
    const attempts = [];
    for (const ep of endpoints) {
      const url = `${baseHandler}/${ep}?${q.toString()}`;
      try {
        const rr = await fetchBuf(url, {...common, referer:TARGET, cookie:cookies, accept:'*/*'});
        attempts.push({
          ep, url, status:rr.status, headers:rr.headers, length:rr.buf.length,
          sha256: crypto.createHash('sha256').update(rr.buf).digest('hex'),
          first64hex: rr.buf.subarray(0,64).toString('hex'),
          textStart: rr.text.slice(0,3000)
        });
      } catch (e) {
        attempts.push({ep, error:String(e && e.stack || e)});
      }
    }
    console.log('ENDPOINT_ATTEMPTS_BEGIN');
    console.log(JSON.stringify(attempts, null, 2));
    console.log('ENDPOINT_ATTEMPTS_END');
    result = {status:'done', config, cookies, viewerTrace:trace, attempts};
  } catch (e) {
    result = { status: 'error', error: String(e && e.stack || e) };
    console.error('FATAL', result);
  }
}

inspect();
http.createServer((req,res)=>{res.writeHead(200,{'content-type':'application/json; charset=utf-8'});res.end(JSON.stringify(result));})
  .listen(process.env.PORT || 10000, '0.0.0.0', ()=>console.log('listening'));
