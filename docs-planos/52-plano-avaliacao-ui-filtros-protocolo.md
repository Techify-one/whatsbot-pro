# Plano 52 — Avaliação na tela de Protocolos: exibição + filtros

> Complemento do plano [50](50-plano-plugin-avaliacao-protocolo.md). O plano 50
> **gravou** a avaliação e a expôs na API (`protocolo.avaliacao`), mas **deixou de
> fora** a exibição e os filtros na UI (adiado explicitamente: "group-by/filtro por
> nota no Kanban"). Este plano fecha isso. Tudo na cópia VIVA
> `storages/plugins/protocolos/`.

## O que faltava (não estava no plano 50)

1. **Ver a nota + sugestão** no protocolo (só existia no payload da API).
2. **Filtrar por nota** de avaliação.
3. **Buscar pelo nº/código do protocolo** (ex.: `20260714-120754-44`) — lacuna
   pré-existente, sem relação com avaliação.

## Entregas

### Backend ([logic.py](../storages/plugins/protocolos/logic.py) + [routes.py](../storages/plugins/protocolos/routes.py))

- `list_protocolos(..., nota=None)`: filtro por nota (multi-seleção 1..5) via
  subquery `id IN (SELECT protocolo_id FROM plugin_protocolos_avaliacoes WHERE
  answered_at IS NOT NULL AND nota IN :notas)`. `notas` adicionado aos binds
  EXPANDING de `_list_clause`.
- Busca `q` estendida: além de nome/telefone, casa o **id do protocolo** — extrai o
  ÚLTIMO grupo de dígitos de `q` (o código `AAAAMMDD-HHMMSS-<id>` ou o id puro `44`)
  e adiciona `OR id = :qid`.
- Rota `GET /protocolos` aceita `nota` (escalar ou lista JSON, via `_maybe_list`).

### Frontend ([protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js))

- **Exibição**: bloco "Avaliação do cliente" (★ nota /5 + data + sugestão) no popup de
  detalhe (`at.avaliacao`); badge `★N` âmbar no card do Kanban (`row.avaliacao`).
- **Filtro por nota**: widget nativo "Avaliação" (multi-seleção 5→1) na barra;
  estado `notaFilter`; persiste/limpa/restaura junto dos outros filtros da view;
  entra em `NATIVE_ITEMS` do "Configurar filtros da aba" (chave `nota`).
- **Busca por nº**: rótulo "Buscar" + placeholder "nome, telefone ou nº do protocolo".

### Testes ([tests/test_avaliacao_protocolo.py](../tests/test_avaliacao_protocolo.py))

- `test_list_filter_by_nota_and_protocol_code`: nota=5 traz o protocolo (com
  `avaliacao` anexada), nota=1 não; busca pelo código e pelo id puro trazem. 5/5 verde.

## Observação de rollout

Views JÁ SALVAS com `available_filters` explícito (lista, não "todos") **não mostram**
o novo filtro "Avaliação" automaticamente — é preciso marcá-lo em **Configurar filtros
da aba → Nativas → Avaliação** e "Salvar configuração da aba". Views com todos os
filtros (default) já o exibem.
