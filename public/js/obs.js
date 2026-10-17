// Minimaler Client für obs-websocket v5 (in OBS 28+ eingebaut: Werkzeuge -> WebSocket-Server-Einstellungen).

async function sha256b64(text) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(hash)));
}

export class ObsClient {
  constructor(url, password = '') {
    this.url = url;
    this.password = password;
    this.pending = new Map();
    this.counter = 0;
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url, 'obswebsocket.json');
      this.ws = ws;
      ws.onerror = () => reject(new Error(`Keine Verbindung zu ${this.url}`));
      ws.onclose = (e) => {
        for (const p of this.pending.values()) p.reject(new Error('Verbindung geschlossen'));
        this.pending.clear();
        reject(new Error(e.code === 4009 ? 'Falsches OBS-WebSocket-Passwort' : `Verbindung geschlossen (${e.code})`));
        this.onclose?.(e);
      };
      ws.onmessage = async (msg) => {
        const { op, d } = JSON.parse(msg.data);
        if (op === 0) {
          const identify = { rpcVersion: 1, eventSubscriptions: 0 };
          if (d.authentication) {
            const secret = await sha256b64(this.password + d.authentication.salt);
            identify.authentication = await sha256b64(secret + d.authentication.challenge);
          }
          ws.send(JSON.stringify({ op: 1, d: identify }));
        } else if (op === 2) {
          resolve(this);
        } else if (op === 7) {
          const p = this.pending.get(d.requestId);
          if (!p) return;
          this.pending.delete(d.requestId);
          if (d.requestStatus.result) p.resolve(d.responseData || {});
          else p.reject(new Error(d.requestStatus.comment || `OBS-Fehler ${d.requestStatus.code}`));
        }
      };
    });
  }

  request(requestType, requestData = {}) {
    const requestId = String(++this.counter);
    return new Promise((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
      this.ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
    });
  }

  close() {
    this.ws?.close();
  }
}
