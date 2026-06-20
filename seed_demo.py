"""One-off seed: popula o WhatsBot com dados de demonstração pra explorar a UI.

Idempotente — pode rodar de novo sem duplicar. NÃO faz parte do app (é um script
de conveniência). Roda com a venv: ./venv/bin/python seed_demo.py
"""
from db.engine import init_engine, get_engine
init_engine("sqlite:///storages/whatsbot.db")

import time
from sqlalchemy import text
from db.repositories import (
    agent_repo, prompt_repo, tool_repo, user_repo,
    quick_reply_repo, tag_repo, custom_attribute_repo,
)
from server.auth import hash_password_argon2

MODEL = "deepseek/deepseek-v4-pro"  # mesmo do agente default (válido no proxy Techify)
log = []

# ── 1. Sub-agentes de IA (prompt + agente) ───────────────────────────────
AGENTS = [
    ("vendas", "Agente de Vendas", False, None,
     "Você é um agente de vendas simpático e objetivo. Qualifique o lead, "
     "entenda a necessidade, apresente os planos e conduza para o fechamento. "
     "Use o frete e os valores quando perguntarem. Seja cordial e use no máximo "
     "2 emojis por mensagem.", ["calcular_frete"]),
    ("suporte", "Suporte Técnico", False, None,
     "Você é o suporte técnico. Diagnostique o problema do cliente com perguntas "
     "claras, dê o passo a passo da solução e confirme se resolveu. Se for algo "
     "que você não resolve, oriente a abrir um chamado.", []),
    ("financeiro", "Financeiro", False, None,
     "Você cuida do financeiro: segunda via de boleto, chave PIX, prazos de "
     "pagamento e negociação de débitos. Seja formal e preciso com valores e datas.",
     []),
    ("triagem", "Triagem (Roteador)", True, ["vendas", "suporte", "financeiro"],
     "Você é a triagem. Identifique a intenção do cliente e transfira para o "
     "agente certo: vendas (comprar/planos/preços), suporte (problema técnico) ou "
     "financeiro (pagamento/boleto/PIX). Faça no máximo uma pergunta antes de transferir.",
     None),
]
for key, name, is_router, targets, prompt_body, tools in AGENTS:
    try:
        prompt_repo.save(f"{key}_prompt", prompt_body)
        agent_repo.save(
            key, display_name=name, prompt_key=f"{key}_prompt",
            model_config={"model": MODEL},
            tool_names=tools, enabled=True,
            description=f"Agente de demonstração: {name}.",
            is_router=is_router, routing_targets=targets,
        )
        log.append(f"  agente: {key} ({name}){' [roteador]' if is_router else ''}")
    except Exception as e:
        log.append(f"  agente {key} FALHOU: {e}")

# ── 2. AI Tools (code-in-DB) ──────────────────────────────────────────────
TOOLS = [
    ("horario_atual", "Retorna a data e hora atuais no horário de Brasília.", '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "horario_atual",
        "description": "Retorna a data e hora atuais no horário de Brasília.",
        "parameters": {"type": "object", "properties": {}},
    },
}

def execute(ctx, args):
    from datetime import datetime, timezone, timedelta
    agora = datetime.now(timezone(timedelta(hours=-3)))
    return "Agora são " + agora.strftime("%d/%m/%Y %H:%M") + " (horário de Brasília)."
'''),
    ("calcular_frete", "Estima o frete a partir do CEP de destino e do peso (kg).", '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "calcular_frete",
        "description": "Estima o frete a partir do CEP de destino e do peso em kg.",
        "parameters": {
            "type": "object",
            "properties": {
                "cep": {"type": "string", "description": "CEP de destino"},
                "peso_kg": {"type": "number", "description": "Peso do pacote em kg"},
            },
            "required": ["cep", "peso_kg"],
        },
    },
}

def execute(ctx, args):
    cep = str(args.get("cep", "")).replace("-", "").strip()
    peso = float(args.get("peso_kg") or 0)
    preco = 12.50 + peso * 3.20
    if not (cep[:1] in ("0", "1")):
        preco += 8.00
    return "Frete estimado para o CEP %s: R$ %.2f (peso %.1f kg)." % (cep, preco, peso)
'''),
    ("validar_cep", "Valida e formata um CEP brasileiro (8 dígitos).", '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "validar_cep",
        "description": "Valida e formata um CEP brasileiro de 8 dígitos.",
        "parameters": {
            "type": "object",
            "properties": {"cep": {"type": "string", "description": "CEP informado"}},
            "required": ["cep"],
        },
    },
}

def execute(ctx, args):
    cep = str(args.get("cep", "")).replace("-", "").strip()
    if len(cep) != 8 or not cep.isdigit():
        return "CEP inválido. Informe 8 dígitos numéricos."
    return "CEP %s-%s válido." % (cep[:5], cep[5:])
'''),
]
for name, desc, code in TOOLS:
    try:
        tool_repo.save(name, description=desc, code=code.strip(),
                       dependencies=[], enabled=True)
        log.append(f"  tool: {name}")
    except Exception as e:
        log.append(f"  tool {name} FALHOU: {e}")

# ── 3. Usuários do sistema (RBAC) ─────────────────────────────────────────
USERS = [
    ("gestor@gmail.com", "Gestor Demo", "123456", ["gestor"]),
    ("atendente1@gmail.com", "Ana (Atendente)", "123456", ["atendente"]),
    ("atendente2@gmail.com", "Bruno (Atendente)", "123456", ["atendente"]),
]
for email, name, pw, roles in USERS:
    try:
        if user_repo.get_auth_row(email):
            log.append(f"  usuário: {email} (já existia, pulado)")
            continue
        user_repo.create(email=email, name=name,
                         password_hash=hash_password_argon2(pw), role_keys=roles)
        log.append(f"  usuário: {email} / {pw} [{', '.join(roles)}]")
    except Exception as e:
        log.append(f"  usuário {email} FALHOU: {e}")

# ── 4. Respostas rápidas ──────────────────────────────────────────────────
QUICK = [
    ("bomdia", "Bom dia! ☀️ Como posso te ajudar hoje?"),
    ("boatarde", "Boa tarde! Em que posso ajudar?"),
    ("aguarde", "Só um momento, por favor — já vou verificar isso para você. 🙏"),
    ("horario", "Nosso horário de atendimento é de segunda a sexta, das 9h às 18h."),
    ("obrigado", "Obrigado pelo contato! Qualquer coisa, estamos à disposição. 😊"),
    ("pix", "Nossa chave PIX é contato@empresa.com.br. Assim que cair, me avise para confirmar!"),
    ("endereco", "Estamos na Rua Exemplo, 123 — Centro. Te esperamos por aqui!"),
    ("retorno", "Vou verificar com a equipe e já te retorno, combinado?"),
]
for code, content in QUICK:
    try:
        if quick_reply_repo.exists(code):
            log.append(f"  resposta /{code} (já existia, pulado)")
            continue
        quick_reply_repo.create(short_code=code, content=content)
        log.append(f"  resposta: /{code}")
    except Exception as e:
        log.append(f"  resposta /{code} FALHOU: {e}")

# ── 5. Tags ───────────────────────────────────────────────────────────────
TAGS = [
    ("VIP", "#f59e0b"), ("Lead", "#3b82f6"), ("Cliente", "#10b981"),
    ("Urgente", "#ef4444"), ("Aguardando", "#8b5cf6"), ("Pós-venda", "#06b6d4"),
]
for name, color in TAGS:
    try:
        ok = tag_repo.create(name, color)
        log.append(f"  tag: {name} {'' if ok else '(já existia)'}")
    except Exception as e:
        log.append(f"  tag {name} FALHOU: {e}")

# ── 6. Atributos personalizados (contato + conversa) ──────────────────────
ATTRS = [
    dict(attribute_key="cpf", display_name="CPF", type="text", applies_to="contact",
         description="CPF do cliente", position=1),
    dict(attribute_key="plano", display_name="Plano", type="list", applies_to="contact",
         options=["Básico", "Pro", "Enterprise"], filterable=1, position=2),
    dict(attribute_key="data_nascimento", display_name="Data de nascimento", type="date",
         applies_to="contact", position=3),
    dict(attribute_key="newsletter", display_name="Aceita newsletter", type="checkbox",
         applies_to="contact", position=4),
    dict(attribute_key="prioridade", display_name="Prioridade", type="list",
         applies_to="conversation", options=["Baixa", "Média", "Alta"], filterable=1, position=1),
    dict(attribute_key="canal_origem", display_name="Canal de origem", type="text",
         applies_to="conversation", position=2),
]
for a in ATTRS:
    try:
        row = custom_attribute_repo.create_definition(**a)
        log.append(f"  atributo: {a['attribute_key']} ({a['applies_to']})"
                   + ("" if row else " (já existia)"))
    except Exception as e:
        log.append(f"  atributo {a['attribute_key']} FALHOU: {e}")

# ── 7. Segundo inbox (pra ver o conceito multi-inbox) ─────────────────────
try:
    with get_engine().begin() as conn:
        exists = conn.execute(text("select count(*) from inboxes where name='Vendas'")).scalar()
        if not exists:
            now = time.time()
            conn.execute(text(
                "insert into inboxes (name, channel_type, channel_id, agent_bot_enabled, created_at, updated_at) "
                "values ('Vendas', 'whatsapp', 'vendas', 1, :n, :n)"), {"n": now})
            log.append("  inbox: Vendas (2º inbox)")
        else:
            log.append("  inbox: Vendas (já existia)")
except Exception as e:
    log.append(f"  inbox Vendas FALHOU: {e}")

print("\n=== SEED CONCLUÍDO ===")
print("\n".join(log))
