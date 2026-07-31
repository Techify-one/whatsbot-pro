"""Plugin ``trackify`` — a jornada do cliente no CDP, dentro do atendimento.

Duas direções, com transportes diferentes de propósito (plano 94):

* **LÊ** o CDP (contatos, eventos, assinaturas) direto no banco do Nexus, por uma
  2ª engine SQLAlchemy read-only — a REST de leitura do Trackify só aceita cookie
  de sessão SSO, então não serve para servidor-a-servidor. Mesmo precedente do
  ``vendas_ia``, e o mesmo caminho que o módulo ``nexus-campanhas`` já usa.
* **ESCREVE** por HTTP em ``POST /ingestion/<canal>``, nunca no banco: é lá que
  moram dedupe, merge, changelog e ``total_spent``. Escrever direto no banco
  pularia tudo isso e corromperia o CDP em silêncio.

Autocontido: nenhum outro plugin é importado (do ``vendas_ia`` o
``trackify_db.py`` é uma CÓPIA adaptada, não um import). Sem DSN configurado o
plugin é no-op — a tela diz "não configurado" e o WhatsBot segue de pé.
"""
