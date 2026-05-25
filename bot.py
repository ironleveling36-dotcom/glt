#!/usr/bin/env python3
"""
GTL Swiggy Telegram Bot  —  with Force-Subscribe + Admin Channel Management
=============================================================================

New features added on top of the original:
  • Admin can add/remove channels via /addchannel and /removechannel
  • Users must join ALL required channels before using /play
  • /verify checks membership and unlocks the bot for that user
  • /stats shows admin the total users, verified count, channel list
  • /users lists all users (admin only)
  • Full deploy-ready for Railway (reads env vars, no hard-coded tokens)
"""

from __future__ import annotations

import os
import re
import json
import random
import logging
import asyncio
from typing import Optional

import httpx

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError

import database as db

# ─────────────────────────────────────────────────────────────────────────────
# Configuration  (set these as env vars on Railway)
# ─────────────────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
FAST2SMS_KEY = os.getenv("FAST2SMS_API_KEY",   "")

# Comma-separated list of Telegram user IDs who are admins
# e.g. ADMIN_IDS=123456789,987654321
_raw_admins  = os.getenv("ADMIN_IDS", "")
ADMIN_IDS    = set(int(x.strip()) for x in _raw_admins.split(",") if x.strip().isdigit())

BASE_URL = "https://api.thegoodtimesleague.com/api/game"
LB_URL   = "https://api.thegoodtimesleague.com/game/leaderboard"

LOCATIONS = [
    (28.6139, 77.2090),
    (19.0760, 72.8777),
    (12.9716, 77.5946),
    (13.0827, 80.2707),
    (22.5726, 88.3639),
    (17.3850, 78.4867),
]

# Conversation states
ASK_NUMBER, ASK_OTP = range(2)
# Admin add-channel states
WAIT_CHANNEL_ID = 10

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Guards
# ─────────────────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def check_force_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if the user has joined all required channels.
    Returns True  → user is cleared, proceed.
    Returns False → user shown join prompt, stop current handler.
    """
    user = update.effective_user
    channels = db.get_all_channels()

    if not channels:
        # No channels configured — allow all users
        return True

    not_joined = []
    for ch in channels:
        try:
            member = await ctx.bot.get_chat_member(ch["channel_id"], user.id)
            if member.status in (
                ChatMember.LEFT,
                ChatMember.BANNED,
                "kicked",
                "restricted",
            ):
                not_joined.append(ch)
        except TelegramError as e:
            logger.warning("get_chat_member error for %s: %s", ch["channel_id"], e)
            not_joined.append(ch)  # Assume not joined on error

    if not not_joined:
        db.set_user_verified(user.id, True)
        return True

    # Build join buttons
    buttons = []
    for ch in not_joined:
        label = ch["channel_name"] or ch["channel_id"]
        link  = ch["invite_link"] or f"https://t.me/{ch['channel_id'].lstrip('@')}"
        buttons.append([InlineKeyboardButton(f"📢 Join {label}", url=link)])

    buttons.append([InlineKeyboardButton("✅ I Joined — Verify Me", callback_data="verify_membership")])
    markup = InlineKeyboardMarkup(buttons)

    msg = (
        "⚠️ *Bot use karne ke liye pehle ye channels join karo:*\n\n"
        + "\n".join(f"• {ch['channel_name']}" for ch in not_joined)
        + "\n\nSab join karne ke baad *'✅ I Joined — Verify Me'* button dabao."
    )
    await update.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# GTL API Client  (async via httpx — fixes event-loop blocking)
# ─────────────────────────────────────────────────────────────────────────────

class GTLClient:
    def __init__(self, mobile: str):
        self.mobile  = mobile
        self.token: Optional[str] = None
        self._headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
            "Origin":       "https://swiggy.thegoodtimesleague.com",
            "Referer":      "https://swiggy.thegoodtimesleague.com/",
        }

    def _auth_headers(self) -> dict:
        h = dict(self._headers)
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def login(self) -> dict:
        payload = {
            "mobile": self.mobile,
            "utm_source": "Direct", "utm_medium": "Direct",
            "utm_campaign": "Direct", "browser": "Chrome", "os": "Android",
        }
        async with httpx.AsyncClient(headers=self._headers, timeout=15) as client:
            r    = await client.post(f"{BASE_URL}/login", json=payload)
            data = r.json()
        otp_token = (
            data.get("token")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
        )
        if otp_token:
            self.token = otp_token
        return data

    async def verify_otp(self, otp: str) -> dict:
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=15) as client:
            r    = await client.post(f"{BASE_URL}/otp/verify", json={"otp": otp})
            data = r.json()
        self.token = (
            data.get("token") or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("user") or {}).get("token")
        )
        return data

    async def signup(self) -> dict:
        names  = ["Rahul Sharma", "Priya Singh", "Amit Verma", "Neha Gupta",
                  "Vijay Kumar", "Sneha Patel", "Ravi Joshi", "Pooja Mehta"]
        states = ["Delhi", "Maharashtra", "Karnataka", "Tamil Nadu",
                  "West Bengal", "Telangana", "Uttar Pradesh", "Gujarat"]
        payload = {
            "name": random.choice(names), "mobile": self.mobile,
            "state": random.choice(states), "age": random.randint(18, 30),
            "age_consent": True, "receive_consent": True, "tnc_consent": True,
        }
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=15) as client:
            r    = await client.post(f"{BASE_URL}/signup", json=payload)
            data = r.json()
        new_token = data.get("token") or (data.get("data") or {}).get("token")
        if new_token:
            self.token = new_token
        return data

    async def ping(self) -> dict:
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=15) as client:
            r = await client.post(f"{BASE_URL}/ping", json={})
            return r.json()

    async def game_data(self, lat: float = 28.6139, lng: float = 77.2090) -> dict:
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=15) as client:
            r = await client.post(f"{BASE_URL}/data", json={"lat": lat, "lng": lng})
            return r.json()

    async def game_data_spoof(self) -> Optional[dict]:
        best = None
        for lat, lng in LOCATIONS:
            try:
                data = await self.game_data(lat, lng)
                if data.get("isInStore") and data.get("store"):
                    store = data["store"]
                    try:
                        elat = float(store.get("lat", lat))
                        elng = float(store.get("lng", lng))
                        ed   = await self.game_data(elat, elng)
                        if ed.get("isInStore"):
                            ed["_store_lat"] = elat; ed["_store_lng"] = elng
                            return ed
                    except Exception:
                        pass
                    data["_store_lat"] = lat; data["_store_lng"] = lng
                    return data
                if best is None:
                    best = data
            except Exception:
                pass
        return best or await self.game_data()

    async def submit_score(self, game_data: Optional[dict] = None) -> dict:
        headers   = self._auth_headers()
        store_lat = game_data.get("_store_lat") if game_data else None
        store_lng = game_data.get("_store_lng") if game_data else None
        store_id  = (game_data.get("store") or {}).get("id") if game_data else None

        async def _post(payload: dict) -> tuple:
            if store_lat and store_lng:
                payload["lat"] = store_lat; payload["lng"] = store_lng
            if store_id:
                payload["store_id"] = store_id
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                r = await client.post(f"{BASE_URL}/score", json=payload)
                return r, r.json()

        try:
            r, resp = await _post({"score": 90, "source": "swiggy"})
            if r.status_code == 200 and not resp.get("error"):
                return resp
        except Exception:
            pass

        scores = [
            {"object_id": 1, "score": 10},
            {"object_id": 2, "score": 30},
            {"object_id": 3, "score": 50},
        ]
        for _ in range(5):
            if not scores:
                break
            r, resp = await _post({"scores": scores})
            if r.status_code == 200 and not resp.get("error"):
                return resp
            error = resp.get("error", "")
            if error == "invalid_object_score":
                details = resp.get("details") or []
                if details:
                    corrected = {d["object_id"]: d["expected_score"] for d in details}
                    scores = [{"object_id": s["object_id"],
                               "score": corrected.get(s["object_id"], s["score"])} for s in scores]
                    continue
            if error == "object_not_playable_for_store":
                bad = resp.get("object_ids") or []
                if bad:
                    scores = [s for s in scores if s["object_id"] not in bad]
                    continue
            return resp

        _, resp = await _post({"scores": [{"object_id": 1, "score": 10}, {"object_id": 2, "score": 30}]})
        return resp

    async def leaderboard(self) -> dict:
        # FIX: added auth headers (were missing in original)
        async with httpx.AsyncClient(headers=self._auth_headers(), timeout=15) as client:
            r = await client.post(LB_URL, json={"data_type": "match_tickets"})
            return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_coupon(resp: dict) -> Optional[str]:
    raw = json.dumps(resp)
    for key in ("coupon", "coupon_code", "code", "voucher", "voucherCode",
                "promo", "promo_code", "discount_code", "offer_code"):
        val = resp.get(key) or (resp.get("data") or {}).get(key)
        if val and isinstance(val, str) and len(val) >= 4:
            return val.strip()
    m = re.search(r'\b([A-Z][A-Z0-9]{5,19})\b', raw)
    return m.group(1) if m else None


async def send_sms_coupon(mobile: str, coupon: str) -> bool:
    # FIX: converted to async httpx to avoid blocking event loop
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.fast2sms.com/dev/bulkV2",
                params={
                    "authorization": FAST2SMS_KEY,
                    "message": f"GTL Swiggy Coupon Code: {coupon} - The Good Times League",
                    "language": "english", "route": "q", "numbers": mobile,
                },
            )
            return r.json().get("return", False)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.full_name)

    channels = db.get_all_channels()
    if channels:
        # Check membership first
        cleared = await check_force_subscribe(update, ctx)
        if not cleared:
            return

    await update.message.reply_text(
        "🎮 *GTL Swiggy Bot*\n\n"
        "The Good Times League ka coupon bot!\n\n"
        "📋 *Commands:*\n"
        "/play — Game khelo aur coupon pao\n"
        "/leaderboard — Top players dekho\n"
        "/verify — Channel membership verify karo\n"
        "/cancel — Rok do\n",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Verify button callback
# ─────────────────────────────────────────────────────────────────────────────

async def callback_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user     = update.effective_user
    channels = db.get_all_channels()

    if not channels:
        db.set_user_verified(user.id, True)
        await query.edit_message_text("✅ Verified! Ab /play karo.")
        return

    not_joined = []
    for ch in channels:
        try:
            member = await ctx.bot.get_chat_member(ch["channel_id"], user.id)
            if member.status in (ChatMember.LEFT, ChatMember.BANNED, "kicked", "restricted"):
                not_joined.append(ch)
        except TelegramError:
            not_joined.append(ch)

    if not not_joined:
        db.set_user_verified(user.id, True)
        await query.edit_message_text(
            "✅ *Sabhi channels join kar liye!*\n\nAb /play karo aur coupon pao 🎉",
            parse_mode="Markdown",
        )
    else:
        names = ", ".join(ch["channel_name"] for ch in not_joined)
        await query.edit_message_text(
            f"❌ *Abhi bhi ye channels join nahi kiye:*\n{names}\n\nPehle join karo, phir verify karo.",
            parse_mode="Markdown",
        )


async def cmd_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual /verify command — re-check membership."""
    user     = update.effective_user
    channels = db.get_all_channels()

    if not channels:
        await update.message.reply_text("✅ Koi required channel nahi hai. /play karo!")
        return

    not_joined = []
    for ch in channels:
        try:
            member = await ctx.bot.get_chat_member(ch["channel_id"], user.id)
            if member.status in (ChatMember.LEFT, ChatMember.BANNED, "kicked", "restricted"):
                not_joined.append(ch)
        except TelegramError:
            not_joined.append(ch)

    if not not_joined:
        db.set_user_verified(user.id, True)
        await update.message.reply_text("✅ *Verification successful!* Ab /play karo 🎮", parse_mode="Markdown")
    else:
        names = "\n".join(f"• {ch['channel_name']}" for ch in not_joined)
        await update.message.reply_text(
            f"❌ *Ye channels abhi join nahi kiye:*\n{names}\n\nJoin karo phir /verify karo.",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────────────────────────────────────
# /play conversation
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_play(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.full_name)

    cleared = await check_force_subscribe(update, ctx)
    if not cleared:
        return ConversationHandler.END

    ctx.user_data.clear()
    await update.message.reply_text(
        "📱 Apna *mobile number* enter karo (10 digits):",
        parse_mode="Markdown",
    )
    return ASK_NUMBER


async def get_number(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mobile = update.message.text.strip().replace(" ", "").replace("-", "")
    if not re.fullmatch(r"[6-9]\d{9}", mobile):
        await update.message.reply_text(
            "❌ *Invalid number!*\n10 digit number chahiye (6-9 se shuru).\nDobara enter karo:",
            parse_mode="Markdown",
        )
        return ASK_NUMBER

    client = GTLClient(mobile)
    ctx.user_data["mobile"] = mobile
    ctx.user_data["client"] = client

    await update.message.reply_text(f"⏳ {mobile} pe OTP bhej raha hoon...")

    try:
        resp = await client.login()
    except Exception as e:
        await update.message.reply_text(f"❌ Server error: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END

    msg     = resp.get("message") or resp.get("msg") or str(resp)
    success = resp.get("success") or resp.get("status") in ("success", "ok") or resp.get("otp_sent")

    if success:
        await update.message.reply_text(
            f"✅ OTP bhej diya *{mobile}* pe!\n📩 GTL: _{msg}_\n\n🔑 OTP enter karo:",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"⚠️ Response: `{msg}`\n\nOTP aaya ho toh enter karo, warna /cancel karo:",
            parse_mode="Markdown",
        )
    return ASK_OTP


async def get_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    otp    = update.message.text.strip()
    mobile = ctx.user_data.get("mobile", "")
    client: GTLClient = ctx.user_data.get("client")

    if not client:
        await update.message.reply_text("❌ Session expired. /play se dobara shuru karo.")
        return ConversationHandler.END

    if not re.fullmatch(r"\d{4,6}", otp):
        await update.message.reply_text("❌ OTP 4-6 digits ka hota hai. Dobara enter karo:")
        return ASK_OTP

    await update.message.reply_text("🔄 OTP verify ho raha hai...")

    try:
        verify_resp = await client.verify_otp(otp)
    except Exception as e:
        await update.message.reply_text(f"❌ OTP verify error: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END

    raw    = json.dumps(verify_resp).lower()
    is_new = (verify_resp.get("new_user") or "new" in raw or "signup" in raw or "register" in raw)

    # FIX: always signup when server signals new user, regardless of token state
    if is_new:
        await update.message.reply_text("📝 Naya account bana raha hoon...")
        try:
            await client.signup()
        except Exception as e:
            await update.message.reply_text(f"❌ Signup error: `{e}`", parse_mode="Markdown")
            return ConversationHandler.END

    if not client.token:
        msg = verify_resp.get("message") or verify_resp.get("msg") or str(verify_resp)[:200]
        await update.message.reply_text(
            f"❌ Login fail.\nGTL Response: `{msg}`\n\n/play se dobara try karo.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text("✅ Login ho gaya!")

    try:
        await client.ping()
    except Exception:
        pass

    await update.message.reply_text("🗺️ Game data fetch ho raha hai...")
    game_resp = None
    try:
        game_resp = await client.game_data_spoof()
    except Exception:
        pass

    await update.message.reply_text("🎯 Score submit ho raha hai...")
    try:
        score_resp = await client.submit_score(game_data=game_resp)
    except Exception as e:
        await update.message.reply_text(f"❌ Score submit error: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END

    added  = score_resp.get("added_score", 0)
    total  = score_resp.get("total_score", 0)
    plays  = score_resp.get("total_plays", 0)
    ok     = score_resp.get("ok", False)
    reason = score_resp.get("coupon_distribution_reason", "")

    # FIX: mutually exclusive success/error branches — no double-reply
    if ok or added:
        await update.message.reply_text(
            f"✅ *Score Submit Hua!*\n\n➕ Is baar: *{added}* points\n📊 Total: *{total}* points\n🎮 Total plays: *{plays}*",
            parse_mode="Markdown",
        )
        coupon = extract_coupon(score_resp)
        if coupon:
            await update.message.reply_text(
                f"🎉 *Coupon Mila!*\n\n🏷️ Code: `{coupon}`\n\nSwiggy pe apply karo 🛵",
                parse_mode="Markdown",
            )
            sms_ok = await send_sms_coupon(mobile, coupon)
            if sms_ok:
                await update.message.reply_text(f"📱 SMS bhi bhej diya *{mobile}* pe!", parse_mode="Markdown")
    elif reason == "already_rewarded":
        await update.message.reply_text(
            "ℹ️ *Is account ko coupon pehle se mil chuka hai.*\n\nNaye number se try karo.",
            parse_mode="Markdown",
        )
    elif score_resp.get("reward_awarded") is False:
        await update.message.reply_text(
            f"⚠️ Score submit hua lekin coupon nahi mila.\nReason: `{reason or 'N/A'}`",
            parse_mode="Markdown",
        )
    elif score_resp.get("error"):
        await update.message.reply_text(
            f"❌ Error:\n```\n{json.dumps(score_resp, indent=2)[:500]}\n```",
            parse_mode="Markdown",
        )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Ruk gaya. /play se phir shuru karo.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /leaderboard
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cleared = await check_force_subscribe(update, ctx)
    if not cleared:
        return

    await update.message.reply_text("📊 Leaderboard load ho raha hai...")
    try:
        client  = GTLClient("0000000000")
        data    = await client.leaderboard()
        players = data.get("data") or data.get("leaderboard") or data.get("users") or []
        if players and isinstance(players, list):
            lines = ["🏆 *Top Players:*\n"]
            for i, p in enumerate(players[:10], 1):
                name  = p.get("name") or p.get("username") or "Unknown"
                score = p.get("score") or p.get("points") or p.get("total") or "-"
                lines.append(f"{i}. {name} — {score}")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        else:
            await update.message.reply_text(f"Data:\n`{str(data)[:400]}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /addchannel  (conversation)
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_addchannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 *Channel add karo*\n\n"
        "Bot ko us channel ka *admin* banana padega.\n\n"
        "Channel ka username ya ID bhejo:\n"
        "Examples:\n"
        "• `@mychannel`\n"
        "• `-1001234567890`",
        parse_mode="Markdown",
    )
    return WAIT_CHANNEL_ID


async def receive_channel_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()

    # Normalize: if user sent a t.me link, extract username
    if "t.me/" in raw:
        raw = "@" + raw.split("t.me/")[-1].strip("/")

    # Validate format
    if not (raw.startswith("@") or raw.lstrip("-").isdigit()):
        await update.message.reply_text(
            "❌ Invalid format.\n`@username` ya `-100xxxxxxxxxx` format mein bhejo.",
            parse_mode="Markdown",
        )
        return WAIT_CHANNEL_ID

    try:
        chat = await ctx.bot.get_chat(raw)
    except TelegramError as e:
        await update.message.reply_text(
            f"❌ Channel nahi mila: `{e}`\n\n"
            "Make sure bot us channel ka admin hai.",
            parse_mode="Markdown",
        )
        return WAIT_CHANNEL_ID

    channel_id   = str(chat.id)
    channel_name = chat.title or raw
    invite_link  = None

    # Try to get/create invite link
    try:
        if chat.invite_link:
            invite_link = chat.invite_link
        else:
            lnk = await ctx.bot.create_chat_invite_link(chat.id)
            invite_link = lnk.invite_link
    except TelegramError:
        invite_link = f"https://t.me/{chat.username}" if chat.username else None

    db.add_channel(channel_id, channel_name, invite_link, update.effective_user.id)

    await update.message.reply_text(
        f"✅ *Channel added!*\n\n"
        f"📢 Name: *{channel_name}*\n"
        f"🆔 ID: `{channel_id}`\n"
        f"🔗 Link: {invite_link or 'N/A'}\n\n"
        f"Ab users ko ye channel join karna hoga before using /play.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel_addchannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Channel add cancel kar diya.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /removechannel
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_removechannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return

    channels = db.get_all_channels()
    if not channels:
        await update.message.reply_text("ℹ️ Koi channel add nahi hai abhi.")
        return

    # Show inline buttons to pick which channel to remove
    buttons = []
    for ch in channels:
        buttons.append([
            InlineKeyboardButton(
                f"🗑 {ch['channel_name']} ({ch['channel_id']})",
                callback_data=f"rmch_{ch['channel_id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="rmch_cancel")])

    await update.message.reply_text(
        "🗑 *Kaun sa channel remove karna hai?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def callback_removechannel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Not authorized.")
        return

    data = query.data
    if data == "rmch_cancel":
        await query.edit_message_text("❌ Cancel kar diya.")
        return

    # FIX: prefix-only strip — prevents corruption if ID contains "rmch_"
    channel_id = data[len("rmch_"):]
    ch         = db.get_channel(channel_id)
    db.remove_channel(channel_id)
    name = ch["channel_name"] if ch else channel_id
    await query.edit_message_text(
        f"✅ *{name}* channel remove kar diya.\nAb users ko ye join nahi karna padega.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /listchannels
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_listchannels(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return

    channels = db.get_all_channels()
    if not channels:
        await update.message.reply_text("ℹ️ Koi channel add nahi hai abhi.")
        return

    lines = ["📋 *Required Channels:*\n"]
    for i, ch in enumerate(channels, 1):
        link = ch["invite_link"] or "N/A"
        lines.append(f"{i}. *{ch['channel_name']}*\n   ID: `{ch['channel_id']}`\n   Link: {link}")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /stats
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return

    total, verified, ch_count = db.get_stats()
    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"✅ Verified Users: *{verified}*\n"
        f"📢 Required Channels: *{ch_count}*",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /users
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("ℹ️ Koi user abhi tak nahi hai.")
        return

    # Show last 20 users
    lines = [f"👥 *Last 20 Users* (total: {len(users)}):\n"]
    for u in users[:20]:
        v    = "✅" if u["is_verified"] else "❌"
        name = u["full_name"] or u["username"] or "Unknown"
        un   = f"@{u['username']}" if u["username"] else "no username"
        lines.append(f"{v} {name} ({un}) — ID: `{u['user_id']}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN: /broadcast
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admins ke liye hai.")
        return

    # Usage: /broadcast <message>
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text(
            "Usage: `/broadcast Aapka message yahan`\n\nSabhi registered users ko message jayega.",
            parse_mode="Markdown",
        )
        return

    users      = db.get_all_users()
    sent_count = 0
    fail_count = 0

    await update.message.reply_text(f"📣 Broadcasting to {len(users)} users...")

    for u in users:
        try:
            await ctx.bot.send_message(u["user_id"], text)
            sent_count += 1
            await asyncio.sleep(0.05)  # Gentle rate limit
        except TelegramError:
            fail_count += 1

    await update.message.reply_text(
        f"✅ Broadcast complete!\n\n"
        f"📨 Sent: *{sent_count}*\n"
        f"❌ Failed: *{fail_count}*",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Admin: add-channel conversation ──────────────────────────────────────
    add_ch_conv = ConversationHandler(
        entry_points=[CommandHandler("addchannel", cmd_addchannel)],
        states={
            WAIT_CHANNEL_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_id)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_addchannel)],
        per_user=True,
        per_chat=True,
    )

    # ── Play conversation ─────────────────────────────────────────────────────
    play_conv = ConversationHandler(
        entry_points=[CommandHandler("play", cmd_play)],
        states={
            ASK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_number)],
            ASK_OTP:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        per_chat=True,
    )

    # ── Register handlers ─────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",         cmd_start))
    app.add_handler(CommandHandler("leaderboard",   cmd_leaderboard))
    app.add_handler(CommandHandler("verify",        cmd_verify))
    app.add_handler(CommandHandler("removechannel", cmd_removechannel))
    app.add_handler(CommandHandler("listchannels",  cmd_listchannels))
    app.add_handler(CommandHandler("stats",         cmd_stats))
    app.add_handler(CommandHandler("users",         cmd_users))
    app.add_handler(CommandHandler("broadcast",     cmd_broadcast))
    app.add_handler(add_ch_conv)
    app.add_handler(play_conv)

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(callback_verify,        pattern="^verify_membership$"))
    app.add_handler(CallbackQueryHandler(callback_removechannel, pattern="^rmch_"))

    logger.info("GTL Bot chalu ho gaya (polling)... Admins: %s", ADMIN_IDS)
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
