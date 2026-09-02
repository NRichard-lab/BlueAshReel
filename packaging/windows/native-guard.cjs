'use strict';

// Installed Node preload. DNS Client can send resolver traffic from a Windows
// system service, so executable firewall rules alone are not a DNS privacy gate.
// Reject resolution BEFORE calling Windows; allow only literal loopback traffic.
const dns = require('node:dns');
const net = require('node:net');
const dgram = require('node:dgram');
const fs = require('node:fs');
const http = require('node:http');

function localOnlyError() {
  const error = new Error('BlueReel strict-local mode blocked a network operation');
  error.code = 'ERR_BLUEREEL_LOCAL_ONLY';
  return error;
}

function loopback(host) {
  return host === undefined || host === 'localhost' || host === '::1'
    || (typeof host === 'string' && net.isIP(host) === 4 && host.startsWith('127.'));
}

dns.lookup = function lookup(host, options, callback) {
  if (typeof options === 'function') { callback = options; options = {}; }
  options ||= {};
  if (typeof callback !== 'function') throw localOnlyError();
  if (!loopback(host)) { process.nextTick(callback, localOnlyError()); return; }
  const family = host === '::1' || options === 6 || options.family === 6 ? 6 : 4;
  const address = family === 6 ? '::1' : (host && net.isIP(host) ? host : '127.0.0.1');
  process.nextTick(() => options.all
    ? callback(null, [{ address, family }]) : callback(null, address, family));
};
dns.promises.lookup = async function lookup(host, options = {}) {
  if (!loopback(host)) throw localOnlyError();
  const family = host === '::1' || options === 6 || options.family === 6 ? 6 : 4;
  const record = { address: family === 6 ? '::1' : (host && net.isIP(host) ? host : '127.0.0.1'), family };
  return options.all ? [record] : record;
};
const resolverMethods = ['resolve', 'resolve4', 'resolve6', 'resolveAny', 'resolveCaa', 'resolveCname',
  'resolveMx', 'resolveNaptr', 'resolveNs', 'resolvePtr', 'resolveSoa', 'resolveSrv', 'resolveTxt', 'reverse'];
function denyCallback(...args) {
  const callback = args.at(-1);
  if (typeof callback === 'function') process.nextTick(callback, localOnlyError());
  else throw localOnlyError();
}
for (const method of resolverMethods) {
  dns[method] = denyCallback;
  dns.promises[method] = async () => { throw localOnlyError(); };
  if (dns.Resolver.prototype[method]) dns.Resolver.prototype[method] = denyCallback;
  if (dns.promises.Resolver.prototype[method]) {
    dns.promises.Resolver.prototype[method] = async () => { throw localOnlyError(); };
  }
}
dns.lookupService = denyCallback;
dns.promises.lookupService = async () => { throw localOnlyError(); };
const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function connect(...args) {
  const normalized = Array.isArray(args[0]) ? args[0] : args;
  const options = normalized[0];
  if (typeof options === 'object' && options !== null) {
    if (options.path || !loopback(options.host)) throw localOnlyError();
  } else if (!/^\d+$/.test(String(options)) || (typeof normalized[1] === 'string' && !loopback(normalized[1]))) {
    throw localOnlyError();
  }
  return originalConnect.apply(this, args);
};
for (const method of ['connect', 'send']) {
  dgram.Socket.prototype[method] = function denyDatagram() { throw localOnlyError(); };
}

// Service shutdown is private filesystem IPC, not an unauthenticated HTTP route.
const servers = new Set();
const originalListen = http.Server.prototype.listen;
http.Server.prototype.listen = function listen(...args) {
  servers.add(this);
  this.once('close', () => servers.delete(this));
  return originalListen.apply(this, args);
};
let stopping = false;
if (process.env.BLUEREEL_STOP_FILE) {
  const timer = setInterval(() => {
    if (stopping || !fs.existsSync(process.env.BLUEREEL_STOP_FILE)) return;
    stopping = true;
    clearInterval(timer);
    for (const server of servers) {
      server.close();
      server.closeIdleConnections?.();
    }
    const deadline = setTimeout(() => {
      for (const server of servers) server.closeAllConnections?.();
      process.exit(0);
    }, 10000);
    deadline.unref();
  }, 250);
  timer.unref();
}
