"""TradingView authentication for real-time scanner data.

Ported from mngqun/stock-screener `actions.retrieve_tradingview_cookie` + `methods.tradingview_login`
(same MongoDB layout and behaviour):
  * DB 'Lynk', collection 'Accounts', query {"site": "tradingview", "public": True}
  * pick a random account; if it has no stored cookies, log in and save the cookies back
  * if a scan with those cookies still returns delayed data, the cookies are cleared in MongoDB
    (`invalidate`) so the next call logs in again.

Secrets (GitHub Actions): MONGO_URI (preferred) or MONGO. Optional fallback: TV_SESSIONID.
Cookies and passwords are never printed or written to disk.
"""
from __future__ import annotations

import os
import random
import traceback

import curl_cffi
from curl_cffi import requests

DB, COLL = "Lynk", "Accounts"
LOGIN_URL = "https://www.tradingview.com/accounts/signin/"
BROWSERS = ["chrome", "firefox", "safari"]


def _mongo_uri() -> str | None:
    return (os.environ.get("MONGO_URI") or os.environ.get("MONGO") or "").strip() or None


def _to_dict(cookies) -> dict | None:
    """Stored cookies may be a dict, a list of {name, value} or a curl_cffi Cookies object."""
    if not cookies:
        return None
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items() if v is not None}
    if isinstance(cookies, list):
        out = {}
        for c in cookies:
            if isinstance(c, dict) and "name" in c:
                out[c["name"]] = str(c.get("value", ""))
        return out or None
    try:
        return dict(cookies)
    except Exception:
        return None


def tradingview_login(username: str, password: str) -> dict | None:
    mp = curl_cffi.CurlMime.from_list([
        {"name": "username", "content_type": "text/plain", "data": username},
        {"name": "password", "content_type": "text/plain", "data": password},
        {"name": "remember", "content_type": "text/plain", "data": "true"},
    ])
    headers = {"accept": "*/*", "origin": "https://www.tradingview.com",
               "referer": LOGIN_URL, "x-requested-with": "XMLHttpRequest"}
    try:
        r = requests.post(LOGIN_URL, headers=headers, multipart=mp,
                          impersonate=random.choice(BROWSERS), timeout=30)
        body = r.json()
        if body.get("error") == "":
            print("[TV LOGIN] logged in successfully")
            return dict(r.cookies)
        print(f"[TV LOGIN] failed: {body.get('error')}")
        return None
    except Exception:
        print("[TV LOGIN] error:", traceback.format_exc().splitlines()[-1])
        return None
    finally:
        mp.close()


def retrieve_tradingview_cookie() -> dict:
    """Returns {"_id": account id or None, "cookies": dict or None, "source": str}."""
    uri = _mongo_uri()
    if uri:
        try:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=15000)
            try:
                coll = client[DB][COLL]
                accounts = list(coll.find({"site": "tradingview", "public": True}))
                if accounts:
                    acc = random.choice(accounts)
                    cookies = _to_dict(acc.get("cookies"))
                    if not cookies:
                        cookies = tradingview_login(acc.get("username"), acc.get("password"))
                        if cookies:
                            coll.update_one({"_id": acc["_id"]}, {"$set": {"cookies": cookies}})
                    if cookies:
                        return {"_id": acc["_id"], "cookies": cookies, "source": "mongo"}
                else:
                    print("[TV AUTH] no public tradingview accounts found in MongoDB")
            finally:
                client.close()
        except Exception:
            print("[TV AUTH] mongo error:", traceback.format_exc().splitlines()[-1])
    sid = os.environ.get("TV_SESSIONID", "").strip()
    if sid:
        return {"_id": None, "cookies": {"sessionid": sid}, "source": "TV_SESSIONID"}
    return {"_id": None, "cookies": None, "source": "none"}


def invalidate(account_id) -> None:
    """Clear stored cookies after a delayed result (same as stock-screener behaviour)."""
    uri = _mongo_uri()
    if not uri or account_id is None:
        return
    try:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=15000)
        try:
            client[DB][COLL].update_one({"_id": account_id}, {"$set": {"cookies": None}})
            print("[TV AUTH] cleared stale cookies for account")
        finally:
            client.close()
    except Exception:
        print("[TV AUTH] invalidate error:", traceback.format_exc().splitlines()[-1])
