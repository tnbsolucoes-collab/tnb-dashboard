import os
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

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
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,Arial;background:radial-gradient(circle at top right,#102326 0,#07090a 38%);color:var(--text);min-height:100vh}
a{color:inherit}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{border-right:1px solid var(--line);padding:28px 20px;background:#090d0e}
.logo{font-weight:900;font-size:22px}.logo span{color:var(--green)}.muted{color:var(--muted)}nav a{display:block;margin:9px 0;padding:13px;border-radius:12px;text-decoration:none;color:#b8c2c0}nav a:hover{background:#13201e;color:var(--green)}
main{padding:30px;max-width:1400px;width:100%}.top{display:flex;justify-content:space-between;gap:15px;align-items:center}.top h1{margin:0;font-size:28px}.btn{display:inline-block;border:0;border-radius:12px;padding:12px 16px;font-weight:800;background:var(--green);color:#042116;cursor:pointer;text-decoration:none}
.btn.secondary{background:#253032;color:white}.btn.danger{background:#3a1b20;color:#ff8a96}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:25px 0}.card,.panel{background:linear-gradient(145deg,#111718,#0d1213);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 15px 40px #0005}
.card label{color:var(--muted);font-size:13px}.value{font-size:27px;font-weight:900;margin-top:8px}.up{font-size:12px;color:var(--green);margin-top:6px}.grid{display:grid;grid-template-columns:2fr 1fr;gap:16px}
input,select{width:100%;padding:12px;margin:7px 0 13px;border-radius:10px;border:1px solid #2b383a;background:#080c0d;color:white}.sale{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid #1b2425}.sale b{color:var(--green)}
.goal{height:11px;background:#1a2223;border-radius:20px;overflow:hidden}.goal i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--cyan))}
.auth{max-width:430px;margin:8vh auto;padding:28px}.auth .logo{text-align:center;margin-bottom:24px}.flash{padding:12px;border:1px solid #5c3b3f;background:#261417;border-radius:10px;margin:10px 0}.badge{font-size:11px;padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
@media(max-width:900px){.app{grid-template-columns:1fr}.side{display:none}.cards{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}main{padding:18px}}
@media(max-width:520px){.cards{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.top h1{font-size:23px}}
</style>
"""

AUTH = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} - TNB</title>""" + BASE_STYLE + """
<div class="auth panel"><div class="logo">TNB <span>Soluções</span></div><h2>{{title}}</h2>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<form method="post">
{% if register %}<label>Nome</label><input name="name" required maxlength="120">{% endif %}
<label>E-mail</label><input name="email" type="email" required>
<label>Senha</label><input name="password" type="password" required minlength="6">
<button class="btn" style="width:100%">{{button}}</button></form>
<p class="muted" style="text-align:center">{{bottom|safe}}</p></div></html>"""

DASH = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TNB Dashboard</title>""" + BASE_STYLE + """
<div class="app"><aside class="side"><div class="logo">TNB <span>Soluções</span></div><p class="muted">Central de vendas</p><nav>
<a href="/">◈ Visão geral</a><a href="/sale/new">＋ Registrar venda</a>{% if is_admin %}<a href="/admin">♛ Administração</a>{% endif %}<a href="/logout">↪ Sair</a></nav></aside>
<main><div class="top"><div><span class="badge">{% if is_admin %}ADMIN{% else %}USUÁRIO{% endif %}</span><h1>Olá, {{name}} 👋</h1></div><a class="btn" href="/sale/new">+ Registrar venda</a></div>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<section class="cards"><div class="card"><label>Faturamento hoje</label><div class="value">{{today|money}}</div><div class="up">Vendas registradas hoje</div></div>
<div class="card"><label>Vendas no mês</label><div class="value">{{month_sales|money}}</div><div class="up">Somente vendas reais registradas</div></div>
<div class="card"><label>Ajustes manuais</label><div class="value">{{adjustments|money}}</div><div class="up">Separado das vendas</div></div>
<div class="card"><label>Saldo total</label><div class="value">{{total|money}}</div><div class="up">{{count}} vendas no mês</div></div></section>
<section class="grid"><div class="panel"><h3>Últimos 7 dias</h3><canvas id="chart" height="115"></canvas></div>
<div class="panel"><h3>Meta do mês</h3><div class="value">{{month_sales|money}} / {{goal|money}}</div><p class="muted">{{pct}}% concluída</p><div class="goal"><i style="width:{{pct}}%"></i></div></div></section>
<section class="panel" style="margin-top:16px"><h3>Vendas recentes</h3>{% for s in sales %}<div class="sale"><span>{{s.product}} <small class="muted">• {{s.platform}} • {{s.created_at.strftime('%d/%m %H:%M')}}</small></span><b>+ {{s.amount|money}}</b></div>{% else %}<p class="muted">Nenhuma venda cadastrada ainda.</p>{% endfor %}</section>
</main></div><script src="https://cdn.jsdelivr.net/npm/chart.js"></script><script>
new Chart(document.getElementById('chart'),{type:'line',data:{labels:{{labels|safe}},datasets:[{data:{{values|safe}},borderColor:'#28e59b',backgroundColor:'#28e59b22',fill:true,tension:.4}]},options:{plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8e9a98'},grid:{display:false}},y:{ticks:{color:'#8e9a98'},grid:{color:'#1c2728'}}}}});
</script></html>"""

FORM = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Registrar venda</title>""" + BASE_STYLE + """
<div class="auth panel"><div class="logo">TNB <span>Soluções</span></div><h2>Registrar venda</h2>
<form method="post"><label>Produto</label><input name="product" required maxlength="180"><label>Valor (R$)</label><input name="amount" type="number" min="0.01" step="0.01" required>
<label>Plataforma</label><select name="platform"><option>Cakto</option><option>Mercado Livre</option><option>Shopee</option><option>TikTok Shop</option><option>Outra</option></select>
<button class="btn" style="width:100%">Salvar venda</button></form><p style="text-align:center"><a href="/">Voltar ao painel</a></p></div></html>"""

ADMIN = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin TNB</title>""" + BASE_STYLE + """
<main style="max-width:900px;margin:auto"><div class="top"><div><span class="badge">ADMIN</span><h1>Administração</h1></div><a class="btn secondary" href="/">Voltar</a></div>
{% with ms=get_flashed_messages() %}{% for m in ms %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}
<div class="panel" style="margin-top:20px"><h3>Adicionar ajuste ao meu saldo</h3><p class="muted">Ajustes ficam identificados e não são contados como venda.</p>
<form method="post" action="/admin/adjust"><label>Valor (R$)</label><input name="amount" type="number" step="0.01" required><label>Motivo</label><input name="reason" required maxlength="220" placeholder="Ex.: ajuste inicial">
<button class="btn">Adicionar ajuste</button></form></div>
<div class="panel" style="margin-top:16px"><h3>Usuários</h3>{% for u in users %}<div class="sale"><span>{{u.name}} <small class="muted">• {{u.email}}</small></span><span class="badge">{% if u.is_admin %}ADMIN{% else %}USUÁRIO{% endif %}</span></div>{% endfor %}</div></main></html>"""

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
    cur.execute("SELECT product,platform,amount,created_at FROM sales WHERE user_id=%s ORDER BY created_at DESC LIMIT 12",(uid,)); sales=cur.fetchall()
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
        flash("Venda registrada com sucesso.")
        return redirect(url_for("dashboard"))
    return render_template_string(FORM)

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
