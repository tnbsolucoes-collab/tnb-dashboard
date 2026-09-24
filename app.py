import os
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import json
import base64
from urllib.parse import urlparse
from pywebpush import webpush, WebPushException
from webauthn import generate_registration_options, verify_registration_response, generate_authentication_options, verify_authentication_response
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, AuthenticatorSelectionCriteria, UserVerificationRequirement, ResidentKeyRequirement
from webauthn.helpers import options_to_json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    if not DATABASE_URL:
        return
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
      id SERIAL PRIMARY KEY,
      name VARCHAR(120) NOT NULL,
      email VARCHAR(180) UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      is_admin BOOLEAN NOT NULL DEFAULT FALSE,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS sales(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      product VARCHAR(180) NOT NULL,
      platform VARCHAR(80) NOT NULL,
      amount NUMERIC(12,2) NOT NULL CHECK(amount > 0),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS adjustments(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      amount NUMERIC(12,2) NOT NULL,
      reason VARCHAR(220) NOT NULL,
      created_by INTEGER NOT NULL REFERENCES users(id),
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS goals(
      user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      monthly_goal NUMERIC(12,2) NOT NULL DEFAULT 10000
    );
    CREATE TABLE IF NOT EXISTS integrations(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      platform VARCHAR(80) NOT NULL,
      status VARCHAR(30) NOT NULL DEFAULT 'not_connected',
      created_at TIMESTAMP NOT NULL DEFAULT NOW(),
      UNIQUE(user_id, platform)
    );
    CREATE TABLE IF NOT EXISTS site_projects(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      client_name VARCHAR(140) NOT NULL,
      project_name VARCHAR(180) NOT NULL,
      project_value NUMERIC(12,2) NOT NULL DEFAULT 0,
      domain_url TEXT,
      deadline DATE,
      monthly_maintenance NUMERIC(12,2) NOT NULL DEFAULT 0,
      status VARCHAR(40) NOT NULL DEFAULT 'Orçamento',
      created_at TIMESTAMP NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS push_subscriptions(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      endpoint TEXT UNIQUE NOT NULL,
      subscription_json TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS passkeys(
      id SERIAL PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      credential_id TEXT UNIQUE NOT NULL,
      public_key BYTEA NOT NULL,
      sign_count BIGINT NOT NULL DEFAULT 0,
      device_type VARCHAR(40),
      backed_up BOOLEAN DEFAULT FALSE,
      created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print("DB init:", e)

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

def money(v):
    s = f"{float(v or 0):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

app.jinja_env.filters["money"] = money

BASE_STYLE = """
<style>
:root{--bg:#07090a;--card:#101516;--line:#203033;--text:#f4f7f6;--muted:#8e9a98;--green:#28e59b;--cyan:#35d8e6;--danger:#ff6474}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,Arial;background:
radial-gradient(circle at 85% 8%,#0a2a55 0,#050b15 30%,#02050a 62%),
linear-gradient(135deg,#02040a,#06101f);background-attachment:fixed;color:var(--text);min-height:100vh}
a{color:inherit}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{border-right:1px solid var(--line);padding:28px 20px;background:linear-gradient(180deg,#030712,#071426 55%,#02050a)}
.logo{font-weight:900;font-size:22px}.logo span{color:var(--green)}.muted{color:var(--muted)}nav a{display:block;margin:9px 0;padding:13px;border-radius:12px;text-decoration:none;color:#b8c2c0}nav a:hover{background:#0a2342;color:#63b3ff;transform:translateX(3px)}nav a{transition:.2s ease}
main{padding:30px;max-width:1400px;width:100%}.top{display:flex;justify-content:space-between;gap:15px;align-items:center}.top h1{margin:0;font-size:28px}.btn{display:inline-block;border:0;border-radius:12px;padding:12px 16px;font-weight:800;background:linear-gradient(135deg,#1478ff,#43a8ff);color:white;box-shadow:0 8px 24px #1478ff33;cursor:pointer;text-decoration:none}
.btn.secondary{background:#10233c;color:white}.btn.danger{background:#3a1b20;color:#ff8a96}.btn.small{padding:8px 10px;font-size:12px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:25px 0}.card,.panel{background:linear-gradient(145deg,#0b1422,#050a12);border:1px solid #16365c;border-radius:18px;padding:20px;box-shadow:0 15px 40px #0005}
.card label{color:var(--muted);font-size:13px}.value{font-size:27px;font-weight:900;margin-top:8px}.up{font-size:12px;color:var(--green);margin-top:6px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}
input,select{width:100%;padding:12px;margin:7px 0 13px;border-radius:10px;border:1px solid #1c4778;background:#050b14;color:white}.sale{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid #102b4a}.sale b{color:var(--green)}
.goal{height:11px;background:#0b1a2c;border-radius:20px;overflow:hidden}.goal i{display:block;height:100%;background:linear-gradient(90deg,#1478ff,#63c5ff)}
.auth{max-width:430px;margin:8vh auto;padding:28px}.auth .logo{text-align:center;margin-bottom:24px}.flash{padding:12px;border:1px solid #5c3b3f;background:#261417;border-radius:10px;margin:10px 0}.badge{font-size:11px;padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
@keyframes rise{from{opacity:0;transform:translateY(18px) scale(.985)}to{opacity:1;transform:translateY(0) scale(1)}}@keyframes glow{0%,100%{box-shadow:0 0 5px #258cff33}50%{box-shadow:0 0 30px #258cff88}}@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}@keyframes shine{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}.card,.panel{animation:rise .55s cubic-bezier(.2,.8,.2,1) both;transition:transform .25s ease,border-color .25s ease,box-shadow .25s ease}.card:nth-child(2){animation-delay:.07s}.card:nth-child(3){animation-delay:.14s}.card:nth-child(4){animation-delay:.21s}.card:hover,.panel:hover{transform:translateY(-5px);border-color:#258cff;box-shadow:0 18px 45px #006dff22}.btn{transition:transform .2s ease,box-shadow .2s ease,filter .2s ease}.btn:hover{transform:translateY(-2px) scale(1.02);box-shadow:0 10px 28px #1478ff55;filter:brightness(1.08)}.goal i{animation:glow 1.8s ease infinite;transition:width .8s ease}.logo span{animation:glow 2.2s ease infinite}.top h1{background:linear-gradient(90deg,#fff,#66b9ff,#fff);background-size:200% auto;-webkit-background-clip:text;background-clip:text;color:transparent;animation:shine 5s linear infinite}
@media(max-width:900px){.app{grid-template-columns:1fr}.side{display:none}.cards{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}main{padding:18px}}
@media(max-width:520px){.cards{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.top h1{font-size:23px}}
@media(max-width:900px){
.app{display:block}
.side{display:block;position:fixed;top:0;left:0;width:260px;height:100vh;overflow-y:auto;transform:translateX(-110%);transition:transform .3s ease;z-index:1001;box-shadow:18px 0 50px #0009}
.side.open{transform:translateX(0)}
main{margin-left:0;padding:76px 18px 18px}.cards{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}
.menu-toggle{display:flex}
.menu-overlay{display:block;position:fixed;inset:0;background:#0009;opacity:0;visibility:hidden;transition:.3s;z-index:1000;backdrop-filter:blur(3px)}
.menu-overlay.show{opacity:1;visibility:visible}
}
@media(min-width:901px){.menu-toggle,.menu-overlay{display:none!important}}
.menu-toggle{position:fixed;top:14px;left:14px;width:46px;height:46px;border:1px solid #1c4778;border-radius:12px;background:#071426;color:#fff;z-index:1002;align-items:center;justify-content:center;flex-direction:column;gap:5px;cursor:pointer;box-shadow:0 8px 30px #0008}
.menu-toggle span{display:block;width:22px;height:2px;background:#63b3ff;border-radius:3px;transition:.25s}
.menu-toggle.active span:nth-child(1){transform:translateY(7px) rotate(45deg)}
.menu-toggle.active span:nth-child(2){opacity:0}
.menu-toggle.active span:nth-child(3){transform:translateY(-7px) rotate(-45deg)}
</style>
"""

AUTH = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} - Bithfy</title>""" + BASE_STYLE + """
<div class="auth panel"><div class="logo">Bith<span>fy</span></div><h2>{{title}}</h2>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<form method="post">
{% if register %}<label>Nome</label><input name="name" required maxlength="120">{% endif %}
<label>E-mail</label><input name="email" type="email" required>
<label>Senha</label><input name="password" type="password" required minlength="6">
<button class="btn" style="width:100%">{{button}}</button></form>
{% if not register %}<button class="btn secondary" id="quickLogin" style="width:100%;margin-top:10px" type="button">🔐 Entrar com Face ID / passkey</button>
<script>
function b64u(v){v=v.replace(/-/g,'+').replace(/_/g,'/');while(v.length%4)v+='=';return Uint8Array.from(atob(v),c=>c.charCodeAt(0))}
function enc(b){return btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')}
document.getElementById('quickLogin').onclick=async()=>{
 try{
  let r=await fetch('/passkey/login/options',{method:'POST'}),o=await r.json();
  o.challenge=b64u(o.challenge);if(o.allowCredentials)o.allowCredentials=o.allowCredentials.map(x=>({...x,id:b64u(x.id)}));
  let c=await navigator.credentials.get({publicKey:o});
  let body={id:c.id,rawId:enc(c.rawId),type:c.type,response:{clientDataJSON:enc(c.response.clientDataJSON),authenticatorData:enc(c.response.authenticatorData),signature:enc(c.response.signature),userHandle:c.response.userHandle?enc(c.response.userHandle):null}};
  r=await fetch('/passkey/login/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let j=await r.json(); if(j.ok) location='/'; else alert(j.error||'Não foi possível entrar.');
 }catch(e){alert('Face ID/passkey indisponível ou cancelado.');}
};
</script>{% endif %}
<p class="muted" style="text-align:center">{{bottom|safe}}</p></div></html>"""

DASH = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#050b15"><link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><title>Bithfy</title>""" + BASE_STYLE + """
<button class="menu-toggle" id="menuToggle" type="button" aria-label="Abrir menu"><span></span><span></span><span></span></button>
<div class="menu-overlay" id="menuOverlay"></div>
<div class="app"><aside class="side"><div class="logo">Bith<span>fy</span></div><p class="muted">Central de vendas Bithfy</p><nav>
<a href="/">◈ Visão geral</a><a href="/sale/new">＋ Registrar venda</a><a href="/sites">🌐 Sites/Clientes</a><a href="/goal">◎ Alterar meta</a><a href="/integrations">⌁ Integrações</a>{% if is_admin %}<a href="/admin">♛ Administração</a>{% endif %}<a href="/logout">↪ Sair</a></nav></aside>
<main><div class="top"><div><span class="badge">{% if is_admin %}ADMIN{% else %}USUÁRIO{% endif %}</span><h1>Olá, {{name}} 👋</h1></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn secondary" id="notifyBtn" type="button">🔔 Ativar notificações</button><button class="btn secondary" id="passkeyBtn" type="button">🔐 Ativar Face ID</button><a class="btn" href="/sale/new">+ Registrar venda</a></div></div>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<section class="cards"><div class="card"><label>Faturamento hoje</label><div class="value">{{today|money}}</div><div class="up">Vendas registradas hoje</div></div>
<div class="card"><label>Vendas no mês</label><div class="value">{{month_sales|money}}</div><div class="up">Somente vendas reais registradas</div></div>
<div class="card"><label>Ajustes manuais</label><div class="value">{{adjustments|money}}</div><div class="up">Separado das vendas</div></div>
<div class="card"><label>Saldo total</label><div class="value">{{total|money}}</div><div class="up">{{count}} vendas no mês</div></div></section>
<section class="grid"><div class="panel"><h3>Últimos 7 dias</h3><canvas id="chart" height="115"></canvas></div>
<div class="panel"><h3>Meta do mês</h3><div class="value">{{month_sales|money}} / {{goal|money}}</div><p class="muted">{{pct}}% concluída</p><div class="goal"><i style="width:{{pct}}%"></i></div></div></section>
<section class="panel" style="margin-top:16px"><h3>Vendas recentes</h3>{% for s in sales %}<div class="sale"><span>{{s.product}} <small class="muted">• {{s.platform}} • {{s.created_at.strftime('%d/%m %H:%M')}}</small></span><span style="display:flex;align-items:center;gap:9px"><b>+ {{s.amount|money}}</b>{% if is_admin %}<form method="post" action="/admin/sale/{{s.id}}/delete" onsubmit="return confirm('Apagar esta venda? Essa ação não pode ser desfeita.')"><button class="btn danger small" type="submit">🗑 Apagar</button></form>{% endif %}</span></div>{% else %}<p class="muted">Nenhuma venda cadastrada ainda.</p>{% endfor %}</section>
</main></div><script src="https://cdn.jsdelivr.net/npm/chart.js"></script><script>
new Chart(document.getElementById('chart'),{type:'line',data:{labels:{{labels|safe}},datasets:[{data:{{values|safe}},borderColor:'#258cff',backgroundColor:'#258cff22',fill:true,tension:.4}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8e9a98'},grid:{display:false}},y:{ticks:{color:'#8e9a98'},grid:{color:'#102b4a'}}}}});
document.querySelectorAll('.value').forEach((el,i)=>{el.style.animation=`rise .45s ease ${i*.07}s both`;});
(function(){
 const btn=document.getElementById('menuToggle'), side=document.querySelector('.side'), overlay=document.getElementById('menuOverlay');
 if(!btn||!side||!overlay)return;
 function menu(open){side.classList.toggle('open',open);overlay.classList.toggle('show',open);btn.classList.toggle('active',open);document.body.style.overflow=open?'hidden':'';}
 btn.addEventListener('click',()=>menu(!side.classList.contains('open')));
 overlay.addEventListener('click',()=>menu(false));
 side.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>menu(false)));
 window.addEventListener('resize',()=>{if(innerWidth>900)menu(false)});
})();
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js');}
function b64urlToBuf(v){v=v.replace(/-/g,'+').replace(/_/g,'/');while(v.length%4)v+='=';return Uint8Array.from(atob(v),c=>c.charCodeAt(0));}
function bufToB64url(buf){return btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
const notifyBtn=document.getElementById('notifyBtn');
if(notifyBtn) notifyBtn.onclick=async()=>{
 try{
  const reg=await navigator.serviceWorker.ready;
  const permission=await Notification.requestPermission();
  if(permission!=='granted') return alert('Permissão de notificações não concedida.');
  const r=await fetch('/push/public-key'); const j=await r.json();
  if(!j.publicKey) return alert('Notificações ainda precisam das chaves VAPID no Render.');
  const sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64urlToBuf(j.publicKey)});
  await fetch('/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});
  alert('Notificações ativadas neste aparelho.');
 }catch(e){alert('Não foi possível ativar notificações neste aparelho.');}
};
const passkeyBtn=document.getElementById('passkeyBtn');
if(passkeyBtn) passkeyBtn.onclick=async()=>{
 try{
  let r=await fetch('/passkey/register/options',{method:'POST'}); let o=await r.json();
  o.challenge=b64urlToBuf(o.challenge); o.user.id=b64urlToBuf(o.user.id);
  if(o.excludeCredentials)o.excludeCredentials=o.excludeCredentials.map(x=>({...x,id:b64urlToBuf(x.id)}));
  const cred=await navigator.credentials.create({publicKey:o});
  const body={id:cred.id,rawId:bufToB64url(cred.rawId),type:cred.type,response:{clientDataJSON:bufToB64url(cred.response.clientDataJSON),attestationObject:bufToB64url(cred.response.attestationObject)}};
  r=await fetch('/passkey/register/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json(); alert(j.ok?'Face ID/passkey ativado para acesso rápido.':(j.error||'Falha ao ativar.'));
 }catch(e){alert('Face ID/passkey não foi ativado.');}
};
</script></html>"""

FORM = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Registrar venda</title>""" + BASE_STYLE + """
<div class="auth panel"><div class="logo">Bith<span>fy</span></div><h2>Registrar venda</h2>
<form method="post"><label>Produto</label><input name="product" required maxlength="180"><label>Valor (R$)</label><input name="amount" type="number" min="0.01" step="0.01" required>
<label>Plataforma</label><select name="platform"><option>Cakto</option><option>Mercado Livre</option><option>Shopee</option><option>TikTok Shop</option><option>Outra</option></select>
<button class="btn" style="width:100%">Salvar venda</button></form><p style="text-align:center"><a href="/">Voltar ao painel</a></p></div></html>"""

ADMIN = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin Bithfy</title>""" + BASE_STYLE + """
<main style="max-width:900px;margin:auto"><div class="top"><div><span class="badge">ADMIN</span><h1>Administração</h1></div><a class="btn secondary" href="/">Voltar</a></div>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<div class="panel" style="margin-top:20px"><h3>Adicionar ajuste ao meu saldo</h3><p class="muted">Ajustes ficam identificados e não são contados como venda.</p>
<form method="post" action="/admin/adjust"><label>Valor (R$)</label><input name="amount" type="number" step="0.01" required><label>Motivo</label><input name="reason" required maxlength="220" placeholder="Ex.: ajuste inicial">
<button class="btn">Adicionar ajuste</button></form></div>
<div class="panel" style="margin-top:16px"><h3>Usuários</h3>{% for u in users %}<div class="sale"><span>{{u.name}} <small class="muted">• {{u.email}}</small></span><span class="badge">{% if u.is_admin %}ADMIN{% else %}USUÁRIO{% endif %}</span></div>{% endfor %}</div></main></html>"""

GOAL_FORM = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Meta mensal</title>""" + BASE_STYLE + """
<div class="auth panel"><div class="logo">Bith<span>fy</span></div><h2>Meta do mês</h2><p class="muted">Defina sua própria meta mensal. Ela altera a barra de progresso do seu painel.</p>
<form method="post"><label>Nova meta (R$)</label><input name="goal" type="number" min="1" step="0.01" value="{{goal}}" required>
<button class="btn" style="width:100%">Salvar meta</button></form><p style="text-align:center"><a href="/">Voltar ao painel</a></p></div></html>"""

INTEGRATIONS = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Integrações</title>""" + BASE_STYLE + """
<main style="max-width:1000px;margin:auto"><div class="top"><div><span class="badge">CONTAS</span><h1>Integrações</h1><p class="muted">Cada usuário conecta somente as próprias contas. Nunca informe sua senha das plataformas aqui.</p></div><a class="btn secondary" href="/">Voltar</a></div>
<div class="cards" style="grid-template-columns:repeat(3,1fr)">
{% for item in items %}
<div class="card"><label>{{item.name}}</label><div class="value" style="font-size:20px">{{item.icon}} {{item.name}}</div>
<p class="muted">{{item.text}}</p><span class="badge">{{item.status}}</span>
{% if item.ready %}<a class="btn" style="margin-top:15px" href="{{item.url}}">Conectar</a>{% else %}<button class="btn secondary" style="margin-top:15px" disabled>Preparado para API oficial</button>{% endif %}
</div>{% endfor %}</div>
<div class="panel"><b>Importante:</b> vendas automáticas só serão registradas quando a plataforma confirmar a transação por integração oficial/API/webhook. Esta tela não coleta senhas de Mercado Livre, Shopee ou TikTok.</div></main></html>"""



SITES = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sites e Clientes • Bithfy</title>""" + BASE_STYLE + """
<main style="max-width:1100px;margin:auto"><div class="top"><div><span class="badge">PROJETOS</span><h1>🌐 Sites / Clientes</h1><p class="muted">Organize clientes, valores, domínios, prazos, manutenção e pagamentos.</p></div><div style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="/sites/new">+ Novo projeto</a><a class="btn secondary" href="/">Voltar</a></div></div>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<section class="cards"><div class="card"><label>Projetos</label><div class="value">{{count}}</div></div><div class="card"><label>Valor dos projetos</label><div class="value">{{total_value|money}}</div></div><div class="card"><label>Manutenção mensal</label><div class="value">{{maintenance|money}}</div></div><div class="card"><label>Pagos</label><div class="value">{{paid}}</div></div></section>
<section class="panel" style="margin-top:16px"><h3>Meus projetos</h3>
{% for p in projects %}
<div class="sale" style="align-items:flex-start;gap:15px"><div style="min-width:0"><b>{{p.project_name}}</b><div class="muted" style="margin-top:5px">Cliente: {{p.client_name}} • {{p.status}}</div><div class="muted" style="margin-top:5px">Projeto: {{p.project_value|money}}{% if p.monthly_maintenance %} • Manutenção: {{p.monthly_maintenance|money}}/mês{% endif %}{% if p.deadline %} • Prazo: {{p.deadline.strftime('%d/%m/%Y')}}{% endif %}</div>{% if p.domain_url %}<div style="margin-top:6px"><a href="{{p.domain_url}}" target="_blank" rel="noopener">Abrir site/domínio ↗</a></div>{% endif %}</div><div style="display:flex;gap:7px;flex-wrap:wrap"><a class="btn secondary small" href="/sites/{{p.id}}/edit">Editar</a><form method="post" action="/sites/{{p.id}}/delete" onsubmit="return confirm('Excluir este projeto?')"><button class="btn danger small" type="submit">🗑 Excluir</button></form></div></div>
{% else %}<p class="muted">Nenhum site ou cliente cadastrado ainda.</p>{% endfor %}
</section></main></html>"""

SITE_FORM = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} • Bithfy</title>""" + BASE_STYLE + """
<div class="auth panel" style="max-width:720px"><div class="logo">Bith<span>fy</span></div><h2>{{title}}</h2><p class="muted">Cadastre o projeto do cliente. Você poderá editar tudo depois.</p>
<form method="post"><label>Cliente</label><input name="client_name" required maxlength="140" value="{{p.client_name if p else ''}}" placeholder="Ex.: Barbearia Central"><label>Projeto / site</label><input name="project_name" required maxlength="180" value="{{p.project_name if p else ''}}" placeholder="Ex.: Site institucional"><label>Valor do projeto (R$)</label><input name="project_value" type="number" min="0" step="0.01" required value="{{p.project_value if p else '0'}}"><label>Domínio ou link</label><input name="domain_url" maxlength="500" value="{{p.domain_url if p and p.domain_url else ''}}" placeholder="https://..."><label>Prazo de entrega</label><input name="deadline" type="date" value="{{p.deadline.isoformat() if p and p.deadline else ''}}"><label>Manutenção mensal (R$)</label><input name="monthly_maintenance" type="number" min="0" step="0.01" value="{{p.monthly_maintenance if p else '0'}}"><label>Status</label><select name="status">{% for s in statuses %}<option value="{{s}}" {% if p and p.status==s %}selected{% endif %}>{{s}}</option>{% endfor %}</select><button class="btn" style="width:100%">{{button}}</button></form><p style="text-align:center"><a href="/sites">Voltar para Sites/Clientes</a></p></div></html>"""

def _rp():
    host=urlparse(PUBLIC_BASE_URL).hostname if PUBLIC_BASE_URL else request.host.split(":")[0]
    origin=PUBLIC_BASE_URL if PUBLIC_BASE_URL else request.host_url.rstrip("/")
    return host, origin

def _b64e(data):
    return base64.urlsafe_b64encode(bytes(data)).decode().rstrip("=")

def _b64d(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({"name":"Bithfy","short_name":"Bithfy","start_url":"/","display":"standalone","background_color":"#02050a","theme_color":"#050b15"})

@app.route("/sw.js")
def service_worker():
    js = """self.addEventListener('push',e=>{let d={};try{d=e.data.json()}catch(x){d={title:'Bithfy',body:e.data?e.data.text():'Nova atualização'}};e.waitUntil(self.registration.showNotification(d.title||'Bithfy',{body:d.body||'',data:{url:d.url||'/'}}))});self.addEventListener('notificationclick',e=>{e.notification.close();e.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(ws=>{for(const w of ws){if('focus'in w){w.navigate(e.notification.data.url||'/');return w.focus()}}return clients.openWindow(e.notification.data.url||'/')}))});"""
    return Response(js,mimetype="application/javascript")

@app.route("/push/public-key")
@login_required
def push_public_key():
    return jsonify({"publicKey":VAPID_PUBLIC_KEY})

@app.route("/push/subscribe",methods=["POST"])
@login_required
def push_subscribe():
    sub=request.get_json(force=True); endpoint=sub.get("endpoint")
    if not endpoint:return jsonify({"ok":False}),400
    conn=db();cur=conn.cursor()
    cur.execute("""INSERT INTO push_subscriptions(user_id,endpoint,subscription_json) VALUES(%s,%s,%s)
                   ON CONFLICT(endpoint) DO UPDATE SET user_id=EXCLUDED.user_id,subscription_json=EXCLUDED.subscription_json""",
                (session["uid"],endpoint,json.dumps(sub)))
    conn.commit();cur.close();conn.close()
    return jsonify({"ok":True})

def send_push_to_user(uid,title,body,url="/"):
    if not (VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY): return
    conn=db();cur=conn.cursor()
    cur.execute("SELECT id,subscription_json FROM push_subscriptions WHERE user_id=%s",(uid,))
    rows=cur.fetchall()
    for sid,raw in rows:
        try:
            webpush(subscription_info=json.loads(raw),data=json.dumps({"title":title,"body":body,"url":url}),
                    vapid_private_key=VAPID_PRIVATE_KEY,vapid_claims={"sub":VAPID_SUBJECT})
        except WebPushException as e:
            if getattr(e,"response",None) is not None and e.response.status_code in (404,410):
                cur.execute("DELETE FROM push_subscriptions WHERE id=%s",(sid,))
    conn.commit();cur.close();conn.close()

@app.route("/passkey/register/options",methods=["POST"])
@login_required
def passkey_register_options():
    conn=db();cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id,name,email FROM users WHERE id=%s",(session["uid"],));u=cur.fetchone()
    cur.execute("SELECT credential_id FROM passkeys WHERE user_id=%s",(session["uid"],));existing=cur.fetchall()
    cur.close();conn.close();rp_id,_=_rp()
    opts=generate_registration_options(
        rp_id=rp_id,rp_name="Bithfy",user_id=str(u["id"]).encode(),user_name=u["email"],user_display_name=u["name"],
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_b64d(x["credential_id"])) for x in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,user_verification=UserVerificationRequirement.REQUIRED)
    )
    session["reg_challenge"]=_b64e(opts.challenge)
    return Response(options_to_json(opts),mimetype="application/json")

@app.route("/passkey/register/verify",methods=["POST"])
@login_required
def passkey_register_verify():
    try:
        rp_id,origin=_rp()
        v=verify_registration_response(credential=request.get_json(force=True),expected_challenge=_b64d(session.pop("reg_challenge")),
            expected_rp_id=rp_id,expected_origin=origin,require_user_verification=True)
        conn=db();cur=conn.cursor()
        cid=_b64e(v.credential_id)
        cur.execute("""INSERT INTO passkeys(user_id,credential_id,public_key,sign_count,device_type,backed_up)
                       VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(credential_id) DO NOTHING""",
                    (session["uid"],cid,psycopg2.Binary(v.credential_public_key),v.sign_count,str(v.credential_device_type),v.credential_backed_up))
        conn.commit();cur.close();conn.close()
        return jsonify({"ok":True})
    except Exception:
        return jsonify({"ok":False,"error":"Não foi possível validar a passkey."}),400

@app.route("/passkey/login/options",methods=["POST"])
def passkey_login_options():
    rp_id,_=_rp()
    opts=generate_authentication_options(rp_id=rp_id,user_verification=UserVerificationRequirement.REQUIRED)
    session["auth_challenge"]=_b64e(opts.challenge)
    return Response(options_to_json(opts),mimetype="application/json")

@app.route("/passkey/login/verify",methods=["POST"])
def passkey_login_verify():
    try:
        data=request.get_json(force=True); cid=data["id"]
        conn=db();cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM passkeys WHERE credential_id=%s",(cid,));p=cur.fetchone()
        if not p: cur.close();conn.close();return jsonify({"ok":False,"error":"Passkey não cadastrada."}),404
        rp_id,origin=_rp()
        v=verify_authentication_response(credential=data,expected_challenge=_b64d(session.pop("auth_challenge")),
            expected_rp_id=rp_id,expected_origin=origin,credential_public_key=bytes(p["public_key"]),
            credential_current_sign_count=p["sign_count"],require_user_verification=True)
        cur.execute("UPDATE passkeys SET sign_count=%s WHERE id=%s",(v.new_sign_count,p["id"]))
        conn.commit();cur.close();conn.close()
        session.clear();session["uid"]=p["user_id"]
        return jsonify({"ok":True})
    except Exception:
        return jsonify({"ok":False,"error":"Falha ao validar Face ID/passkey."}),400

@app.route("/health")
def health():
    return "OK", 200

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if len(password) < 6:
            flash("Use uma senha com pelo menos 6 caracteres.")
            return redirect(url_for("register"))
        conn = db()
        cur = conn.cursor()
        try:
            is_admin = bool(ADMIN_EMAIL and email == ADMIN_EMAIL)
            cur.execute("INSERT INTO users(name,email,password_hash,is_admin) VALUES(%s,%s,%s,%s) RETURNING id",
                        (name,email,generate_password_hash(password),is_admin))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO goals(user_id) VALUES(%s) ON CONFLICT DO NOTHING",(uid,))
            conn.commit()
            session["uid"] = uid
            return redirect(url_for("dashboard"))
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            flash("Esse e-mail já está cadastrado.")
        finally:
            cur.close(); conn.close()
    return render_template_string(AUTH,title="Criar conta",button="Criar minha conta",register=True,
                                  bottom='Já tem conta? <a href="/login">Entrar</a>')

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email=request.form["email"].strip().lower()
        conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM users WHERE email=%s",(email,)); u=cur.fetchone()
        cur.close(); conn.close()
        if u and check_password_hash(u["password_hash"],request.form["password"]):
            session.clear(); session["uid"]=u["id"]; return redirect(url_for("dashboard"))
        flash("E-mail ou senha inválidos.")
    return render_template_string(AUTH,title="Entrar",button="Entrar",register=False,
                                  bottom='Ainda não tem conta? <a href="/register">Criar conta</a>')

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    uid=session["uid"]; conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s",(uid,)); u=cur.fetchone()
    start_month=date.today().replace(day=1)
    cur.execute("SELECT COALESCE(SUM(amount),0) v, COUNT(*) c FROM sales WHERE user_id=%s AND created_at::date >= %s",(uid,start_month)); m=cur.fetchone()
    cur.execute("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE user_id=%s AND created_at::date=CURRENT_DATE",(uid,)); today=cur.fetchone()["v"]
    cur.execute("SELECT COALESCE(SUM(amount),0) v FROM adjustments WHERE user_id=%s",(uid,)); adj=cur.fetchone()["v"]
    cur.execute("SELECT monthly_goal FROM goals WHERE user_id=%s",(uid,)); g=cur.fetchone(); goal=float(g["monthly_goal"] if g else 10000)
    cur.execute("SELECT id,product,platform,amount,created_at FROM sales WHERE user_id=%s ORDER BY created_at DESC LIMIT 12",(uid,)); sales=cur.fetchall()
    labels=[]; values=[]
    for i in range(6,-1,-1):
        d=date.today()-timedelta(days=i); labels.append(d.strftime("%d/%m"))
        cur.execute("SELECT COALESCE(SUM(amount),0) v FROM sales WHERE user_id=%s AND created_at::date=%s",(uid,d)); values.append(float(cur.fetchone()["v"]))
    cur.close(); conn.close()
    month=float(m["v"]); adjustments=float(adj); pct=min(100,round((month/goal*100) if goal else 0,1))
    import json
    return render_template_string(DASH,name=u["name"],is_admin=u["is_admin"],today=float(today),month_sales=month,
        adjustments=adjustments,total=month+adjustments,count=m["c"],goal=goal,pct=pct,sales=sales,
        labels=json.dumps(labels),values=json.dumps(values))

@app.route("/sale/new", methods=["GET","POST"])
@login_required
def new_sale():
    if request.method=="POST":
        try: amount=float(request.form["amount"])
        except: amount=0
        if amount<=0:
            flash("Valor inválido."); return redirect(url_for("new_sale"))
        conn=db(); cur=conn.cursor()
        cur.execute("INSERT INTO sales(user_id,product,platform,amount) VALUES(%s,%s,%s,%s)",
                    (session["uid"],request.form["product"].strip(),request.form["platform"],amount))
        conn.commit(); cur.close(); conn.close()
        send_push_to_user(session["uid"],"💰 Nova venda na Bithfy",f"{request.form['product'].strip()} • R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X","."),"/")
        flash("Venda registrada com sucesso.")
        return redirect(url_for("dashboard"))
    return render_template_string(FORM)

@app.route("/goal", methods=["GET","POST"])
@login_required
def goal():
    uid=session["uid"]; conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if request.method=="POST":
        try: value=float(request.form["goal"])
        except: value=0
        if value <= 0:
            cur.close(); conn.close(); flash("Digite uma meta válida."); return redirect(url_for("goal"))
        cur.execute("""INSERT INTO goals(user_id,monthly_goal) VALUES(%s,%s)
                       ON CONFLICT(user_id) DO UPDATE SET monthly_goal=EXCLUDED.monthly_goal""",(uid,value))
        conn.commit(); cur.close(); conn.close(); flash("Meta mensal atualizada.")
        return redirect(url_for("dashboard"))
    cur.execute("SELECT monthly_goal FROM goals WHERE user_id=%s",(uid,)); row=cur.fetchone()
    cur.close(); conn.close()
    return render_template_string(GOAL_FORM,goal=float(row["monthly_goal"] if row else 10000))


SITE_STATUSES = ["Orçamento","Em produção","Aguardando pagamento","Pago","Publicado"]

def _site_form_values():
    def number(name):
        try: return max(0, float(request.form.get(name, "0") or 0))
        except: return 0
    url=request.form.get("domain_url","").strip()
    if url and not url.lower().startswith(("http://","https://")): url="https://"+url
    status=request.form.get("status","Orçamento")
    if status not in SITE_STATUSES: status="Orçamento"
    return {"client_name":request.form.get("client_name","").strip(),"project_name":request.form.get("project_name","").strip(),"project_value":number("project_value"),"domain_url":url or None,"deadline":request.form.get("deadline","").strip() or None,"monthly_maintenance":number("monthly_maintenance"),"status":status}

@app.route("/sites")
@login_required
def sites():
    uid=session["uid"]; conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM site_projects WHERE user_id=%s ORDER BY created_at DESC",(uid,)); projects=cur.fetchall()
    cur.execute("""SELECT COUNT(*) count,COALESCE(SUM(project_value),0) total_value,COALESCE(SUM(monthly_maintenance),0) maintenance,COUNT(*) FILTER (WHERE status='Pago') paid FROM site_projects WHERE user_id=%s""",(uid,)); s=cur.fetchone()
    cur.close(); conn.close()
    return render_template_string(SITES,projects=projects,count=s["count"],total_value=float(s["total_value"]),maintenance=float(s["maintenance"]),paid=s["paid"])

@app.route("/sites/new",methods=["GET","POST"])
@login_required
def site_new():
    if request.method=="POST":
        v=_site_form_values()
        if not v["client_name"] or not v["project_name"]: flash("Informe o cliente e o projeto."); return redirect(url_for("site_new"))
        conn=db(); cur=conn.cursor(); cur.execute("""INSERT INTO site_projects(user_id,client_name,project_name,project_value,domain_url,deadline,monthly_maintenance,status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(session["uid"],v["client_name"],v["project_name"],v["project_value"],v["domain_url"],v["deadline"],v["monthly_maintenance"],v["status"])); conn.commit(); cur.close(); conn.close()
        flash("Projeto cadastrado com sucesso."); return redirect(url_for("sites"))
    return render_template_string(SITE_FORM,title="Novo site / cliente",button="Salvar projeto",p=None,statuses=SITE_STATUSES)

@app.route("/sites/<int:project_id>/edit",methods=["GET","POST"])
@login_required
def site_edit(project_id):
    uid=session["uid"]; conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute("SELECT * FROM site_projects WHERE id=%s AND user_id=%s",(project_id,uid)); p=cur.fetchone()
    if not p: cur.close(); conn.close(); return "Projeto não encontrado",404
    if request.method=="POST":
        v=_site_form_values()
        if not v["client_name"] or not v["project_name"]: cur.close(); conn.close(); flash("Informe o cliente e o projeto."); return redirect(url_for("site_edit",project_id=project_id))
        old_status = p["status"]
        cur.execute("""UPDATE site_projects SET client_name=%s,project_name=%s,project_value=%s,domain_url=%s,deadline=%s,monthly_maintenance=%s,status=%s,updated_at=NOW() WHERE id=%s AND user_id=%s""",(v["client_name"],v["project_name"],v["project_value"],v["domain_url"],v["deadline"],v["monthly_maintenance"],v["status"],project_id,uid)); conn.commit(); cur.close(); conn.close()
        if old_status != "Pago" and v["status"] == "Pago":
            valor = money(v["project_value"])
            send_push_to_user(uid, "💰 Pagamento confirmado", f'{v["project_name"]} — {valor} • Cliente: {v["client_name"]}', "/sites")
        flash("Projeto atualizado."); return redirect(url_for("sites"))
    cur.close(); conn.close(); return render_template_string(SITE_FORM,title="Editar site / cliente",button="Salvar alterações",p=p,statuses=SITE_STATUSES)

@app.route("/sites/<int:project_id>/delete",methods=["POST"])
@login_required
def site_delete(project_id):
    conn=db(); cur=conn.cursor(); cur.execute("DELETE FROM site_projects WHERE id=%s AND user_id=%s RETURNING id",(project_id,session["uid"])); deleted=cur.fetchone(); conn.commit(); cur.close(); conn.close()
    flash("Projeto excluído." if deleted else "Projeto não encontrado."); return redirect(url_for("sites"))

@app.route("/integrations")
@login_required
def integrations():
    # Os conectores reais serão habilitados somente após credenciais e permissões oficiais.
    items=[
      {"name":"Mercado Livre","icon":"🟡","text":"OAuth/API oficial por usuário. Estrutura pronta para a próxima etapa.","status":"Aguardando configuração","ready":False,"url":"#"},
      {"name":"Shopee","icon":"🟠","text":"Será habilitado após acesso aprovado às ferramentas/API aplicáveis.","status":"Aguardando acesso","ready":False,"url":"#"},
      {"name":"TikTok Shop","icon":"⚫","text":"Será habilitado após concluir a verificação e obter as permissões aplicáveis.","status":"Aguardando acesso","ready":False,"url":"#"}
    ]
    return render_template_string(INTEGRATIONS,items=items)


@app.route("/admin")
@login_required
def admin():
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE id=%s",(session["uid"],)); me=cur.fetchone()
    if not me["is_admin"]:
        cur.close(); conn.close(); return "Acesso negado",403
    cur.execute("SELECT id,name,email,is_admin FROM users ORDER BY created_at DESC"); users=cur.fetchall()
    cur.close(); conn.close()
    return render_template_string(ADMIN,users=users)

@app.route("/admin/sale/<int:sale_id>/delete", methods=["POST"])
@login_required
def admin_delete_sale(sale_id):
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT is_admin FROM users WHERE id=%s",(session["uid"],)); me=cur.fetchone()
    if not me or not me["is_admin"]:
        cur.close(); conn.close(); return "Acesso negado",403
    # Admin can delete only a sale belonging to the admin's own account.
    cur.execute("DELETE FROM sales WHERE id=%s AND user_id=%s RETURNING id",(sale_id,session["uid"]))
    deleted=cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    flash("Venda apagada com sucesso." if deleted else "Venda não encontrada.")
    return redirect(url_for("dashboard"))


@app.route("/admin/adjust", methods=["POST"])
@login_required
def admin_adjust():
    conn=db(); cur=conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT is_admin FROM users WHERE id=%s",(session["uid"],)); me=cur.fetchone()
    if not me or not me["is_admin"]:
        cur.close(); conn.close(); return "Acesso negado",403
    try: amount=float(request.form["amount"])
    except: amount=0
    reason=request.form["reason"].strip()
    if amount==0 or not reason:
        flash("Informe valor e motivo."); cur.close(); conn.close(); return redirect(url_for("admin"))
    cur.execute("INSERT INTO adjustments(user_id,amount,reason,created_by) VALUES(%s,%s,%s,%s)",
                (session["uid"],amount,reason,session["uid"]))
    conn.commit(); cur.close(); conn.close()
    flash("Ajuste registrado no seu saldo.")
    return redirect(url_for("admin"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
