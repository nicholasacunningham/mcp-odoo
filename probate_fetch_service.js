const http = require('http');
http.createServer((req, res) => {
  res.writeHead(200, {'content-type': 'application/json; charset=utf-8'});
  res.end(JSON.stringify({status: 'temporary extraction complete', documentDataExposed: false}));
}).listen(process.env.PORT || 10000, '0.0.0.0');
