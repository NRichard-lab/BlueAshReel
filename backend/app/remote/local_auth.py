"""One-use browser-bound PKCE callback and Portal-authenticated loopback status."""

from __future__ import annotations

import asyncio
import hashlib
import html
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.remote.connector import post_control
from app.remote.control import PORTAL_ORIGIN, queue_action, snapshot
from app.remote.protocol import canonical, encode, readable_fingerprint

router = APIRouter(prefix="/portal", include_in_schema=False)
COOKIE = "bluereel_portal_handoff"
STATUS_COOKIE = "bluereel_portal_status"


def local_host(request: Request) -> str:
    server = request.scope.get("server")
    if (
        request.url.hostname != "127.0.0.1"
        or request.url.scheme != "http"
        or request.url.port is None
        or not 1024 <= request.url.port <= 65535
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
        or (server is not None and server[1] != request.url.port)
    ):
        raise HTTPException(403, "Use the Agent loopback address")
    return f"http://127.0.0.1:{request.url.port}"


def state(request: Request) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    connector = getattr(request.app.state, "portal_connector", None)
    if connector is None:
        raise HTTPException(503, "The Windows Agent is not available")
    return connector, request.app.state.portal_pending, request.app.state.portal_status_sessions


@router.get("/start")
async def start(request: Request, purpose: str = "status", retry: bool = False) -> RedirectResponse:
    origin = local_host(request)
    callback_origin = getattr(request.app.state, "portal_callback_origin", origin)
    connector, pending, _sessions = state(request)
    if purpose not in {"pair", "status"}:
        raise HTTPException(422, "Invalid authorization purpose")
    if not connector.paired:
        purpose = "pair"
    elif purpose == "pair":
        raise HTTPException(409, "This Agent is paired. Revoke the existing pairing before changing accounts.")
    if (connector.identity / "revocation.json").exists():
        raise HTTPException(409, "Unpairing is still reaching the Portal. Reconnect to the network and try again.")
    if retry:
        previous = request.cookies.get(COOKIE, "")
        current = pending.get(previous)
        if current and current["purpose"] == purpose and current["callback"] == callback_origin + "/portal/callback":
            del pending[previous]  # Only this browser's pending request is replaced.
    for key, value in list(pending.items()):
        if value["expires_at"] < time.time():
            del pending[key]
    # A double-click, refresh or a reopened browser returns to the same pending
    # authorization. Never replace its cookie or strand a second approval.
    for key, value in pending.items():
        if value["purpose"] == purpose and value["callback"] == callback_origin + "/portal/callback" and (
            purpose == "pair" or request.cookies.get(COOKIE) == key
        ):
            parameters = {key: item for key, item in value.items() if key not in {"verifier", "expires_at"}}
            return authorization_redirect(parameters, max_age=max(1, int(value["expires_at"] - time.time())))
    if purpose == "pair" and connector.state in {"waiting_local_confirmation", "pairing"}:
        raise HTTPException(409, "Confirm the fingerprint on the Agent workstation to finish pairing.")
    if len(pending) >= 8:
        raise HTTPException(429, "Too many authorization requests")
    browser_state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    parameters = {
        "purpose": purpose,
        "state": browser_state,
        "nonce": nonce,
        "code_challenge": encode(hashlib.sha256(verifier.encode("ascii")).digest()),
        "callback": callback_origin + "/portal/callback",
    }
    if purpose == "pair":
        identity = connector.prepare_identity("Blue Ash Reel Agent")
        parameters.update(identity)
    else:
        parameters["agent_id"] = connector.credentials["agent_id"]
    pending[browser_state] = {**parameters, "verifier": verifier, "expires_at": time.time() + 300}
    if purpose == "pair":
        connector.state = "waiting_portal_approval"
        connector.pairing_expires_at = pending[browser_state]["expires_at"]
        connector.publish()
    return authorization_redirect(parameters)


def authorization_redirect(parameters: dict[str, str], *, max_age: int = 300) -> RedirectResponse:
    result = RedirectResponse(PORTAL_ORIGIN + "/agent/authorize#" + urlencode(parameters), status_code=303)
    result.set_cookie(COOKIE, parameters["state"], httponly=True, samesite="lax", max_age=max_age, path="/portal")
    result.headers["Cache-Control"] = "no-store"
    result.headers["Referrer-Policy"] = "no-referrer"
    return result


@router.get("/callback")
async def callback_page(request: Request) -> HTMLResponse:
    origin = local_host(request)
    if origin != getattr(request.app.state, "portal_callback_origin", origin):
        raise HTTPException(403, "Use the authorization callback opened by the Portal")
    # The short-lived code is delivered in a fragment, never an HTTP access log.
    csp_nonce = secrets.token_urlsafe(20)
    result = HTMLResponse(
        """<!doctype html><html><meta charset="utf-8"><title>Blue Ash Reel</title>
<body><p id="status">Completing Portal authorization. Confirm on the Agent workstation if requested.</p>
<p><a id="retry" href="/portal/start?purpose=pair&amp;retry=true" hidden>Retry Pairing</a></p>
<script nonce="""
        + '"'
        + csp_nonce
        + '"'
        + """>(async()=>{
const parameters=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'','/portal/callback');
try {const response=await fetch('/portal/callback',{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(parameters))});
const result=await response.json();if(!response.ok)throw new Error(result.detail||'Authorization was rejected.');
if(result.redirect)location.replace(result.redirect);else {document.getElementById('status').textContent=result.message;
document.getElementById('retry').hidden=false;}
}catch(error){document.getElementById('status').textContent=
(error.message||'Authorization expired or was rejected.')+' Retry pairing to request a fresh approval.';
document.getElementById('retry').hidden=false;}
})();</script></body></html>"""
    )
    result.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{csp_nonce}'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Cache-Control"] = "no-store"
    return result


@router.post("/callback")
async def callback(request: Request) -> JSONResponse:
    origin = local_host(request)
    if request.headers.get("origin") != origin or request.headers.get("content-type") != "application/json":
        raise HTTPException(403, "Invalid callback origin")
    connector, pending, sessions = state(request)
    body = await request.body()
    if len(body) > 2048:
        raise HTTPException(413, "Invalid callback")
    import json

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) not in (
            {"code", "state", "nonce"}, {"error", "state", "nonce"}
        ):
            raise ValueError()
        browser_state = payload["state"]
        cookie = request.cookies.get(COOKIE, "")
        if not isinstance(browser_state, str) or not cookie or not secrets.compare_digest(cookie, browser_state):
            raise ValueError()
        value = pending.pop(browser_state, None)  # Consume before any asynchronous work.
        if value is None:
            raise ValueError()
        if value["expires_at"] <= time.time():
            if value["purpose"] == "pair":
                connector.state = "pairing_expired"
                connector.publish()
            raise ValueError()
        if not isinstance(payload["nonce"], str) or not secrets.compare_digest(value["nonce"], payload["nonce"]):
            if value["purpose"] == "pair":
                connector.state = "pairing_failed"
                connector.publish()
            raise ValueError()
        if value["callback"] != origin + "/portal/callback":
            raise ValueError()
        if "error" in payload:
            if payload["error"] != "access_denied":
                raise ValueError()
        elif not isinstance(payload["code"], str) or not 16 <= len(payload["code"]) <= 256:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(403, "Expired or replayed callback") from None
    if "error" in payload:
        if value["purpose"] == "pair":
            connector.state = "pairing_cancelled"
            connector.publish()
        response = JSONResponse({"message": "Authorization cancelled. You can close this page."})
        response.delete_cookie(COOKIE, path="/portal")
        response.headers["Cache-Control"] = "no-store"
        return response
    if value["purpose"] == "pair":
        if connector.paired or connector.prepare_identity(value["name"])["fingerprint"] != value["fingerprint"]:
            raise HTTPException(403, "Agent identity changed")
        from app.native_consent import request_confirmation

        connector.state = "waiting_local_confirmation"
        connector.publish()
        approved = await request_confirmation(
            connector.media.config,
            "Pair Blue Ash Reel with the signed-in Portal account? Compare this Agent fingerprint with the browser:\n\n"
            + readable_fingerprint(value["fingerprint"])
            + "\n\nApprove only if the fingerprints match and you requested pairing.",
        )
        if not approved:
            connector.state = "pairing_cancelled"
            connector.publish()
            raise HTTPException(403, "Local confirmation was declined")
        if value["expires_at"] <= time.time():
            connector.state = "pairing_expired"
            connector.publish()
            raise HTTPException(403, "Pairing expired. Start again from the Agent tray.")
        queue_action(
            connector.control,
            "pair",
            {
                "code": payload["code"],
                "name": value["name"],
                "code_verifier": value["verifier"],
                "state": browser_state,
                "nonce": value["nonce"],
                "callback": value["callback"],
                "local_confirmed": True,
            },
        )
        deadline = time.monotonic() + 25
        while not connector.paired and time.monotonic() < deadline:
            if connector.state == "pairing_failed":
                raise HTTPException(403, "Pairing failed. Open the pairing flow again.")
            await asyncio.sleep(0.25)
        if not connector.paired:
            raise HTTPException(503, "Pairing could not be confirmed. Check the Agent tray status.")
        redirect = PORTAL_ORIGIN + "/portal/agents/" + connector.credentials["agent_id"]
    else:
        agent_id = connector.credentials["agent_id"]
        assert connector.private_key is not None
        signed = canonical(
            "blueashreel-local-exchange-v2", agent_id, payload["code"], browser_state, value["nonce"],
            value["verifier"], value["callback"]
        )
        result = await asyncio.to_thread(
            post_control,
            "/api/agent-authorizations/exchange",
            {
                "agent_id": agent_id,
                **payload,
                "code_verifier": value["verifier"],
                "callback": value["callback"],
                "signature": encode(connector.private_key.sign(signed)),
            },
        )
        if (
            result.get("authorized") is not True
            or result.get("agent_id") != agent_id
            or result.get("state") != browser_state
            or result.get("nonce") != value["nonce"]
            or result.get("callback") != value["callback"]
            or result.get("code_challenge") != value["code_challenge"]
            or result.get("purpose") != "status"
            or result.get("expires_at", 0) <= time.time()
        ):
            raise HTTPException(403, "Agent authorization rejected")
        with connector.media.factory() as db:
            connector.media.principal(db, result)
        for key, current in list(sessions.items()):
            if current["expires_at"] < time.time():
                del sessions[key]
        if len(sessions) >= 16:
            raise HTTPException(429, "Local status session limit")
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = result
        response = JSONResponse({"redirect": "/portal/status"})
        response.set_cookie(STATUS_COOKIE, token, httponly=True, samesite="strict", max_age=300, path="/portal")
        response.delete_cookie(COOKIE, path="/portal")
        return response
    response = JSONResponse({"redirect": redirect})
    response.delete_cookie(COOKIE, path="/portal")
    return response


@router.get("/status")
async def status_page(request: Request) -> Any:
    local_host(request)
    connector, _pending, sessions = state(request)
    if not connector.paired:
        sessions.clear()
        return RedirectResponse("/portal/start", status_code=303)
    token = request.cookies.get(STATUS_COOKIE, "")
    authorization = sessions.get(hashlib.sha256(token.encode()).hexdigest())
    if not authorization or authorization["expires_at"] <= time.time():
        return RedirectResponse("/portal/start", status_code=303)
    agent_id = connector.credentials["agent_id"]
    try:
        challenge = await asyncio.to_thread(post_control, f"/api/agents/{agent_id}/challenge", {"purpose": "access"})
        payload = {key: authorization[key] for key in ("authorization_id", "user_id", "session_id")}
        payload["nonce"] = challenge["nonce"]
        assert connector.private_key is not None
        proof = canonical(
            "blueashreel-local-session-validate-v1",
            agent_id,
            payload["authorization_id"],
            payload["user_id"],
            payload["session_id"],
            payload["nonce"],
        )
        fresh = await asyncio.to_thread(
            post_control,
            f"/api/agents/{agent_id}/local-session/validate",
            {**payload, "signature": encode(connector.private_key.sign(proof))},
        )
        if (
            fresh.get("authorized") is not True
            or fresh.get("user_id") != authorization["user_id"]
            or fresh.get("agent_id") != agent_id
            or fresh.get("session_id") != authorization["session_id"]
        ):
            raise ValueError("Local status authorization rejected")
        authorization = {**authorization, **fresh}
    except Exception:
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        return RedirectResponse("/portal/start", status_code=303)
    with connector.media.factory() as db:
        connector.media.principal(db, authorization)
    current = snapshot(connector.control)
    display = html.escape(str(current["state"]).replace("_", " "))
    fingerprint = html.escape(str(current.get("fingerprint") or "Unpaired"))
    result = HTMLResponse(
        f"<!doctype html><html><meta charset='utf-8'><title>Blue Ash Reel Agent</title>"
        f"<body><h1>Blue Ash Reel Agent</h1><p>Status: {display}</p><p>Fingerprint: {fingerprint}</p>"
        f"<p><a href='{PORTAL_ORIGIN}'>Open Blue Ash Reel</a></p></body></html>"
    )
    result.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    return result
