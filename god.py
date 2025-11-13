# ────────────────────────────────────────────────────────────────────────────────
# Copyright (c) 2025 Daniel
# All rights reserved.
# This software may not be copied, modified, or distributed without permission.
# ────────────────────────────────────────────────────────────────────────────────

import glob
import os
import random
import logging
import discord
from datetime import datetime
from discord.ext import commands, tasks
import asyncio
from collections import defaultdict, deque
import re
import json
from discord.utils import find

DATA_FILE = "session_data.json"

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(GUILD_DATA, f, ensure_ascii=False, indent=2)

def load_data():
    global GUILD_DATA
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            GUILD_DATA = json.load(f)


logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

MAX_PARTICIPANTS = 9
NEXT_ROUND_MAX = None
LABEL = {"3️⃣":3, "2️⃣":2, "1️⃣":1, "❤️":float('inf')}
TIERS = ["레","불","초","다","플","골","실","브","아"]
EMOJI_CLOSE       = "🛑"
EMOJI_OPEN        = "▶️"
EMOJI_RANDOM_MAP  = "🎲"
EMOJI_DELETE      = "🗑️"
EMOJI_ROTATE      = "🎮"
MAP_LIST = ["바인드","헤이븐","스플릿","어센트","아이스박스","펄","프랙처","로터스","어비스","선셋","무무가 원하는 맵","코로드"]
BACKUP_DIR = "backups"
auto_backup_enabled = {}

CUSTOM_EMOJI = "<:24:1386641516155375687>"

user_nicknames = {}
pending_warnings = {}

def save_user_nicknames():
    with open("valo_nicknames.json","w",encoding="utf-8") as f:
        json.dump(user_nicknames, f, ensure_ascii=False, indent=2)
    view = {}
    for uid_str, nick in user_nicknames.items():
        try: uid = int(uid_str)
        except: continue
        for g in bot.guilds:
            m = g.get_member(uid)
            if m:
                view[m.display_name] = nick
                break
        else:
            view[f"알수없음(ID:{uid})"] = nick
    with open("valo_nicknames_view.json","w",encoding="utf-8") as vf:
        json.dump(view, vf, ensure_ascii=False, indent=2)

def load_user_nicknames():
    global user_nicknames
    try:
        with open("valo_nicknames.json","r",encoding="utf-8") as f:
            user_nicknames = json.load(f)
    except FileNotFoundError:
        user_nicknames = {}

load_user_nicknames()

def get_current_limit(data):
    if data.get("locked_participants") is not None:
        return data["locked_participants"]
    if NEXT_ROUND_MAX is not None:
        return NEXT_ROUND_MAX
    return data.get("max_participants") or MAX_PARTICIPANTS

TIER_WEIGHT = {tier:wt for tier,wt in zip(TIERS, range(len(TIERS),0,-1))}
guild_locks = defaultdict(asyncio.Lock)
reaction_queues = defaultdict(deque)
GUILD_DATA = {}

MEMBER_CACHE = defaultdict(dict)
def get_member_fast(guild, uid):
    cache = MEMBER_CACHE[guild.id]
    if uid in cache:
        return cache[uid]
    m = guild.get_member(uid)
    if m:
        cache[uid] = m
    return m

def get_tier_cached(member):
    if member is None:
        return "티어 없음"
    for r in member.roles:
        if r.name in TIERS:
            return r.name
    return "티어 없음"

def build_participant_text_fast(data, guild):
    parts = data["participants"]
    waits = data["waitlist"]
    rl    = data["rounds_left"]
    lines = [f"{CUSTOM_EMOJI} 참가자 목록:"]
    if parts:
        for uid in parts:
            m = get_member_fast(guild, uid)
            tier = get_tier_cached(m)
            left = rl.get(uid, 0)
            suffix = " (고정)" if left==float('inf') else (f" ({left}판)" if left>1 else (" (1판)" if left==1 else ""))
            nick = user_nicknames.get(str(uid), "")
            fmt  = f" / `{nick}`" if nick else ""
            name = m.display_name if m else f"알수없음({uid})"
            lines.append(f"{name}{fmt} [{tier}]{suffix}")
    else:
        lines.append("(아직 없음)")
    if waits:
        lines.append("🔼 대기자:")
        for uid in waits:
            m = get_member_fast(guild, uid)
            tier = get_tier_cached(m)
            left = rl.get(uid, 0)
            suffix = " (고정)" if left==float('inf') else (f" ({left}판)" if left>1 else (" (1판)" if left==1 else ""))
            nick = user_nicknames.get(str(uid), "")
            fmt  = f" / `{nick}`" if nick else ""
            name = m.display_name if m else f"알수없음({uid})"
            lines.append(f"{name}{fmt} [{tier}]{suffix}")
    return "\n".join(lines)

async def update_status(gid_str, force=False):
    data = GUILD_DATA[gid_str]
    ch = bot.get_channel(data["viewer_channel_id"])
    if not ch:
        print(f"[DEBUG] update_status: 채널 없음 (gid={gid_str})")
        return

    try:
        # ✅ 항상 최신 메시지 다시 가져오기 (모드변경 이후 캐시된 객체 무효화 방지)
        msg = await ch.fetch_message(data["viewer_status_msg_id"])
        new_text = build_participant_text_fast(data, msg.guild)

        await msg.edit(content=new_text)
        save_data()
        print(f"[DEBUG] update_status 완료: {gid_str}")

    except discord.errors.NotFound:
        print(f"[DEBUG] update_status: 메시지 없음 (gid={gid_str}) → 무시")
    except Exception as e:
        print(f"[DEBUG] update_status 오류: {e}")

async def adjust_current_participants(gid_str, new_limit):
    data = GUILD_DATA[gid_str]
    parts, waits = data["participants"], data["waitlist"]
    while len(parts) > new_limit:
        u = parts.pop(); waits.insert(0, u)
    while len(parts) < new_limit and waits:
        parts.append(waits.pop(0))
    await update_status(gid_str)

def write_backup(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)

@tasks.loop(seconds=0.2)
async def process_reactions():
    for gid, q in list(reaction_queues.items()):
        if not q:
            continue

        # ✅ GUILD_DATA 자동 복구 (모드변경 직후 즉시 반응 큐 처리 시 None 방지)
        if gid not in GUILD_DATA:
            print(f"[DEBUG] process_reactions: {gid} 데이터 없음 → 스킵")
            continue
        if not GUILD_DATA[gid].get("viewer_msg_id"):
            print(f"[DEBUG] process_reactions: viewer_msg_id 없음 → 스킵")
            continue

        async with guild_locks[gid]:
            data = GUILD_DATA.get(gid)
            if not data:
                q.clear()
                continue

            guild = bot.get_guild(int(gid))
            status_changed = False

            while q:
                etype, payload = q.popleft()

                # 시청자 이모지 메시지에서만 작동
                if payload.message_id != data["viewer_msg_id"]:
                    continue

                member = get_member_fast(guild, payload.user_id)
                if not member:
                    continue

                emo = str(payload.emoji)

                # 일반 참가
                if etype == "add" and emo in ["3️⃣", "2️⃣", "1️⃣"]:
                    if not data.get("signup_open", False):
                        continue
                    uid = payload.user_id
                    parts, waits = data["participants"], data["waitlist"]
                    if uid in parts or uid in waits:
                        continue
                    limit = get_current_limit(data)
                    (parts if len(parts) < limit else waits).append(uid)
                    data.setdefault("rounds_left", {})[uid] = LABEL[emo]
                    status_changed = True
                    continue

                # 고정권 참가
                if etype == "add" and emo == "❤️":
                    if not data.get("signup_open", False):
                        continue
                    if "고정룰렛권" not in [r.name for r in member.roles]:
                        continue
                    uid = payload.user_id
                    parts, waits = data["participants"], data["waitlist"]
                    if uid not in parts and uid not in waits:
                        (parts if len(parts) < get_current_limit(data) else waits).append(uid)
                    data["rounds_left"][uid] = float("inf")
                    status_changed = True
                    continue

                # 대기자 삭제(🗑️)
                if etype == "add" and emo == EMOJI_DELETE:
                    uid = payload.user_id
                    if uid in data["waitlist"]:
                        data["waitlist"].remove(uid)
                        data["rounds_left"].pop(uid, None)
                        for msg_id in [data["viewer_msg_id"], data["viewer_status_msg_id"]]:
                            try:
                                tgt = await bot.get_channel(data["viewer_channel_id"]).fetch_message(msg_id)
                                mem = get_member_fast(guild, uid)
                                for e in [*LABEL.keys(), EMOJI_DELETE]:
                                    await tgt.remove_reaction(e, mem)
                            except:
                                pass
                        status_changed = True
                    continue

            if status_changed:
                await update_status(gid)


async def background_add_reaction(gid):
    data = GUILD_DATA.get(gid)
    if not data: return
    try:
        ch = bot.get_channel(data["admin_channel_id"])
        msg = await ch.fetch_message(data["admin_msg_id"])
        await msg.add_reaction(EMOJI_ROTATE)
    except:
        pass

@tasks.loop(seconds=10)
async def auto_update_status():
    for gid, data in GUILD_DATA.items():
        try:
            await update_status(gid)
        except:
            logging.exception("자동 새로고침 실패")

async def append_backup(guild_id_str, data):
    path = os.path.join(BACKUP_DIR, f"backup_{guild_id_str}.txt")
    text = build_participant_text_fast(data, bot.get_guild(int(guild_id_str)))
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n====== {datetime.now():%Y-%m-%d %H:%M:%S} ======\n{text}\n")

@tasks.loop(seconds=10)  # 60초마다 백업, 180으로 바꾸면 3분마다
async def periodic_backup():
    for gid, data in GUILD_DATA.items():
        path = os.path.join(BACKUP_DIR, f"backup_{gid}.txt")
        text = build_participant_text_fast(data, bot.get_guild(int(gid)))
        # 파일 입출력은 blocking이므로 to_thread로 분리 (안 해도 되지만 더 안전)
        await asyncio.to_thread(
            lambda: open(path, "a", encoding="utf-8").write(
                f"\n====== {datetime.now():%Y-%m-%d %H:%M:%S} ======\n{text}\n"
            )
        )

@bot.event
async def on_ready():
    logging.info(f"❤️ 봇 온라인: {bot.user}")

    # 🔧 중복 실행 방지 (재가동 시 task 중복 실행 문제 해결)
    if process_reactions.is_running():
        process_reactions.cancel()
    if auto_update_status.is_running():
        auto_update_status.cancel()
    if periodic_backup.is_running():
        periodic_backup.cancel()

    # ✅ 태스크 재시작
    process_reactions.start()
    auto_update_status.start()
    periodic_backup.start()

    # ✅ 데이터 복구
    load_data()
    print("데이터 복구 완료!")

    # ✅ 봇 상태 로그
    for gid, data in GUILD_DATA.items():
        ch = bot.get_channel(data.get("viewer_channel_id"))
        if ch:
            print(f"[복구됨] 서버ID {gid}, 채널: {ch.name}")
        else:
            print(f"[주의] 서버ID {gid}의 채널을 찾을 수 없음")

    print("🟢 봇이 완전히 온라인 상태입니다!")

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id==bot.user.id: return
    key = str(payload.guild_id)
    data = GUILD_DATA.get(key)
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    if not member: return
    # 시청자 메시지: 닉네임 등록 검사 및 참가 이모지
    if data and payload.message_id==data.get("viewer_msg_id") and str(payload.emoji) in LABEL:
        if not member.guild_permissions.administrator and str(payload.user_id) not in user_nicknames:
            await remove_reaction(payload, str(payload.emoji))
            ch = bot.get_channel(data["viewer_channel_id"])
            warn = await ch.send(f"{member.mention} ⚠️ 발로닉네임 등록해주세요!", delete_after=2)
            if payload.user_id in pending_warnings:
                cid, mid = pending_warnings[payload.user_id]
                try: await bot.get_channel(cid).fetch_message(mid).delete()
                except: pass
            pending_warnings[payload.user_id] = (ch.id, warn.id)
            return
    # === 관리자 메시지 ===
    if data and payload.message_id==data.get("admin_msg_id") and member.guild_permissions.administrator:
        # 시참시작
        if str(payload.emoji)==EMOJI_OPEN:
            await remove_reaction(payload, EMOJI_OPEN)
            data["signup_open"]=True
            ch=bot.get_channel(data["viewer_channel_id"])
            await ch.send("🟢 시참이 시작되었습니다!", delete_after=2)
            return
        # 시참마감
        if str(payload.emoji)==EMOJI_CLOSE:
            await remove_reaction(payload, EMOJI_CLOSE)
            data["signup_open"]=False
            ch=bot.get_channel(data["viewer_channel_id"])
            await ch.send("🔴 시참이 마감되었습니다!", delete_after=2)
            return
        # 로테이션
        if str(payload.emoji)==EMOJI_ROTATE:
            await remove_reaction(payload, EMOJI_ROTATE)
            # === 이전 상태 백업 ===
            data["prev_participants"] = data["participants"].copy()
            data["prev_waitlist"] = data["waitlist"].copy()
            data["prev_rounds_left"] = data["rounds_left"].copy()
            # 기존 로테이션 코드 유지
            old = data["participants"].copy(); new = []
            rl = data["rounds_left"]
            for uid in old:
                left = rl.get(uid, 0)
                if left==float('inf') or left>1:
                    if left>1: rl[uid] = left-1
                    new.append(uid)
            data["participants"] = new
            while len(new)<get_current_limit(data) and data["waitlist"]:
                new.append(data["waitlist"].pop(0))
            await update_status(key)
            return
        # 랜덤맵
        if str(payload.emoji)==EMOJI_RANDOM_MAP:
            await remove_reaction(payload, EMOJI_RANDOM_MAP)
            if data.get("last_map_msg_id"):
                try:
                    old=await bot.get_channel(data["viewer_channel_id"]).fetch_message(data["last_map_msg_id"])
                    await old.delete()
                except: pass
            chosen=random.choice(MAP_LIST)
            msg=await bot.get_channel(data["viewer_channel_id"]).send(f"🎲 이번 내전 맵은 **{chosen}**!")
            data["last_map_msg_id"]=msg.id
            return
    # === 시청자 메시지 이모지(참가/대기자 관련) ===
    # ✅ 모드변경 직후 새 메시지 ID 싱크 오류 보정 + 디버그 로그 추가
    if data and str(payload.emoji) in (*LABEL.keys(), EMOJI_DELETE):
        viewer_id = data.get("viewer_msg_id")

        print(f"[DEBUG] REACT EVENT 감지: emoji={payload.emoji}, msg_id={payload.message_id}, viewer_id={viewer_id}")

        # 정상적인 경우
        if payload.message_id == viewer_id:
            print(f"[DEBUG] 정상 일치 → 큐 등록")
            reaction_queues[str(key)].append(("add", payload))
            return
        else:
            # 모드변경 직후 ID 불일치 시 같은 채널의 viewer 메시지면 허용
            ch = bot.get_channel(data["viewer_channel_id"])
            try:
                latest_msg = await ch.fetch_message(viewer_id)
                print(f"[DEBUG] 불일치 감지: payload={payload.message_id}, 최신 viewer={latest_msg.id}")
                if payload.message_id == latest_msg.id:
                    print(f"[DEBUG] 강제 싱크 → 큐 등록 완료")
                    reaction_queues[str(key)].append(("add", payload))
                    return
            except Exception as e:
                print(f"[DEBUG] 강제 싱크 실패: {e}")
                pass

@bot.event
async def on_raw_reaction_remove(payload):
    return

async def remove_reaction(payload, emoji):
    ch = bot.get_channel(payload.channel_id)
    if not ch: return
    try:
        msg = await ch.fetch_message(payload.message_id)
    except:
        return
    member = ch.guild.get_member(payload.user_id)
    if not member: return
    try:
        await msg.remove_reaction(emoji, member)
    except:
        pass

@bot.event
async def on_member_remove(member):
    key=str(member.guild.id)
    data=GUILD_DATA.get(key)
    if not data: return
    uid=member.id; parts, waits=data["participants"], data["waitlist"]
    removed=False
    if uid in parts:
        parts.remove(uid); data["rounds_left"].pop(uid,None); removed=True
    if uid in waits:
        waits.remove(uid); data["rounds_left"].pop(uid,None); removed=True
    if removed:
        while len(parts)<get_current_limit(data) and waits:
            parts.append(waits.pop(0))
        await update_status(key)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return await bot.process_commands(message)
    data=GUILD_DATA.get(str(message.guild.id))
    if data:
        c=message.content.strip()
        if "#" in c and len(c)>=3 and str(message.author.id) not in user_nicknames:
            try: await message.delete()
            except: pass
            user_nicknames[str(message.author.id)]=c
            save_user_nicknames()
            if message.author.id in pending_warnings:
                cid,mid=pending_warnings.pop(message.author.id)
                try: await bot.get_channel(cid).fetch_message(mid).delete()
                except: pass
            await message.channel.send(f"{message.author.mention} ✅ `{c}` 닉네임이 등록되었습니다! 이제 이모지를 눌러주세요.", delete_after=2)
            return
    m=re.match(r"^!(\d+)명$", message.content.strip())
    if m and message.author.guild_permissions.administrator:
        num=int(m.group(1)); gid=str(message.guild.id)
        await message.channel.send(f"✅ 이번 판 참가 최대 인원을 **{num}명**으로 설정하고, 명단을 재조정합니다!", delete_after=2)
        await adjust_current_participants(gid,num)
        return
    await bot.process_commands(message)

# ─── 채널 분리 명령어 ──────────────────────────────────────────────
@bot.command(name="등록")
@commands.has_permissions(administrator=True)
async def 등록(ctx, viewer_channel:discord.TextChannel=None):
    channel = viewer_channel or ctx.channel
    reg_msg = await ctx.send("1️⃣ 일반 2️⃣ 1티어구독 3️⃣ 2티어구독 ❤️고정권")
    for e in ["1️⃣","2️⃣","3️⃣","❤️",EMOJI_DELETE]:
        await reg_msg.add_reaction(e)
    status_msg = await channel.send(f"{CUSTOM_EMOJI} 참가자 목록:\n(아직 없음)")
    GUILD_DATA[str(ctx.guild.id)] = {
        **GUILD_DATA.get(str(ctx.guild.id), {}),
        "viewer_channel_id": channel.id,
        "viewer_msg_id": reg_msg.id,
        "viewer_status_msg_id": status_msg.id,
        "participants": [],
        "waitlist": [],
        "rounds_left": {},
        "max_participants": 9,
        "locked_participants": None,
        "signup_open": False,
        "last_map_msg_id": None,
        "party_code": None,
        "party_code_msg_id": None
    }

@bot.command(name="관리자")
@commands.has_permissions(administrator=True)
async def 관리자(ctx, admin_channel:discord.TextChannel=None):
    channel = admin_channel or ctx.channel
    admin_msg = await channel.send("🎮로테이션 ▶️시참시작 🛑시참정지 🎲랜덤맵")
    for e in [EMOJI_ROTATE, EMOJI_OPEN, EMOJI_CLOSE, EMOJI_RANDOM_MAP]:
        await admin_msg.add_reaction(e)
    GUILD_DATA[str(ctx.guild.id)] = {
        **GUILD_DATA.get(str(ctx.guild.id), {}),
        "admin_channel_id": channel.id,
        "admin_msg_id": admin_msg.id,
    }

# (아래 기존 관리자/유저 커맨드들은 동일, 단 명단 갱신은 viewer 채널 기준)

@bot.command()
@commands.has_permissions(administrator=True)
async def 명단(ctx):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.", delete_after=2)
    guild=ctx.guild
    await ctx.send(build_participant_text_fast(data, guild))

@bot.command()
@commands.has_permissions(administrator=True)
async def 백업(ctx):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.", delete_after=2)
    await update_status(str(ctx.guild.id))
    path=os.path.join(BACKUP_DIR,f"backup_{ctx.guild.id}.txt")
    with open(path,"a",encoding="utf-8") as f:
        f.write(f"\n====== {datetime.now():%Y-%m-%d %H:%M:%S} ======\n"+build_participant_text_fast(data,ctx.guild)+"\n")
    await ctx.send(file=discord.File(path))
    auto_backup_enabled[str(ctx.guild.id)]=True
    await ctx.send("✅ 백업이 누적 저장되었습니다.", delete_after=5)

@bot.command(name="전체삭제")
@commands.has_permissions(administrator=True)
async def 전체삭제(ctx):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.",delete_after=2)
    data["participants"].clear();data["waitlist"].clear();data["rounds_left"].clear()
    last=data.get("last_map_msg_id");
    if last:
        try: await bot.get_channel(data["viewer_channel_id"]).fetch_message(last).delete()
        except: pass
        data["last_map_msg_id"]=None
    ch=bot.get_channel(data["viewer_channel_id"])
    try:
        reg=await ch.fetch_message(data["viewer_msg_id"])
        for react in reg.reactions:
            async for user in react.users():
                if user.id!=bot.user.id:
                    try: await reg.remove_reaction(react.emoji,user)
                    except: pass
    except: pass
    await ctx.send("✅ 참가자/대기자 초기화 및 모든 유저 리액션 해제 완료!",delete_after=2)
    await update_status(str(ctx.guild.id))

@bot.command(name="올리기")
@commands.has_permissions(administrator=True)
async def 올리기(ctx,member:discord.Member):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.",delete_after=2)
    uid=member.id;parts,waits=data["participants"],data["waitlist"]
    if uid not in waits:
        return await ctx.send(f"⚠️ {member.display_name}님은 대기열에 없습니다.",delete_after=2)
    max_num=get_current_limit(data)
    if len(parts)<max_num:
        waits.remove(uid);parts.append(uid);data["rounds_left"].setdefault(uid,1)
        await ctx.send(f"✅ {member.display_name}님을 참가자로 올렸습니다!",delete_after=2)
        await update_status(str(ctx.guild.id));return
    last_uid=parts.pop();waits.insert(0,last_uid)
    waits.remove(uid);parts.append(uid);data["rounds_left"].setdefault(uid,1)
    removed_member=ctx.guild.get_member(last_uid)
    await ctx.send(f"🔄 참가자가 이미 {max_num}명이라, **{removed_member.display_name}**님을 대기열 맨 앞으로 이동시키고\n"+
                   f"✅ **{member.display_name}**님을 참가자로 올렸습니다!",delete_after=3)
    await update_status(str(ctx.guild.id))

@bot.command(name="닉네임삭제")
async def 닉네임삭제(ctx):
    if str(ctx.author.id) not in user_nicknames:
        return await ctx.send(f"{ctx.author.mention} ⚠️ 등록된 발로닉네임이 없습니다.",delete_after=2)
    user_nicknames.pop(str(ctx.author.id),None);save_user_nicknames()
    await ctx.send(f"{ctx.author.mention} ✅ 발로닉네임이 삭제되었습니다. 다시 닉네임#KR1 형식으로 등록해주세요.",delete_after=2)

@bot.command(name="종료합니당")
@commands.has_permissions(administrator=True)
async def 종료합니당(ctx):
    await ctx.send("👋 봇을 종료합니다…")
    await bot.close()

@bot.command(name="백업기록")
@commands.has_permissions(administrator=True)
async def 백업기록(ctx):
    path = os.path.join(BACKUP_DIR, "backup_1226441318046109766.txt")  # ← 파일명 고정!
    if not os.path.exists(path):
        return await ctx.send("❌ 지정된 백업 파일이 없습니다.", delete_after=2)
    await ctx.send(file=discord.File(path))
    await ctx.send("✅ 해당 백업 txt 기록입니다.", delete_after=6)

@bot.command(name="파티코드",aliases=["파티"])
async def 파티코드(ctx,*,code:str):
    gid=str(ctx.guild.id);data=GUILD_DATA.get(gid)
    if not data: return await ctx.send("❌ 먼저 !등록 또는 !일반시참을 실행하세요.",delete_after=2)
    if data.get("party_code_msg_id"):
        try: await bot.get_channel(data["viewer_channel_id"]).fetch_message(data["party_code_msg_id"]).delete()
        except: pass
    ch=bot.get_channel(data["viewer_channel_id"])
    party=await ch.send(f"# (파티코드: {code.strip()})")
    data["party_code"]=code.strip();data["party_code_msg_id"]=party.id
    await ctx.send("✅ 파티코드가 시작버튼 아래에 표시되었습니다!",delete_after=2)

@bot.command(name="고정")
@commands.has_permissions(administrator=True)
async def 고정(ctx,*,arg):
    m=re.match(r"(\d+)",arg)
    if not m: return await ctx.send("숫자를 정확히 입력하세요! 예: !고정7명",delete_after=2)
    n=int(m.group(1));gid=str(ctx.guild.id);data=GUILD_DATA.get(gid)
    if not data: return await ctx.send("❌ 먼저 !일반시참을 실행해주세요.",delete_after=2)
    data["locked_participants"]=n
    await ctx.send(f"🔒 참가 인원을 **{n}명**으로 고정합니다. 로테 돌려도 계속 {n}명입니다!",delete_after=2)
    await adjust_current_participants(gid,n)

@bot.command(name="내리기")
@commands.has_permissions(administrator=True)
async def 내리기(ctx,*members:discord.Member):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.",delete_after=2)
    parts,waits=data["participants"],data["waitlist"]
    moved=[]
    for m in members:
        uid=m.id
        if uid in parts:
            parts.remove(uid);waits.insert(0,uid);moved.append(m.display_name)
            while len(parts)<get_current_limit(data) and waits:
                parts.append(waits.pop(0))
    if moved:
        await ctx.send(f"✅ {' ,'.join(moved)}님을 대기열 맨 앞으로 이동!",delete_after=2)
        await update_status(str(ctx.guild.id))
    else:
        await ctx.send("⚠️ 참가자 명단에 해당 유저가 없습니다.",delete_after=2)

@bot.command(name="판수변경")
@commands.has_permissions(administrator=True)
async def 판수변경(ctx,nickname:str,num:int):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data: return await ctx.send("❌ 등록된 신청 메시지가 없습니다.",delete_after=2)
    member=find(lambda m:m.display_name==nickname or m.name==nickname,ctx.guild.members)
    if not member: return await ctx.send(f"⚠️ '{nickname}' 님을 찾을 수 없습니다.",delete_after=2)
    uid=member.id
    if uid not in data["participants"] and uid not in data["waitlist"]:
        return await ctx.send(f"⚠️ {member.display_name}님은 명단에 없습니다.",delete_after=2)
    data.setdefault("rounds_left",{})[uid]=num
    await ctx.send(f"✅ {member.display_name}님의 판수를 **{num}판**으로 설정했습니다.",delete_after=2)
    await update_status(str(ctx.guild.id))

@bot.command(name="일반시참")
@commands.has_permissions(administrator=True)
async def 일반시참(ctx, viewer_channel:discord.TextChannel=None):
    channel = viewer_channel or ctx.channel
    reg_msg = await ctx.send("1️⃣ 일반")
    for e in ["1️⃣",EMOJI_DELETE]:
        await reg_msg.add_reaction(e)
    status_msg = await channel.send(f"{CUSTOM_EMOJI} 참가자 목록:\n(아직 없음)")
    GUILD_DATA[str(ctx.guild.id)] = {
        **GUILD_DATA.get(str(ctx.guild.id), {}),
        "viewer_channel_id": channel.id,
        "viewer_msg_id": reg_msg.id,
        "viewer_status_msg_id": status_msg.id,
        "participants": [],
        "waitlist": [],
        "rounds_left": {},
        "max_participants": 4,
        "locked_participants": None,
        "signup_open": False,
        "last_map_msg_id": None,
        "party_code": None,
        "party_code_msg_id": None
    }

@bot.command(name="참가자삭제")
@commands.has_permissions(administrator=True)
async def 참가자삭제(ctx,member:discord.Member):
    data=GUILD_DATA.get(str(ctx.guild.id))
    if not data:
        return await ctx.send("❌ 등록된 신청 메시지가 없습니다.",delete_after=2)
    uid=member.id; parts,waits=data["participants"],data["waitlist"]
    if uid in parts:
        parts.remove(uid); data["rounds_left"].pop(uid,None)
        if waits: parts.append(waits.pop(0))
    elif uid in waits:
        waits.remove(uid); data["rounds_left"].pop(uid,None)
    else:
        return await ctx.send(f"⚠️ {member.display_name}님은 명단에 없습니다.",delete_after=2)
    await ctx.send("✅ 삭제 완료",delete_after=2)
    await update_status(str(ctx.guild.id))

@bot.command(name="닉네임수정")
@commands.has_permissions(administrator=True)
async def 닉네임수정(ctx, 디코닉: str, 발로닉네임: str):
    # 디스코드 멤버 찾기 (닉네임/이름 모두 지원)
    member = find(lambda m: m.display_name == 디코닉 or m.name == 디코닉, ctx.guild.members)
    if not member:
        return await ctx.send(f"⚠️ '{디코닉}' 님을 찾을 수 없습니다.", delete_after=3)
    user_nicknames[str(member.id)] = 발로닉네임
    save_user_nicknames()
    await ctx.send(f"✅ {member.display_name}님의 발로란트 닉네임을 `{발로닉네임}`(으)로 변경했습니다.", delete_after=3)

@bot.command(name="참가")
@commands.has_permissions(administrator=True)
async def 참가(ctx, 디코닉: str):
    # 1. 디코닉으로 멤버 찾기
    member = find(lambda m: m.display_name == 디코닉 or m.name == 디코닉, ctx.guild.members)
    if not member:
        return await ctx.send(f"⚠️ '{디코닉}' 님을 찾을 수 없습니다.", delete_after=3)
    uid = member.id

    # 2. 발로닉네임 없으면 출력 x
    valo_nick = user_nicknames.get(str(uid))
    if not valo_nick:
        return await ctx.send(f"❌ {member.display_name} 님은 발로란트 닉네임이 등록되어 있지 않습니다.", delete_after=3)

    # 3. 명단에 이미 있으면 중복 추가 방지
    data = GUILD_DATA.get(str(ctx.guild.id))
    if not data:
        return await ctx.send("❌ 등록된 신청 메시지가 없습니다.", delete_after=2)
    if uid in data["participants"]:
        return await ctx.send(f"⚠️ 이미 참가자 명단에 있습니다.", delete_after=3)
    data["participants"].append(uid)
    data["rounds_left"][uid] = 1

    # 4. 출력 (매번 새 메시지로!)
    await ctx.send(build_participant_text_fast(data, ctx.guild))

@bot.command(name="되돌리기")
@commands.has_permissions(administrator=True)
async def 되돌리기(ctx):
    data = GUILD_DATA.get(str(ctx.guild.id))
    if not data or "prev_participants" not in data:
        return await ctx.send("⛔ 되돌릴 기록이 없습니다.", delete_after=2)
    # === 복원 ===
    data["participants"] = data["prev_participants"]
    data["waitlist"] = data["prev_waitlist"]
    data["rounds_left"] = data["prev_rounds_left"]
    # 복원 후 한 번만 복구 되게 제거
    data.pop("prev_participants", None)
    data.pop("prev_waitlist", None)
    data.pop("prev_rounds_left", None)
    await update_status(str(ctx.guild.id))
    await ctx.send("✅ 직전 로테이션 상태로 되돌렸습니다!", delete_after=2)

@bot.command(name="대기열")
@commands.has_permissions(administrator=True)
async def 대기열(ctx, 디코닉: str, 위치: int = 1):
    data = GUILD_DATA.get(str(ctx.guild.id))
    if not data:
        return await ctx.send("❌ 등록된 신청 메시지가 없습니다.", delete_after=2)

    member = find(lambda m: m.display_name == 디코닉 or m.name == 디코닉, ctx.guild.members)
    if not member:
        return await ctx.send(f"⚠️ '{디코닉}' 님을 찾을 수 없습니다.", delete_after=2)

    uid = member.id
    parts = data["participants"]
    waits = data["waitlist"]
    max_num = get_current_limit(data)

    # 참가자인 경우 -> 대기열로 내리고 지정 위치로 이동
    if uid in parts:
        parts.remove(uid)
        # 위치 보정
        if 위치 < 1:
            위치 = 1
        if 위치 > len(waits) + 1:  # +1: 방금 빠졌으니 자리 생김
            위치 = len(waits) + 1
        # 지정 위치에 삽입
        waits.insert(위치 - 1, uid)

        # 참가자 부족하면 "본인 제외" 대기열 1번을 참가자로 올림
        msg = ""
        if len(parts) < max_num and waits:
            # 본인을 제외한 대기열 1번
            for idx, candidate_uid in enumerate(waits):
                if candidate_uid != uid:
                    올라갈_uid = waits.pop(idx)
                    parts.append(올라갈_uid)
                    msg += f"🔼 <@{올라갈_uid}>님을 참가자로 올리고, "
                    break
        msg += f"✅ {member.display_name}님을 대기열 {위치}번째로 이동시켰습니다."
        await ctx.send(msg, delete_after=3)
        await update_status(str(ctx.guild.id))
        return

    # 이미 대기열에 있는 경우 -> 위치만 이동
    if uid in waits:
        waits.remove(uid)
        if 위치 < 1:
            위치 = 1
        if 위치 > len(waits) + 1:
            위치 = len(waits) + 1
        waits.insert(위치 - 1, uid)
        await ctx.send(f"✅ {member.display_name}님을 대기열 {위치}번째로 이동시켰습니다.", delete_after=3)
        await update_status(str(ctx.guild.id))
        return

    # 참가자/대기자 둘 다 없으면 안내
    return await ctx.send(f"⚠️ {member.display_name}님은 참가자/대기열에 없습니다.", delete_after=2)

@bot.command(name="모드변경")
@commands.has_permissions(administrator=True)
async def 모드변경(ctx):
    gid = str(ctx.guild.id)
    data = GUILD_DATA.get(gid)
    if not data:
        return await ctx.send("❌ 등록된 시참 데이터가 없습니다. 먼저 !등록 또는 !일반시참을 실행해주세요.", delete_after=3)

    ch = bot.get_channel(data["viewer_channel_id"])
    if not ch:
        return await ctx.send("⚠️ 뷰어 채널을 찾을 수 없습니다.", delete_after=3)

    # 현재 모드 판별
    current_mode = "등록" if data.get("max_participants", 9) == 9 else "일반"

    # 기존 메시지 삭제
    for key in ["viewer_msg_id", "viewer_status_msg_id"]:
        try:
            msg = await ch.fetch_message(data[key])
            await msg.delete()
        except:
            pass

    # ✅ 기존 참가자 데이터 초기화
    participants, waitlist, rounds_left = [], [], {}
    locked = data.get("locked_participants")
    signup_open = True  # 시참 자동 오픈 유지

    # ──────────────────────────────────────
    # 새 모드 메시지 생성
    # ──────────────────────────────────────
    if current_mode == "등록":
        # → 일반시참으로 전환
        reg_msg = await ch.send("1️⃣ 일반")
        for e in ["1️⃣", EMOJI_DELETE]:
            await reg_msg.add_reaction(e)
        status_msg = await ch.send(f"{CUSTOM_EMOJI} 참가자 목록:\n(아직 없음)")
        max_part = 4
        msg_text = "🔄 **등록 모드 → 일반시참 모드**로 변경되었습니다."
    else:
        # → 등록(티어) 모드로 전환
        reg_msg = await ch.send("1️⃣ 일반 2️⃣ 1티어구독 4️⃣ 2티어구독 ❤️고정권")
        for e in ["1️⃣", "2️⃣", "4️⃣", "❤️", EMOJI_DELETE]:
            await reg_msg.add_reaction(e)
        status_msg = await ch.send(f"{CUSTOM_EMOJI} 참가자 목록:\n(아직 없음)")
        max_part = 9
        msg_text = "🔄 **일반시참 → 등록(티어) 모드**로 변경되었습니다."

    # GUILD_DATA 갱신
    GUILD_DATA[gid].update({
        "viewer_channel_id": ch.id,
        "viewer_msg_id": reg_msg.id,
        "viewer_status_msg_id": status_msg.id,
        "participants": participants,
        "waitlist": waitlist,
        "rounds_left": rounds_left,
        "max_participants": max_part,
        "locked_participants": locked,
        "signup_open": signup_open,
        "last_map_msg_id": None,
    })

    # 🔁 반응 큐 초기화
    reaction_queues[gid].clear()

    # 🔄 상태 메시지 업데이트 (PythonAnywhere에서 안정 대기)
    await asyncio.sleep(3)  # 이벤트 루프 안정 대기
    await update_status(gid, force=True)
    print(f"[모드변경: PythonAnywhere] 강제 상태 갱신 완료")

    # ✅ 새 viewer 메시지 이벤트 보장
    try:
        new_viewer_id = GUILD_DATA[gid]["viewer_msg_id"]
        msg = await ch.fetch_message(new_viewer_id)

        if GUILD_DATA[gid]["max_participants"] == 4:
            for e in ["1️⃣", EMOJI_DELETE]:
                await msg.add_reaction(e)
        else:
            for e in ["1️⃣", "2️⃣", "️3️⃣", "❤️", EMOJI_DELETE]:
                await msg.add_reaction(e)

        print(f"[모드변경] {gid} 새 메시지 이벤트 재연결 완료")

    except Exception as e:
        print(f"[모드변경] 새 메시지 반응 재연결 실패: {e}")

    print(f"[모드변경 완료] {gid} 새 viewer_msg_id={reg_msg.id}, status_msg_id={status_msg.id}")
    await ctx.send(msg_text + " (🟢 시참 자동 오픈, 기존 명단 초기화됨)", delete_after=5)



# ─── 봇 실행 ────────────────────────────────────────────────────────────────────
bot.run("MTM2NjM2NjA5MTY1NTUxNjI3MQ.GQeQhC.qMr4d10QF-ddkZsMoW6yXDoqWSleGJj4ibXS2s")
