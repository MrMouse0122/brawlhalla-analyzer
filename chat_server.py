#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat_server.py：本地聊天服务——把 Agent 对话搬进浏览器（ZCode 质感的正解）。

架构：
  http.server (ThreadingHTTPServer, 127.0.0.1)
    GET  /            聊天页（内嵌 HTML/CSS/JS，shadcn 深色风）
    GET  /api/state   历史轮次 + 记忆/新对局状态（JSON）
    GET  /api/events  SSE 事件流（delta / tool / done / reset）
    POST /api/send    {q} 触发一轮 agent（busy 时 409）
    POST /api/stop    中断当前生成
    POST /api/clear   清空对话（不删长期记忆）
  Agent / 会话 / 记忆：复用 agent.py + memory_store + 与桌面端同格式的
  chat_session.json（两边互通）。

用法：python chat_server.py [port]   （默认 8903）
"""
import os
import sys
import json
import time
import queue
import threading
import datetime
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8903
from bh_paths import out_dir  # noqa: E402
OUT_DIR = out_dir()
SESSION_PATH = os.path.join(OUT_DIR, "chat_session.json")
CONFIG_PATH = os.path.join(OUT_DIR, "desktop_config.json")

import agent as agent_mod          # noqa: E402
import memory_store                # noqa: E402
import bh_api                      # noqa: E402  (启动时预热数据集)


# ---------------- 会话与配置（与桌面端同文件互通） ----------------
def load_cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_turns():
    try:
        with open(SESSION_PATH, encoding="utf-8") as f:
            return json.load(f).get("turns") or []
    except (OSError, ValueError):
        return []


def save_turns(turns, agent_state=None):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        state = {"turns": turns}
        if agent_state is not None:
            state["agent"] = agent_state
        with open(SESSION_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    except (OSError, TypeError, ValueError):
        pass


class ChatCore:
    """单实例聊天核心：持有 Agent 与轮次，向所有 SSE 订阅者广播事件。"""

    def __init__(self):
        self.lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.subscribers = []           # [queue]
        self.turns = load_turns()
        self.busy = False
        self.abort = False
        self._agent = None
        self._agent_sig = None
        self._pending_state = self._load_agent_state()

    def _load_agent_state(self):
        try:
            with open(SESSION_PATH, encoding="utf-8") as f:
                return json.load(f).get("agent")
        except (OSError, ValueError):
            return None

    def agent(self):
        cfg = load_cfg()
        key = cfg.get("api_key", "")
        base = cfg.get("api_base", "") or agent_mod.PROVIDERS.get(
            cfg.get("api_provider", "deepseek"),
            agent_mod.PROVIDERS["deepseek"])["base_url"]
        model = cfg.get("api_model", "") or "deepseek-chat"
        sig = (base, key, model)
        if self._agent_sig != sig:
            self._agent = agent_mod.Agent(base, key, model)
            self._agent_sig = sig
            p = self._pending_state
            if p and p.get("model") == model and p.get("base_url") == base:
                self._agent.state_load(p)
            self._pending_state = None
        return self._agent

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast(self, event):
        with self.lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.subscribers.remove(q)

    def send(self, q_text):
        q_text = str(q_text or "").strip()
        if not q_text:
            return {"error": "empty"}
        with self._send_lock:           # 检查+置位原子化，防并发双发双启动
            if self.busy:
                return {"error": "busy"}
            if not load_cfg().get("api_key"):
                return {"error": "no_api_key"}
            self.busy = True
        self.abort = False
        ts = time.strftime("%H:%M")
        turn_id = len(self.turns) + 1
        self.broadcast({"type": "turn_start", "turn_id": turn_id, "ts": ts,
                        "q": q_text})
        threading.Thread(target=self._worker, args=(q_text, ts, turn_id),
                         daemon=True).start()
        return {"ok": True, "turn_id": turn_id}

    def _worker(self, q_text, ts, turn_id):
        try:
            a = self.agent()
            reply = a.chat(
                q_text,
                on_delta=lambda frag: self.broadcast(
                    {"type": "delta", "turn_id": turn_id, "text": frag}),
                on_tool=lambda n, args, res: self.broadcast(
                    {"type": "tool", "turn_id": turn_id, "name": n,
                     "args": (args or "{}")[:120],
                     "brief": _brief(res)}),
                abort_check=lambda: self.abort)
            lt = a.last_turn
            turn = {"ts": ts, "q": q_text, "tools": lt.get("tools", []),
                    "elapsed": lt.get("elapsed"), "usage": lt.get("usage"),
                    "reply_md": reply or ""}
            with self.lock:
                self.turns.append(turn)
                save_turns(self.turns, a.state_save())
            self.broadcast({"type": "done", "turn_id": turn_id,
                            "elapsed": lt.get("elapsed"),
                            "usage": lt.get("usage"),
                            "n_tools": len(lt.get("tools", [])),
                            "reply_md": reply or "",
                            "stopped": self.abort})
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()      # 完整栈进服务日志，便于定位
            self.broadcast({"type": "error", "turn_id": turn_id,
                            "message": f"{type(e).__name__}: {e}"})
        finally:
            self.busy = False

    def stop(self):
        self.abort = True
        return {"ok": True}

    def clear(self):
        with self.lock:
            self.turns = []
            save_turns(self.turns)
        if self._agent:
            self._agent.reset()
        self.broadcast({"type": "cleared"})
        return {"ok": True}


def _brief(res):
    if isinstance(res, dict):
        if "error" in res:
            return str(res["error"])[:80]
        return "ok"
    if isinstance(res, str):
        return res.strip().split("\n")[0][:80]
    return json.dumps(res, ensure_ascii=False)[:80]   # list（如 kb_search）等


CORE = ChatCore()


def _state_payload():
    cfg = load_cfg()
    ms = memory_store.stats()
    new_matches = 0
    try:
        bh_api._ensure()
        cur = len(bh_api._DS["matches"]) if bh_api._DS else None
        last = cfg.get("last_match_count")
        if isinstance(cur, int) and isinstance(last, int) and cur > last:
            new_matches = cur - last
    except Exception:  # noqa: BLE001
        pass
    return {
        "turns": CORE.turns,
        "configured": bool(cfg.get("api_key")),
        "model": cfg.get("api_model") or "deepseek-chat",
        "memory_facts": ms.get("facts", 0),
        "busy": CORE.busy,
        "new_matches": new_matches,
    }


# ---------------- HTML 页面（shadcn 深色风） ----------------
PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Brawlhalla AI 教练</title>
<style>
:root{--bg:#0a0a0a;--fg:#fafafa;--card:#171717;--muted:#a3a3a3;
--border:rgba(255,255,255,.1);--accent:#ea580c;--accent2:#ff8a3d;
--good:#34d399;--radius:12px}
:root.light{--bg:#fafafa;--fg:#171717;--card:#ffffff;--muted:#737373;
--border:#e5e5e5;--accent:#ea580c;--accent2:#c2410c;--good:#0d9488}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:"Segoe UI",system-ui,"Microsoft YaHei UI",sans-serif;
font-size:14px;line-height:1.7;-webkit-font-smoothing:antialiased}
#app{max-width:780px;margin:0 auto;display:flex;flex-direction:column;
height:100vh}
header{display:flex;align-items:center;gap:10px;padding:14px 20px;
border-bottom:1px solid var(--border)}
header .logo{width:26px;height:26px;border-radius:8px;
background:linear-gradient(135deg,#ea580c,#ffb454);
display:flex;align-items:center;justify-content:center;font-size:14px}
header h1{font-size:15px;margin:0;font-weight:600}
#status{color:var(--muted);font-size:12px;margin-left:auto}
header button{background:var(--card);border:1px solid var(--border);
color:var(--muted);border-radius:8px;padding:4px 12px;font-size:12px;
cursor:pointer;transition:.15s}
header button:hover{color:var(--fg);border-color:var(--accent)}
#flow{flex:1;overflow-y:auto;padding:20px 20px 8px;scroll-behavior:smooth}
.msg{margin:14px 0}
.msg .who{display:inline-flex;align-items:center;font-size:11px;font-weight:700;
border-radius:6px;padding:1px 8px;margin-bottom:6px}
.msg.user .who{background:rgba(234,88,12,.18);color:var(--accent2)}
.msg.ai .who{background:rgba(52,211,153,.15);color:var(--good)}
.msg .ts{color:var(--muted);font-size:11px;margin-left:6px}
.msg.user .bubble{background:var(--card);border:1px solid var(--border);
border-radius:var(--radius);padding:10px 14px;white-space:pre-wrap}
.md h1,.md h2,.md h3{margin:14px 0 6px;font-weight:650;letter-spacing:-.01em}
.md h1{font-size:18px}.md h2{font-size:15.5px}.md h3{font-size:14px}
.md p{margin:8px 0}
.md ul,.md ol{margin:6px 0;padding-left:22px}
.md li{margin:4px 0}
.md code{font-family:Consolas,monospace;font-size:12.5px;background:#262626;
border-radius:5px;padding:1px 6px}
:root.light .md code{background:#f0f0f0}
#theme{background:none;border:1px solid var(--border);color:var(--muted);
border-radius:8px;width:28px;height:28px;cursor:pointer;font-size:14px}
.md table{border-collapse:collapse;margin:10px 0;font-size:12.5px;width:100%}
.md th,.md td{border:1px solid var(--border);padding:5px 10px;text-align:left}
.md th{color:var(--muted);font-weight:600}
.md hr{border:none;border-top:1px solid var(--border);margin:12px 0}
.md strong{font-weight:700}
.md blockquote{border-left:3px solid var(--border);margin:8px 0;padding:2px 12px;
color:var(--muted)}
details.tools{border:1px solid var(--border);border-radius:10px;
background:var(--card);margin:10px 0;overflow:hidden}
details.tools summary{cursor:pointer;padding:8px 14px;color:var(--muted);
font-size:12.5px;list-style:none;display:flex;align-items:center;gap:8px;
user-select:none}
details.tools summary::before{content:"▸";transition:.15s;font-size:10px}
details.tools[open] summary::before{transform:rotate(90deg)}
details.tools summary:hover{color:var(--fg)}
details.tools .body{padding:4px 16px 12px;border-top:1px solid var(--border)}
.tool-item{margin:8px 0;font-size:12px;color:var(--muted)}
.tool-item .name{color:var(--accent2);font-family:Consolas,monospace}
#welcome{background:linear-gradient(180deg,var(--card),transparent);
border:1px solid var(--border);border-radius:16px;padding:24px;margin:20px 0}
#welcome h2{margin:0 0 6px;font-size:20px;
background:linear-gradient(90deg,#ff8a3d,#ffd08a);
-webkit-background-clip:text;background-clip:text;color:transparent}
#welcome p{color:var(--muted);margin:4px 0 14px;font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--card);border:1px solid var(--border);border-radius:20px;
padding:5px 14px;font-size:12.5px;color:var(--fg);cursor:pointer;transition:.15s}
.chip:hover{border-color:var(--accent);color:var(--accent2)}
#inputbar{padding:12px 20px 18px}
.inputcard{background:var(--card);border:1px solid var(--border);
border-radius:14px;padding:10px 12px;display:flex;gap:10px;align-items:flex-end}
#q{flex:1;background:transparent;border:none;outline:none;color:var(--fg);
font:inherit;resize:none;max-height:140px;line-height:1.6}
#q::placeholder{color:#5a5a5a}
#send{background:var(--accent);color:#fff;border:none;border-radius:10px;
padding:8px 18px;font-size:13px;font-weight:700;cursor:pointer;transition:.15s}
#send:hover{background:var(--accent2)}
#send.stop{background:#dc2626}
#hint{color:#5a5a5a;font-size:11px;text-align:center;margin-top:6px}
.cursor{display:inline-block;width:7px;height:15px;background:var(--accent2);
vertical-align:-2px;margin-left:2px;animation:blink 1s step-start infinite;
border-radius:2px}
@keyframes blink{50%{opacity:0}}
.stopped-tag{color:var(--muted);font-size:11px;font-style:italic}
</style></head><body>
<div id="app">
<header>
  <div class="logo">⚔</div><h1>Brawlhalla AI 教练</h1>
  <span id="status"></span>
  <button id="theme" onclick="toggleTheme()" title="亮/暗主题">◐</button>
  <button onclick="clearChat()">清空</button>
  <button onclick="exportChat()">导出</button>
</header>
<div id="flow"></div>
<div id="inputbar">
  <div class="inputcard">
    <textarea id="q" rows="1" placeholder="问任何问题…（Enter 发送，Shift+Enter 换行，↑ 历史）"></textarea>
    <button id="send" onclick="uiSend()">发送</button>
  </div>
  <div id="hint">数字均来自本地 293 场回放分析 · 长期记忆自动生效</div>
</div></div>
<script>
let busy=false, hist=[], histIdx=-1, es=null, curDeltaEl=null;

function toggleTheme(){
  const el=document.documentElement;
  const dark=!el.classList.contains('light');
  el.classList.toggle('light',dark);
  try{localStorage.setItem('bh-chat-theme',dark?'light':'dark')}catch(e){}
}
(function(){
  try{
    if(localStorage.getItem('bh-chat-theme')==='light')
      document.documentElement.classList.add('light');
    else if(localStorage.getItem('bh-chat-theme')==='dark'){}
    else if(window.matchMedia('(prefers-color-scheme: light)').matches)
      document.documentElement.classList.add('light');
  }catch(e){}
})();

const flow=document.getElementById('flow'), qEl=document.getElementById('q'),
      sendBtn=document.getElementById('send'), statusEl=document.getElementById('status');

function md(t){
  // mini markdown: 标题/加粗/代码/列表/表格/引用/分隔线
  const lines=String(t||'').split('\n'); let out='', i=0, list=null;
  const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const inline=s=>esc(s)
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  while(i<lines.length){
    const L=lines[i], s=L.trim();
    const closeList=()=>{if(list){out+=`</${list}>`;list=null}};
    if(!s){closeList();i++;continue}
    let m;
    if(m=s.match(/^(#{1,4})\s+(.*)/)){closeList();
      const lv=Math.min(m[1].length,3);
      out+=`<h${lv}>${inline(m[2])}</h${lv}>`;i++;continue}
    if(/^(-{3,}|\*{3,})$/.test(s)){closeList();out+='<hr>';i++;continue}
    if(m=s.match(/^[-*•]\s+(.*)/)){if(list!=='ul'){closeList();out+='<ul>';list='ul'}
      out+=`<li>${inline(m[1])}</li>`;i++;continue}
    if(m=s.match(/^(\d+)[.、)]\s+(.*)/)){if(list!=='ol'){closeList();out+='<ol>';list='ol'}
      out+=`<li>${inline(m[2])}</li>`;i++;continue}
    if(s.startsWith('|')&&i+1<lines.length&&/^\|[\s:|-]+\|?$/.test(lines[i+1].trim())){
      closeList();
      const head=s.split('|').slice(1,-1).map(x=>x.trim());
      i+=2; let rows=[];
      while(i<lines.length&&lines[i].trim().startsWith('|')){
        rows.push(lines[i].trim().split('|').slice(1,-1).map(x=>x.trim()));i++}
      out+='<table><thead><tr>'+head.map(h=>`<th>${inline(h)}</th>`).join('')+
        '</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
      continue}
    if(m=s.match(/^>\s?(.*)/)){closeList();
      out+=`<blockquote>${inline(m[1])}</blockquote>`;i++;continue}
    closeList();
    // 连续普通行合并成段落
    let para=[s]; i++;
    while(i<lines.length){const nx=lines[i].trim();
      if(!nx||/^(#{1,4}\s|[-*•]\s|\d+[.、)]\s|\||>|-{3,}$)/.test(nx))break;
      para.push(nx);i++}
    out+=`<p>${inline(para.join('<br>'))}</p>`;
  }
  if(list)out+=`</${list}>`;
  return out;
}

function el(tag,cls,html){const e=document.createElement(tag);
  if(cls)e.className=cls; if(html!=null)e.innerHTML=html; return e}

function addTurn(t,animate){
  const m=el('div','msg '+(t.role||'user'));
  const who=el('div','who',t.role==='ai'?' 助手 ':' 你 ');
  who.appendChild(el('span','ts',(t.ts||'')+
    (t.elapsed!=null?` · ${t.elapsed}s`:'')+
    (t.usage?` · ${(t.usage/1000).toFixed(1)}k tk`:'')));
  m.appendChild(who);
  if(t.role==='ai'){
    const body=el('div','md'); body.innerHTML=md(t.reply_md); m.appendChild(body);
    if(t.tools&&t.tools.length) m.insertBefore(toolsBlock(t),body);
  }else{
    m.appendChild(el('div','bubble',String(t.q||'').replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')));
  }
  flow.appendChild(m);
  if(animate)flow.scrollTop=flow.scrollHeight;
  return m;
}

function toolsBlock(t){
  const d=el('details','tools');
  const names=[...new Set(t.tools.map(x=>x.name))].join('、');
  d.innerHTML=`<summary>⚙ 调用 ${t.tools.length} 个工具${t.elapsed!=null?` · ${t.elapsed}s`:''}${t.usage?` · ${(t.usage/1000).toFixed(1)}k tk`:''}：${names}</summary>`;
  const b=el('div','body');
  t.tools.forEach(x=>{
    b.appendChild(el('div','tool-item',
      `<span class="name">${esc2(x.name)}</span>(${esc2((x.args||'').slice(0,80))})<br>`+
      String(x.result||'').split('\n').slice(0,4).map(l=>esc2(l.slice(0,110))).join('<br>')));
  });
  d.appendChild(b);
  return d;
}
function esc2(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}

async function refreshStatus(){
  // 只负责拉状态并更新 header——绝不回调 loadState（防递归饿死渲染）
  try{
    const s=await(await fetch('/api/state')).json();
    statusEl.textContent=s.configured
      ?`${s.model} · 记忆${s.memory_facts}条`
      :'未配置 API Key（在桌面软件 ⚙ 设置里填）';
    if(!s.busy&&busy){busy=false;setBusyUI(false)}
    return s;
  }catch(e){
    statusEl.textContent='⚠ 服务未运行（打开桌面软件后自动恢复）';
    throw e;
  }
}

async function loadState(){
  try{
    const s=await refreshStatus();
    flow.innerHTML='';
    if(!s.turns.length){flow.appendChild(welcomeCard(s))}
    else{
      s.turns.forEach(t=>{addTurn({role:'user',ts:t.ts,q:t.q});addTurn({role:'ai',ts:t.ts,...t})});
      flow.scrollTop=flow.scrollHeight;
    }
    busy=s.busy; setBusyUI(busy);
    connectSSE();
  }catch(e){
    flow.innerHTML='';
    flow.appendChild(el('div','md',
      '<p style="color:#f87171">⚠ 加载失败：服务未响应。<br>请打开桌面软件后刷新本页。</p>'));
  }
}

async function reconcile(){
  // SSE 重连后的对账：断线期间若有轮次完成但页面没收到 done，补渲染
  try{
    if(busy)return;
    const s=await(await fetch('/api/state')).json();
    const shown=flow.querySelectorAll('.msg.ai').length;
    if(s.turns.length&&shown<s.turns.length){
      flow.innerHTML='';
      s.turns.forEach(t=>{addTurn({role:'user',ts:t.ts,q:t.q});addTurn({role:'ai',ts:t.ts,...t})});
      flow.scrollTop=flow.scrollHeight;
    }
  }catch(e){}
}

function welcomeCard(s){
  const w=el('div'); w.id='welcome';
  w.innerHTML=`<h2>你的 AI 打法教练</h2>
  <p>本地 293 场回放 + 游戏知识库 + 长期记忆${s.new_matches?` · 自上次有 <b>${s.new_matches}</b> 场新对局`:''}</p>`;
  const c=el('div','chips');
  ['Zett 怎么打？给我情报卡','对比 Zett 和 JR • KillerBee',
   '找出受压时往右 dash 逃逸超 40% 的对手','给我一份训练计划，下周检查进度',
   '复盘我最近输的一场'].forEach(x=>{
    const chip=el('div','chip',x);
    chip.onclick=()=>{qEl.value=x;qEl.focus()};
    c.appendChild(chip)});
  w.appendChild(c);
  return w;
}

function connectSSE(){
  if(es)es.close();
  es=new EventSource('/api/events');
  es.onopen=()=>{ refreshStatus(); reconcile(); };  // 重连后刷新状态并对账补齐
  es.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d.type==='turn_start'){busy=true;setBusyUI(true);
      addTurn({role:'user',ts:d.ts,q:d.q});
      const m=addTurn({role:'ai',ts:d.ts});
      const body=el('div','md'); body.innerHTML='<span class="cursor"></span>';
      m.appendChild(body); curDeltaEl=body;
      document.querySelectorAll('#welcome').forEach(x=>x.remove());
      flow.scrollTop=flow.scrollHeight}
    else if(d.type==='delta'&&curDeltaEl){
      curDeltaEl.dataset.buf=(curDeltaEl.dataset.buf||'')+d.text;
      curDeltaEl.innerHTML=md(curDeltaEl.dataset.buf)+'<span class="cursor"></span>';
      autoscroll()}
    else if(d.type==='tool'&&curDeltaEl){
      curDeltaEl.dataset.tools=(curDeltaEl.dataset.tools?curDeltaEl.dataset.tools+'\n':'')+
        `⚙ ${d.name} → ${d.brief}`}
    else if(d.type==='done'){
      busy=false;setBusyUI(false);
      if(curDeltaEl){
        const bodyEl=curDeltaEl, m=bodyEl.closest('.msg');
        const meta=bodyEl.dataset.tools;
        bodyEl.innerHTML=md(d.reply_md||bodyEl.dataset.buf||'')+
          (d.stopped?' <span class="stopped-tag">（已停止）</span>':'');
        curDeltaEl=null;
        if(d.n_tools&&!m.querySelector('details.tools')){
          const det=el('details','tools');
          det.innerHTML=`<summary>⚙ 调用 ${d.n_tools} 个工具 · ${d.elapsed}s${d.usage?` · ${(d.usage/1000).toFixed(1)}k tk`:''}</summary><div class="body">${esc2(meta||'')}</div>`;
          m.insertBefore(det,bodyEl)
        }
      }else{
        // 页面在轮次中途才加载（刷新/断线重连）：没收到流式事件，重拉状态补渲染
        loadState()
      }
      flow.scrollTop=flow.scrollHeight}
    else if(d.type==='cleared'){loadState()}
    else if(d.type==='error'){busy=false;setBusyUI(false);
      if(curDeltaEl){curDeltaEl.innerHTML=`<p style="color:#f87171">出错了：${esc2(d.message)}</p>`;curDeltaEl=null}}
  };
}

function autoscroll(){if(flow.scrollHeight-flow.scrollTop-flow.clientHeight<160)
  flow.scrollTop=flow.scrollHeight}

function setBusyUI(b){
  sendBtn.textContent=b?'停止':'发送';
  sendBtn.className=b?'stop':'';
}

async function uiSend(){
  if(busy){await fetch('/api/stop',{method:'POST'});return}
  const q=qEl.value.trim(); if(!q)return;
  hist.push(q);histIdx=-1;qEl.value='';
  try{
    const r=await fetch('/api/send',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
    const d=await r.json();
    if(d.error==='no_api_key'){alert('先在桌面软件 ⚙ 设置里配置 API Key');refreshStatus()}
    else if(d.error==='busy'){}
  }catch(e){
    // 服务不可达：把问题还原回输入框，别让用户丢字
    hist.pop();qEl.value=q;refreshStatus().catch(()=>{})
  }
}

async function clearChat(){
  if(!confirm('清空当前对话？（长期记忆保留）'))return;
  await fetch('/api/clear',{method:'POST'});
}
async function exportChat(){
  const s=await(await fetch('/api/state')).json();
  let mdTxt='# Brawlhalla AI 对话导出\n\n';
  s.turns.forEach(t=>{mdTxt+=`## ${t.ts} 你\n${t.q}\n\n`;
    if(t.tools&&t.tools.length)mdTxt+=`*（${t.tools.length} 工具 · ${t.elapsed}s）*\n\n`;
    mdTxt+=t.reply_md+'\n\n'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([mdTxt],{type:'text/markdown'}));
  a.download='chat_export.md';a.click();
}

qEl.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.ctrlKey){e.preventDefault();uiSend()}
  else if(e.key==='ArrowUp'&&qEl.selectionStart===0&&hist.length){
    e.preventDefault();
    histIdx=Math.min(histIdx+1,hist.length-1);
    qEl.value=hist[hist.length-1-histIdx]}
  else if(e.key==='Escape'){qEl.value=''}
});
qEl.addEventListener('input',()=>{
  qEl.style.height='auto';qEl.style.height=Math.min(qEl.scrollHeight,140)+'px'});
loadState().then(()=>{
  const qp=new URLSearchParams(location.search).get('q');
  if(qp){qEl.value=qp;qEl.focus()}
});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, json.dumps(_state_payload(), ensure_ascii=False))
        elif self.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            q = CORE.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=20)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    data = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (OSError, ConnectionError):
                pass
            finally:
                CORE.unsubscribe(q)
        else:
            self._send(404, '{"error":"not found"}')

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                body = {}
        except (ValueError, OSError):
            self._send(400, '{"error":"bad request"}')
            return
        if self.path == "/api/send":
            self._send(200, json.dumps(CORE.send(str(body.get("q", ""))[:2000]),
                                       ensure_ascii=False))
        elif self.path == "/api/stop":
            self._send(200, json.dumps(CORE.stop()))
        elif self.path == "/api/clear":
            self._send(200, json.dumps(CORE.clear()))
        else:
            self._send(404, '{"error":"not found"}')

    def log_message(self, *a):
        pass


def run_server(port=None, foreground=False):
    """启动 HTTP 服务。桌面端以后台线程调用（foreground=False）。

    幂等：端口已在服务时直接返回——桌面端「数据加载后自动启动」与
    「点击打开聊天」可能并发触发；Windows 上 SO_REUSEADDR 允许重复绑定，
    双实例会随机吞掉连接，必须先探测。"""
    global PORT
    if port:
        PORT = port
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
            print(f"chat server already on http://127.0.0.1:{PORT}/")
            return
    except OSError:
        pass
    threading.Thread(target=lambda: bh_api.list_opponents(limit=1),
                     daemon=True).start()   # 后台预热数据集
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        return                              # 探测后的并发窗口被抢，视为已在服务
    print(f"chat server on http://127.0.0.1:{PORT}/")
    srv.serve_forever()


def main():
    run_server(PORT, foreground=True)


if __name__ == "__main__":
    main()
