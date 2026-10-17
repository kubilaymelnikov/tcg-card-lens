// Selbst signiertes Zertifikat für den LAN-Modus. Handy-Browser geben die Kamera nur über HTTPS frei.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { networkInterfaces } from 'node:os';
import { join } from 'node:path';
import { DATA_DIR } from './paths.js';

const DIR = join(DATA_DIR, 'certs');
const OPENSSL = ['openssl', 'C:\\Program Files\\Git\\mingw64\\bin\\openssl.exe', 'C:\\Program Files\\Git\\usr\\bin\\openssl.exe'];

/** IPv4-Adressen dieses Rechners im lokalen Netz. */
export function lanAddresses() {
  return Object.values(networkInterfaces())
    .flat()
    .filter((a) => a && a.family === 'IPv4' && !a.internal)
    .map((a) => a.address);
}

function openssl(args) {
  for (const bin of OPENSSL) {
    try {
      return execFileSync(bin, args, { stdio: 'pipe' });
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
  }
  throw new Error('openssl nicht gefunden (z. B. über Git for Windows installieren)');
}

/** Liefert { key, cert }; erzeugt ein neues Zertifikat, wenn sich die IP-Adressen geändert haben. */
export function ensureCert() {
  const ips = ['127.0.0.1', ...lanAddresses()].sort();
  const keyFile = join(DIR, 'key.pem');
  const certFile = join(DIR, 'cert.pem');
  const ipsFile = join(DIR, 'ips.txt');
  const fresh = existsSync(certFile) && existsSync(ipsFile) && readFileSync(ipsFile, 'utf8') === ips.join(',');
  if (!fresh) {
    mkdirSync(DIR, { recursive: true });
    const san = ['DNS:localhost', ...ips.map((ip) => `IP:${ip}`)].join(',');
    openssl([
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '825',
      '-keyout', keyFile, '-out', certFile, '-subj', '/CN=TCG Card Lens (lokal)', '-addext', `subjectAltName=${san}`,
    ]);
    writeFileSync(ipsFile, ips.join(','));
  }
  return { key: readFileSync(keyFile), cert: readFileSync(certFile) };
}
