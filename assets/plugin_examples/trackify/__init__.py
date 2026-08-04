"""Plugin ``trackify`` — a jornada do cliente no CDP, dentro do atendimento.

Duas direções, **um transporte só**: HTTP autenticado por API key do Trackify
(``X-API-Key``), emitida em Configurações → API Keys lá.

* **LÊ** o CDP (contatos, eventos, assinaturas, changelog) pelas rotas de leitura
  do módulo. Antes lia direto no Postgres do Nexus, por uma 2ª engine SQLAlchemy
  — a REST de leitura só aceitava cookie de sessão SSO e não servia para
  servidor-a-servidor. Com a API key isso acabou: o plugin não conhece mais
  tabela nenhuma do CDP nem precisa de credencial de banco de produção.
* **ESCREVE** por ``PUT /contacts/:id`` (campos) e ``POST /ingestion/<canal>``
  (eventos), nunca no banco: é lá que moram dedupe, merge, changelog e
  ``total_spent``. Escrever direto no banco pularia tudo isso e corromperia o CDP
  em silêncio.

A escrita por chave tem procedência própria no changelog do CDP (``source='api'``,
ator ``apikey:<id>``), e é isso que torna a supressão de eco exata — antes, a
integração assinava como um usuário humano dedicado e uma pessoa que entrasse com
aquelas credenciais tinha as edições engolidas.

Autocontido: nenhum outro plugin é importado. Sem API key configurada o plugin é
no-op — a tela diz "não configurado" e o WhatsBot segue de pé.
"""
