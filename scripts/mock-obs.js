// Entwicklungs-Werkzeug: simuliert den OBS-WebSocket-Server (v5) inkl. Passwort-Authentifizierung,
// damit sich das Overlay ohne OBS testen lässt.
//
//   node scripts/mock-obs.js [bild.jpg]      # Passwort: "test", Port 4455
//
// Liefert für GetSourceScreenshot das angegebene Bild bzw. das zuletzt per POST /frame (Data-URL) gesendete.
import { createServer } from 'node:http';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';

const PORT = Number(process.env.PORT || 4455);
const PASSWORD = process.env.OBS_PASSWORD ?? 'test';
let frame = process.argv[2] ? `data:image/jpeg;base64,${readFileSync(process.argv[2]).toString('base64')}` : null;

const b64sha = (s) => createHash('sha256').update(s).digest('base64');

function send(socket, obj) {
  const payload = Buffer.from(JSON.stringify(obj));
  const len = payload.length;
  const head = len < 126 ? Buffer.from([0x81, len]) : len < 65536 ? Buffer.alloc(4) : Buffer.alloc(10);
  if (len >= 126 && len < 65536) {
    head[0] = 0x81;
    head[1] = 126;
    head.writeUInt16BE(len, 2);
  } else if (len >= 65536) {
    head[0] = 0x81;
    head[1] = 127;
    head.writeBigUInt64BE(BigInt(len), 2);
  }
  socket.write(Buffer.concat([head, payload]));
}

function* readFrames(state, chunk) {
  state.buf = Buffer.concat([state.buf, chunk]);
  for (;;) {
    const b = state.buf;
    if (b.length < 2) return;
    const opcode = b[0] & 0x0f;
    let len = b[1] & 0x7f;
    let off = 2;
    if (len === 126) (len = b.readUInt16BE(2)), (off = 4);
    else if (len === 127) (len = Number(b.readBigUInt64BE(2))), (off = 10);
    const masked = b[1] & 0x80;
    const total = off + (masked ? 4 : 0) + len;
    if (b.length < total) return;
    const mask = masked ? b.subarray(off, off + 4) : null;
    const data = Buffer.from(b.subarray(off + (masked ? 4 : 0), total));
    if (mask) for (let i = 0; i < data.length; i++) data[i] ^= mask[i % 4];
    state.buf = b.subarray(total);
    yield { opcode, data };
  }
}

const server = createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.method === 'POST' && req.url === '/frame') {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      frame = body;
      res.end('ok');
    });
    return;
  }
  res.end('mock-obs');
});

server.on('upgrade', (req, socket) => {
  const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
  socket.write(
    `HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n`
  );
  const salt = randomBytes(16).toString('base64');
  const challenge = randomBytes(16).toString('base64');
  const expected = b64sha(b64sha(PASSWORD + salt) + challenge);
  send(socket, { op: 0, d: { obsWebSocketVersion: '5.5.0', rpcVersion: 1, ...(PASSWORD ? { authentication: { salt, challenge } } : {}) } });

  const state = { buf: Buffer.alloc(0) };
  socket.on('data', (chunk) => {
    for (const { opcode, data } of readFrames(state, chunk)) {
      if (opcode === 8) return socket.end();
      if (opcode !== 1) continue;
      const { op, d } = JSON.parse(data.toString());
      if (op === 1) {
        if (PASSWORD && d.authentication !== expected) {
          console.log('Authentifizierung fehlgeschlagen');
          socket.write(Buffer.from([0x88, 0x02, 0x0f, 0xa9])); // Close 4009
          return socket.end();
        }
        console.log('Client identifiziert');
        send(socket, { op: 2, d: { negotiatedRpcVersion: 1 } });
      } else if (op === 6) {
        const ok = d.requestType === 'GetSourceScreenshot' && frame;
        send(socket, {
          op: 7,
          d: {
            requestType: d.requestType,
            requestId: d.requestId,
            requestStatus: ok ? { result: true, code: 100 } : { result: false, code: 600, comment: frame ? 'Unbekannte Anfrage' : 'Noch kein Bild' },
            responseData: ok ? { imageData: frame } : undefined,
          },
        });
      }
    }
  });
  socket.on('error', () => {});
});

server.listen(PORT, () => console.log(`Mock-OBS auf ws://localhost:${PORT} (Passwort: "${PASSWORD}")`));
