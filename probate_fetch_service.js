const http = require('http');

const TARGET = 'https://www.southcarolinaprobate.net/search/ViewImage.aspx?id=61fd5200-b215-49a2-ba59-0ca38cc3aa3e';
let result = { status: 'starting' };

async function inspect() {
  try {
    const r = await fetch(TARGET, {
      redirect: 'follow',
      headers: {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9'
      }
    });
    const body = await r.text();
    const urls = [...body.matchAll(/(?:src|href|url|action)\s*=\s*["']([^"']+)["']/gi)].map(m => m[1]);
    const quoted = [...body.matchAll(/["']([^"']*(?:ashx|asmx|pdf|tif|tiff|jpg|jpeg|png|image|document|download|viewer|api)[^"']*)["']/gi)].map(m => m[1]);
    result = {
      status: 'done',
      httpStatus: r.status,
      finalUrl: r.url,
      headers: Object.fromEntries(r.headers.entries()),
      length: body.length,
      urls: [...new Set(urls)],
      candidates: [...new Set(quoted)],
      body
    };
    console.log('PROBATE_FETCH_RESULT_BEGIN');
    console.log(JSON.stringify({...result, body: body.slice(0, 120000)}, null, 2));
    console.log('PROBATE_FETCH_RESULT_END');
  } catch (e) {
    result = { status: 'error', error: String(e && e.stack || e) };
    console.error(result);
  }
}

inspect();
const server = http.createServer((req, res) => {
  res.writeHead(200, {'content-type':'application/json; charset=utf-8'});
  res.end(JSON.stringify(result));
});
server.listen(process.env.PORT || 10000, '0.0.0.0', () => console.log('listening'));
