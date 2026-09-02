"""Execute the shipped Node preload against synthetic local-only operations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "native-guard.cjs"


@pytest.fixture
def node() -> str:
    executable = os.getenv("TEST_NODE_PATH") or shutil.which("node")
    if not executable:
        pytest.skip("Bundled Node required; set TEST_NODE_PATH")
    return executable


def run_node(
    node: str, source: str, *, preload: bool = True, stop_file: Path | None = None
) -> dict:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"NODE_OPTIONS", "BLUEREEL_STOP_FILE"}
    }
    environment["TEST_BLUEREEL_GUARD"] = str(GUARD)
    if stop_file:
        environment["BLUEREEL_STOP_FILE"] = str(stop_file)
    command = [node, *(["--require", str(GUARD)] if preload else []), "-e", source]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_literal_loopback_and_localhost_http_work_with_the_real_preload(
    node: str,
) -> None:
    result = run_node(
        node,
        r"""
const assert = require('node:assert/strict');
const http = require('node:http');
const dns = require('node:dns/promises');
(async () => {
  const server = http.createServer((_request, response) => response.end('local fixture'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  const received = [];
  for (const host of ['127.0.0.1', 'localhost']) {
    received.push(await new Promise((resolve, reject) => {
      http.get({ host, port, agent: false }, response => {
        let body = ''; response.on('data', data => body += data);
        response.on('end', () => resolve(body));
      }).on('error', reject);
    }));
  }
  assert.deepEqual(received, ['local fixture', 'local fixture']);
  assert.deepEqual(await dns.lookup('::1'), { address: '::1', family: 6 });
  assert.deepEqual(await dns.lookup('localhost', {all: true}), [{address: '127.0.0.1', family: 4}]);
  await new Promise(resolve => server.close(resolve));
  console.log(JSON.stringify({loopback: true, localhost: true, ipv6_lookup: true}));
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
    )
    assert result == {"loopback": True, "localhost": True, "ipv6_lookup": True}


def test_outbound_tcp_hostname_literal_ip_and_named_pipe_are_blocked_before_os_connect(
    node: str,
) -> None:
    result = run_node(
        node,
        r"""
const assert = require('node:assert/strict');
const net = require('node:net');
let operatingSystemCalls = 0;
net.Socket.prototype.connect = function () {
  operatingSystemCalls++; throw new Error('OS connect must not run');
};
require(process.env.TEST_BLUEREEL_GUARD);
for (const operation of [
  () => net.connect({host: 'forbidden.example.invalid', port: 443}),
  () => net.connect({host: '192.0.2.1', port: 443}),
  () => new net.Socket().connect(443, '192.0.2.1'),
  () => new net.Socket().connect(443, 'forbidden.example.invalid'),
  () => net.connect({path: String.raw`\\remote-test\pipe\private`}),
  () => net.connect({host: '2001:db8::1', port: 443}),
]) assert.throws(operation, {code: 'ERR_BLUEREEL_LOCAL_ONLY'});
assert.equal(operatingSystemCalls, 0);
console.log(JSON.stringify({operating_system_connects: operatingSystemCalls, blocked: 6}));
""",
        preload=False,
    )
    assert result == {"operating_system_connects": 0, "blocked": 6}


def test_dns_callback_promises_resolver_and_direct_queries_never_reach_resolver(
    node: str,
) -> None:
    result = run_node(
        node,
        r"""
const assert = require('node:assert/strict');
const dns = require('node:dns');
const promises = require('node:dns/promises');
let underlyingQueries = 0;
const fail = () => { underlyingQueries++; throw new Error('OS DNS must not run'); };
dns.lookup = dns.resolve = dns.resolve4 = dns.lookupService = fail;
promises.lookup = promises.resolve = promises.resolve4 = promises.lookupService = fail;
dns.Resolver.prototype.resolve4 = fail;
promises.Resolver.prototype.resolve4 = fail;
require(process.env.TEST_BLUEREEL_GUARD);
const callbackCheck = operation => new Promise((resolve, reject) => {
  operation(error => { try { assert.equal(error.code, 'ERR_BLUEREEL_LOCAL_ONLY'); resolve(); } catch (failure) { reject(failure); } });
});
(async () => {
  const resolver = new dns.Resolver(); resolver.setServers(['192.0.2.53']);
  const asyncResolver = new promises.Resolver(); asyncResolver.setServers(['192.0.2.53']);
  await callbackCheck(callback => dns.lookup('forbidden.example.invalid', callback));
  await callbackCheck(callback => dns.resolve4('forbidden.example.invalid', callback));
  await callbackCheck(callback => dns.resolve('forbidden.example.invalid', 'TXT', callback));
  await callbackCheck(callback => dns.lookupService('192.0.2.1', 443, callback));
  await callbackCheck(callback => resolver.resolve4('forbidden.example.invalid', callback));
  for (const operation of [
    () => promises.lookup('forbidden.example.invalid'),
    () => promises.resolve4('forbidden.example.invalid'),
    () => promises.resolve('forbidden.example.invalid', 'MX'),
    () => promises.lookupService('192.0.2.1', 443),
    () => asyncResolver.resolve4('forbidden.example.invalid'),
  ]) await assert.rejects(operation, {code: 'ERR_BLUEREEL_LOCAL_ONLY'});
  assert.equal(underlyingQueries, 0);
  console.log(JSON.stringify({underlying_dns_queries: underlyingQueries, blocked: 10}));
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
        preload=False,
    )
    assert result == {"underlying_dns_queries": 0, "blocked": 10}


def test_udp_dns_transport_send_and_connect_are_denied(node: str) -> None:
    result = run_node(
        node,
        r"""
const assert = require('node:assert/strict');
const dgram = require('node:dgram');
let transmitted = 0;
dgram.Socket.prototype.send = dgram.Socket.prototype.connect = function () {
  transmitted++; throw new Error('OS datagram call must not run');
};
require(process.env.TEST_BLUEREEL_GUARD);
const socket = dgram.createSocket('udp4');
assert.throws(() => socket.send(Buffer.from('synthetic DNS'), 53, '192.0.2.53'), {code: 'ERR_BLUEREEL_LOCAL_ONLY'});
assert.throws(() => socket.connect(53, '192.0.2.53'), {code: 'ERR_BLUEREEL_LOCAL_ONLY'});
socket.close();
assert.equal(transmitted, 0);
console.log(JSON.stringify({datagrams: transmitted, blocked: 2}));
""",
        preload=False,
    )
    assert result == {"datagrams": 0, "blocked": 2}


def test_stop_file_gracefully_closes_real_loopback_http_server(
    node: str, tmp_path: Path
) -> None:
    stop = tmp_path / "stop-web"
    result = run_node(
        node,
        r"""
const http = require('node:http');
const fs = require('node:fs');
const assert = require('node:assert/strict');
let served = false;
const server = http.createServer((_request, response) => { served = true; response.end('local'); });
server.on('close', () => console.log(JSON.stringify({served, graceful_close: true})));
server.listen(0, '127.0.0.1', () => {
  http.get({host: '127.0.0.1', port: server.address().port, agent: false}, response => {
    response.resume();
    response.on('end', () => { assert.equal(response.statusCode, 200); fs.writeFileSync(process.env.BLUEREEL_STOP_FILE, 'stop'); });
  }).on('error', error => { console.error(error); process.exitCode = 1; server.close(); });
});
""",
        stop_file=stop,
    )
    assert result == {"served": True, "graceful_close": True}
    assert stop.read_text() == "stop"
