// Tela do plugin `retornos` — UMA entrada no menu da engrenagem com três abas internas:
// «Configurações» (lista + editor com o construtor de regras aninhadas), «Monitor»
// (agendamentos em andamento) e «Eventos» (log do verificador, paginado no servidor).
// Preact + HTM, sem build step; cores wa-*/.wa-field (dark ok).
//
// ⚠️ HTM: NUNCA use crase nem ${...} dentro de comentário em html`...` — fecha o template
// e o módulo quebra em silêncio. Comentários explicativos ficam FORA do html.
import { h } from 'preact';
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '/static/js/services/api.js';
import { subscribe } from '/static/js/services/wsBus.js';
import { RegraBuilder } from './RegraBuilder.js';
import { avaliar, contarRegras, tiposDeMetadata } from './rules.js';
import { ACCEPT, erroDeIncompatibilidade } from './mediaKinds.js';
import { MediaPreviewModal, midiaDaMensagem } from './MediaPreview.js';

const html = htm.bind(h);

const STATUS_META = {
  active: { label: 'Em andamento', cls: 'bg-wa-teal/15 text-wa-teal' },
  completed: { label: 'Concluído', cls: 'bg-green-100 text-green-700' },
  cancelled: { label: 'Cancelado', cls: 'bg-gray-100 text-gray-600' },
  expired: { label: 'Expirado', cls: 'bg-red-100 text-red-700' },
};

async function reqJson(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `Erro ${res.status}`);
  return data;
}

function fmtDateTime(epoch) {
  if (!epoch) return '—';
  try {
    return new Date(epoch * 1000).toLocaleString('pt-BR',
      { dateStyle: 'short', timeStyle: 'short' });
  } catch { return '—'; }
}
function fmtTime(epoch) {
  if (!epoch) return '';
  try { return new Date(epoch * 1000).toLocaleTimeString('pt-BR'); } catch { return ''; }
}
function fmtRelativo(epoch) {
  if (!epoch) return '—';
  const diff = Math.round(epoch - Date.now() / 1000);
  const abs = Math.abs(diff);
  const unidade = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs / 60)}min`
    : abs < 86400 ? `${Math.round(abs / 3600)}h` : `${Math.round(abs / 86400)}d`;
  return diff >= 0 ? `em ${unidade}` : `há ${unidade}`;
}

function Badge({ children, cls = 'bg-wa-hover text-wa-secondary' }) {
  return html`<span class=${`inline-block text-[11px] font-medium px-2 py-0.5 rounded-full ${cls}`}>${children}</span>`;
}

function Field({ label, hint, children }) {
  return html`
    <label class="block">
      <span class="block text-sm font-medium text-wa-text mb-1">${label}</span>
      ${children}
      ${hint ? html`<span class="block text-[11px] text-wa-secondary mt-1">${hint}</span>` : null}
    </label>`;
}

function Check({ label, checked, onChange, hint, disabled = false }) {
  return html`
    <label class="flex items-start gap-2 text-sm text-wa-text py-1">
      <input type="checkbox" class="mt-0.5" checked=${!!checked} disabled=${!!disabled}
        onChange=${(e) => onChange(e.target.checked)} />
      <span>
        ${label}
        ${hint ? html`<span class="block text-[11px] text-wa-secondary">${hint}</span>` : null}
      </span>
    </label>`;
}

// ── Editor de uma mensagem do retorno ────────────────────────────────────────

function MensagemRow({ apiBase, metadata, msg, onSaved, onRemoved, readOnly }) {
  const [form, setForm] = useState({ ...msg });
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState(null);
  const [vendo, setVendo] = useState(false);
  const fileRef = useRef(null);
  const sujo = useMemo(() => JSON.stringify(form) !== JSON.stringify(msg), [form, msg]);
  const ehMidia = ['image', 'audio', 'video', 'document'].includes(form.tipo);
  // Do FORM, não da `msg`: assim o arquivo recém-enviado já pode ser conferido antes de
  // salvar a mensagem (é justamente quando o operador quer ver se subiu o certo).
  const midia = useMemo(() => midiaDaMensagem(form),
    [form.media_path, form.media_url, form.file_name]);
  // Mensagem JÁ gravada com anexo que não bate com o tipo (dado antigo, import de outra
  // instância): marca em vermelho aqui, em vez de deixar a bomba armada até o disparo.
  const incompativelSalvo = useMemo(
    () => erroDeIncompatibilidade(form.tipo, form.file_name || form.media_path)
      || erroDeIncompatibilidade(form.tipo, form.media_url),
    [form.tipo, form.file_name, form.media_path, form.media_url]);
  const formatosAceitos = { image: 'Só imagens.', audio: 'Só áudios.', video: 'Só vídeos.',
    document: 'Qualquer formato.' }[form.tipo] || '';

  useEffect(() => { setForm({ ...msg }); }, [msg.id, msg.updated_at]);

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      const d = await reqJson(`${apiBase}/mensagens/${msg.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tipo: form.tipo, content: form.content || '',
          media_path: form.media_path || '', media_url: form.media_url || '',
          file_name: form.file_name || '',
        }),
      });
      onSaved(d.data);
    } catch (e) { setErro(String(e.message || e)); } finally { setSalvando(false); }
  }

  async function enviarArquivo(file) {
    if (!file) return;
    // Barra ANTES do upload: o `accept` do input é só uma sugestão do navegador (o usuário
    // pode trocar para "Todos os arquivos" na caixa de diálogo, ou arrastar). A rota faz a
    // mesma checagem — esta aqui só evita a subida inútil e responde na hora.
    const incompativel = erroDeIncompatibilidade(form.tipo, file.name, file.type);
    if (incompativel) { setErro(incompativel); return; }
    setSalvando(true); setErro(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('tipo', form.tipo || '');
      const res = await fetch(`${apiBase}/upload`, {
        method: 'POST', headers: authHeaders(), body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d.ok === false) throw new Error(d.error || `Erro ${res.status}`);
      setForm((f) => ({ ...f, media_path: d.data.media_path, file_name: d.data.file_name,
        media_url: '' }));
    } catch (e) { setErro(String(e.message || e)); } finally { setSalvando(false); }
  }

  // Tira o anexo da mensagem (upload ou URL). Igual ao upload, mexe só no FORM — a remoção
  // vale para valer no "Salvar" (o selo "não salva" avisa), então trocar de arquivo por
  // engano não estraga a mensagem já gravada. O arquivo em `statics/outbox/` fica onde está:
  // outra mensagem pode apontar para ele, e o mesmo já valia para o `trocarTipo`.
  function removerMidia() {
    setVendo(false);
    setErro(null);
    setForm((f) => ({ ...f, media_path: '', file_name: '', media_url: '' }));
  }

  // Trocar o tipo com um anexo já escolhido é o caminho mais curto para um `.mp4` numa
  // mensagem de "Imagem" (o `accept` do seletor não protege contra isso). O anexo que não
  // serve para o tipo novo é REMOVIDO na hora, com aviso — deixá-lo ali só adiaria o erro
  // para o "Salvar" (que a rota recusa) ou, pior, para o disparo.
  function trocarTipo(novoTipo) {
    const conflito = erroDeIncompatibilidade(novoTipo, form.file_name || form.media_path)
      || erroDeIncompatibilidade(novoTipo, form.media_url);
    if (!conflito) {
      setErro(null);
      setForm({ ...form, tipo: novoTipo });
      return;
    }
    setForm({ ...form, tipo: novoTipo, media_path: '', file_name: '', media_url: '' });
    setErro(`${conflito} O arquivo anterior foi removido desta mensagem.`);
  }

  const placeholder = form.tipo === 'ia_responde_agora'
    ? 'Instrução para a IA (ex.: "Retome o contato com o cliente, pergunte se ainda tem interesse no orçamento e ofereça ajuda.")'
    : form.tipo === 'private_note'
      ? 'Texto da nota privada (só o atendente vê)'
      : 'Texto enviado ao cliente';

  return html`
    <div class="rounded-lg border border-wa-border bg-wa-panel p-3 space-y-2">
      <div class="flex flex-wrap items-center gap-2">
        <select class="wa-field rounded px-2 py-1 text-sm" value=${form.tipo}
          disabled=${readOnly}
          onChange=${(e) => trocarTipo(e.target.value)}>
          ${(metadata.tipos_mensagem || []).map((t) => html`
            <option value=${t.value}>${t.label}</option>`)}
        </select>
        ${incompativelSalvo ? html`
          <${Badge} cls="bg-red-100 text-red-700">arquivo incompatível</${Badge}>` : null}
        <div class="ml-auto flex items-center gap-2">
          ${sujo ? html`<${Badge} cls="bg-amber-100 text-amber-700">não salva</${Badge}>` : null}
          <button type="button" disabled=${readOnly || salvando || !sujo} onClick=${salvar}
            class="text-xs px-2 py-1 rounded bg-wa-teal text-white disabled:opacity-40">
            ${salvando ? 'Salvando…' : 'Salvar'}
          </button>
          <button type="button" disabled=${readOnly} onClick=${onRemoved}
            class="text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40">
            Excluir
          </button>
        </div>
      </div>

      <textarea rows="3" class="wa-field w-full rounded px-2 py-1 text-sm resize-y"
        placeholder=${placeholder} disabled=${readOnly} value=${form.content || ''}
        onInput=${(e) => setForm({ ...form, content: e.target.value })}></textarea>

      ${ehMidia ? html`
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <button type="button" disabled=${readOnly} onClick=${() => fileRef.current && fileRef.current.click()}
            class="px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
            Escolher arquivo
          </button>
          <input ref=${fileRef} type="file" class="hidden" accept=${ACCEPT[form.tipo] || ''}
            onChange=${(e) => {
              const escolhido = e.target.files && e.target.files[0];
              e.target.value = '';
              enviarArquivo(escolhido);
            }} />
          ${form.media_path
            ? html`<button type="button" onClick=${() => setVendo(true)}
                title="Ver o arquivo"
                class="text-wa-teal underline decoration-dotted truncate max-w-[18rem]">
                ${form.file_name || form.media_path}</button>`
            : html`<span class="text-wa-secondary">nenhum arquivo</span>`}
          <span class="text-wa-secondary">ou URL:</span>
          <input class="wa-field rounded px-2 py-1 min-w-[12rem]" placeholder="https://…"
            disabled=${readOnly} value=${form.media_url || ''}
            onInput=${(e) => setForm({ ...form, media_url: e.target.value })} />
          ${midia ? html`
            <button type="button" onClick=${() => setVendo(true)}
              class="px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
              Ver arquivo
            </button>
            <button type="button" disabled=${readOnly} onClick=${removerMidia}
              title="Remover o anexo desta mensagem"
              class="px-2 py-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40">
              Remover mídia
            </button>` : null}
          <span class="text-wa-secondary">${formatosAceitos}</span>
        </div>` : null}

      ${vendo && midia ? html`
        <${MediaPreviewModal} msg=${form} onClose=${() => setVendo(false)} />` : null}

      ${incompativelSalvo && !erro ? html`
        <div class="text-xs text-red-600">${incompativelSalvo}</div>` : null}

      ${form.tipo === 'ia_responde_agora' ? html`
        <p class="text-[11px] text-wa-secondary">
          A IA gera a resposta com o agente da conversa e ENVIA ao cliente. Se a IA estiver
          desligada na conversa, houver atendente humano ou a conversa estiver transferida,
          o retorno vira uma nota privada de aviso.
        </p>` : null}
      ${erro ? html`<div class="text-xs text-red-600">${erro}</div>` : null}
    </div>`;
}

// ── Editor de um retorno ─────────────────────────────────────────────────────

function RetornoCard({ apiBase, metadata, retorno, indice, total, previewCtx, readOnly,
                     onChanged, onRemoved, onMove }) {
  const [aberto, setAberto] = useState(false);
  const [form, setForm] = useState({ ...retorno });
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState(null);

  useEffect(() => { setForm({ ...retorno }); }, [retorno.id, retorno.updated_at]);

  const sujo = useMemo(() => (
    form.nome !== retorno.nome
    || JSON.stringify(form.filtros) !== JSON.stringify(retorno.filtros)
  ), [form, retorno]);

  const resumo = contarRegras(form.filtros || {});
  // Os tipos saem do /metadata (e não só do espelho estático), senão um campo vindo de
  // outro plugin seria "desconhecido" aqui e o preview diria "não passaria" sempre.
  const tipos = useMemo(() => tiposDeMetadata(metadata), [metadata]);
  const preview = previewCtx ? avaliar(form.filtros || {}, previewCtx, tipos) : null;

  async function salvar() {
    setSalvando(true); setErro(null);
    try {
      const d = await reqJson(`${apiBase}/retornos/${retorno.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          nome: form.nome || '',
          filtros: form.filtros || { regras: [] },
        }),
      });
      onChanged({ ...d.data, mensagens: retorno.mensagens || [] });
    } catch (e) { setErro(String(e.message || e)); } finally { setSalvando(false); }
  }

  // A pausa entre as mensagens vive no bloco "Mensagens deste retorno", longe do botão
  // "Salvar retorno" — então salva sozinha (no blur/Enter do campo), igual ao A/B, e por
  // isso fica FORA do `sujo`. Vazio = herda a pausa global do plugin (coluna NULL).
  async function salvarPausa(bruto) {
    const texto = String(bruto ?? '').trim();
    const valor = texto === '' ? null : Math.max(0, Math.min(pausaMax, Number(texto) || 0));
    if (Number(retorno.delay_mensagens_seg ?? null) === Number(valor)
        && (retorno.delay_mensagens_seg == null) === (valor == null)) {
      setForm((f) => ({ ...f, delay_mensagens_seg: valor }));
      return;
    }
    const anterior = retorno.delay_mensagens_seg ?? null;
    setForm((f) => ({ ...f, delay_mensagens_seg: valor }));
    setErro(null);
    try {
      await reqJson(`${apiBase}/retornos/${retorno.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delay_mensagens_seg: valor }),
      });
      onChanged({ ...retorno, delay_mensagens_seg: valor, mensagens: retorno.mensagens || [] });
    } catch (e) {
      setForm((f) => ({ ...f, delay_mensagens_seg: anterior }));
      setErro(String(e.message || e));
    }
  }

  // O A/B é do retorno inteiro: salva na hora (o botão "Salvar retorno" fica longe daqui) e
  // NÃO propaga `updated_at`, para não descartar edições em andamento no formulário.
  async function alternarAb(valor) {
    const anterior = !!form.ab_ativo;
    setForm((f) => ({ ...f, ab_ativo: valor }));
    setErro(null);
    try {
      await reqJson(`${apiBase}/retornos/${retorno.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ab_ativo: valor }),
      });
      onChanged({ ...retorno, ab_ativo: valor, mensagens: retorno.mensagens || [] });
    } catch (e) {
      setForm((f) => ({ ...f, ab_ativo: anterior }));
      setErro(String(e.message || e));
    }
  }

  async function addMensagem() {
    try {
      const d = await reqJson(`${apiBase}/retornos/${retorno.id}/mensagens`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tipo: 'private_note', content: '' }),
      });
      onChanged({ ...retorno, mensagens: [...(retorno.mensagens || []), d.data] });
      setAberto(true);
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function removerMensagem(id) {
    try {
      await reqJson(`${apiBase}/mensagens/${id}`, { method: 'DELETE' });
      const restantes = (retorno.mensagens || []).filter((m) => m.id !== id);
      // Sobrou menos de 2 mensagens: não há o que alternar — desliga o A/B em vez de deixar
      // o retorno marcado com um teste que não acontece.
      if (form.ab_ativo && restantes.length < 2) {
        setForm((f) => ({ ...f, ab_ativo: false }));
        await reqJson(`${apiBase}/retornos/${retorno.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ab_ativo: false }),
        });
        onChanged({ ...retorno, ab_ativo: false, mensagens: restantes });
        return;
      }
      onChanged({ ...retorno, mensagens: restantes });
    } catch (e) { setErro(String(e.message || e)); }
  }

  function mensagemSalva(nova) {
    onChanged({
      ...retorno,
      mensagens: (retorno.mensagens || []).map((m) => (m.id === nova.id ? nova : m)),
    });
  }

  const mensagens = retorno.mensagens || [];
  // Alternar exige ao menos duas mensagens — com uma só, não há teste A/B.
  const abDisponivel = mensagens.length >= 2;
  const pausaPadrao = Number(metadata?.pausa_mensagens_padrao ?? 2);
  const pausaMax = Number(metadata?.pausa_mensagens_max ?? 300);
  // A pausa só tem efeito quando MAIS DE UMA mensagem sai no mesmo disparo — com A/B ligado
  // sai só uma, então o campo não teria o que espaçar.
  const pausaUtil = mensagens.length >= 2 && !form.ab_ativo;

  return html`
    <div class="rounded-xl border border-wa-border bg-wa-bg">
      <div class="flex flex-wrap items-center gap-2 p-3">
        <button type="button" onClick=${() => setAberto(!aberto)}
          class="text-sm font-semibold text-wa-text flex items-center gap-2 min-w-0">
          <span class="text-wa-secondary">${aberto ? '▾' : '▸'}</span>
          <span class="shrink-0 w-6 h-6 rounded-full bg-wa-teal text-white text-xs flex items-center justify-center">
            ${indice + 1}
          </span>
          <span class="truncate">${form.nome || `Retorno ${indice + 1}`}</span>
        </button>
        <${Badge}>${mensagens.length} mensagem(ns)</${Badge}>
        ${form.ab_ativo ? html`<${Badge} cls="bg-purple-100 text-purple-700">A/B</${Badge}>` : null}
        ${form.delay_mensagens_seg != null && form.delay_mensagens_seg !== ''
          ? html`<${Badge}>pausa ${Number(form.delay_mensagens_seg) || 0}s</${Badge}>` : null}
        <${Badge}>${resumo.condicoes} condição(ões)</${Badge}>
        ${preview !== null ? html`
          <${Badge} cls=${preview ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}>
            ${preview ? 'passaria' : 'não passaria'}
          </${Badge}>` : null}
        <div class="ml-auto flex items-center gap-1">
          <button type="button" title="Mover para cima" disabled=${readOnly || indice === 0}
            onClick=${() => onMove(indice, -1)}
            class="px-1.5 py-0.5 text-wa-secondary hover:text-wa-text disabled:opacity-30">↑</button>
          <button type="button" title="Mover para baixo" disabled=${readOnly || indice === total - 1}
            onClick=${() => onMove(indice, 1)}
            class="px-1.5 py-0.5 text-wa-secondary hover:text-wa-text disabled:opacity-30">↓</button>
          <button type="button" disabled=${readOnly} onClick=${onRemoved}
            class="text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40">
            Excluir retorno
          </button>
        </div>
      </div>

      ${aberto ? html`
        <div class="px-3 pb-3 space-y-3">
          <div class="grid gap-3 sm:grid-cols-2">
            <${Field} label="Nome do retorno"
              hint="Só para você se achar na sequência — o cliente não vê.">
              <input class="wa-field w-full rounded px-2 py-1 text-sm" disabled=${readOnly}
                value=${form.nome || ''} onInput=${(e) => setForm({ ...form, nome: e.target.value })} />
            </${Field}>
          </div>

          <${RegraBuilder} metadata=${metadata} arvore=${form.filtros || { regras: [] }}
            titulo="Condições para este retorno disparar"
            ajuda="Se as condições NÃO baterem, o retorno é reagendado (e nunca pulado). Sem condição, dispara no ciclo seguinte."
            preview=${preview}
            onChange=${(arv) => setForm({ ...form, filtros: arv })} />

          <div class="flex items-center gap-2">
            ${sujo ? html`<${Badge} cls="bg-amber-100 text-amber-700">alterações não salvas</${Badge}>` : null}
            <button type="button" disabled=${readOnly || salvando || !sujo} onClick=${salvar}
              class="text-xs px-3 py-1.5 rounded bg-wa-teal text-white disabled:opacity-40">
              ${salvando ? 'Salvando…' : 'Salvar retorno'}
            </button>
          </div>

          <div class="space-y-2">
            <div class="flex flex-wrap items-center gap-2">
              <h4 class="text-sm font-semibold text-wa-text">Mensagens deste retorno</h4>
              <label class=${`flex items-center gap-1 text-xs ${abDisponivel ? 'text-wa-secondary' : 'text-wa-secondary opacity-50 cursor-not-allowed'}`}
                title=${abDisponivel ? '' : 'Adicione ao menos 2 mensagens para alternar entre elas.'}>
                <input type="checkbox" checked=${!!form.ab_ativo}
                  disabled=${readOnly || !abDisponivel}
                  onChange=${(e) => alternarAb(e.target.checked)} />
                Teste A/B (envia uma mensagem por disparo, alternando entre todas)
              </label>
              <button type="button" disabled=${readOnly} onClick=${addMensagem}
                class="ml-auto text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
                + Mensagem
              </button>
            </div>
            <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p class="text-[11px] text-wa-secondary flex-1 min-w-[14rem]">
                ${!abDisponivel
                  ? 'Todas as mensagens abaixo são enviadas no disparo, na ordem. O teste A/B só fica disponível com 2 ou mais mensagens.'
                  : form.ab_ativo
                    ? 'Com o teste A/B ligado, cada disparo envia só UMA das mensagens abaixo, alternando na ordem a cada conversa.'
                    : 'Todas as mensagens abaixo são enviadas no disparo, na ordem.'}
              </p>
              <label class="flex items-center gap-1.5 text-[11px] text-wa-secondary shrink-0"
                title=${pausaUtil
                  ? `Pausa aplicada entre as mensagens deste retorno. Vazio = padrão global (${pausaPadrao}s). Máximo ${pausaMax}s.`
                  : 'Só tem efeito quando mais de uma mensagem sai no mesmo disparo.'}>
                <span>Pausa entre as mensagens</span>
                <input type="number" min="0" max=${pausaMax} step="1"
                  class="wa-field rounded px-2 py-1 w-20 text-xs text-right"
                  disabled=${readOnly}
                  placeholder=${`${pausaPadrao}`}
                  value=${form.delay_mensagens_seg ?? ''}
                  onInput=${(e) => setForm({ ...form, delay_mensagens_seg: e.target.value })}
                  onChange=${(e) => salvarPausa(e.target.value)}
                  onBlur=${(e) => salvarPausa(e.target.value)} />
                <span>s</span>
              </label>
            </div>
            <p class="text-[11px] text-wa-secondary -mt-1">
              ${form.delay_mensagens_seg == null || form.delay_mensagens_seg === ''
                ? `Sem valor, este retorno usa a pausa global do plugin (${pausaPadrao}s).`
                : `Este retorno espera ${Number(form.delay_mensagens_seg) || 0}s entre uma mensagem e a seguinte.`}
              ${pausaUtil ? '' : ' (só vale com 2 ou mais mensagens saindo no mesmo disparo)'}
            </p>
            ${mensagens.length === 0
              ? html`<p class="text-xs text-wa-secondary">
                  Nenhuma mensagem — o retorno só serve de porteiro (avalia e segue para o próximo).
                </p>`
              : mensagens.map((m) => html`
                <${MensagemRow} key=${m.id} apiBase=${apiBase} metadata=${metadata} msg=${m}
                  readOnly=${readOnly} onSaved=${mensagemSalva}
                  onRemoved=${() => removerMensagem(m.id)} />`)}
          </div>
          ${erro ? html`<div class="text-xs text-red-600">${erro}</div>` : null}
        </div>` : null}
    </div>`;
}

// ── Editor da configuração ────────────────────────────────────────────────────────

function ConfiguracaoEditor({ apiBase, metadata, configuracaoId, onVoltar, readOnly }) {
  const [configuracao, setConfiguracao] = useState(null);
  const [form, setForm] = useState(null);
  const [erro, setErro] = useState(null);
  const [msg, setMsg] = useState(null);
  const [salvando, setSalvando] = useState(false);
  const [convId, setConvId] = useState('');
  const [previewCtx, setPreviewCtx] = useState(null);
  const [previewInfo, setPreviewInfo] = useState(null);
  // Import destrutivo: o arquivo fica em espera até o operador confirmar a substituição.
  const [aImportar, setAImportar] = useState(null);
  const [importando, setImportando] = useState(false);
  const importRef = useRef(null);

  const carregar = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/configuracoes/${configuracaoId}`);
      setConfiguracao(d.data);
      setForm({ ...d.data });
      setErro(null);
    } catch (e) { setErro(String(e.message || e)); }
  }, [apiBase, configuracaoId]);

  useEffect(() => { carregar(); }, [carregar]);

  const sujo = useMemo(() => {
    if (!configuracao || !form) return false;
    const campos = ['nome', 'descricao', 'ativo', 'on_reply', 'cancel_on_resolve',
      'cancel_on_assign_human', 'cancel_on_ai_off', 'apply_to_groups', 'tz_offset_hours'];
    return campos.some((k) => String(form[k]) !== String(configuracao[k]));
  }, [form, configuracao]);

  async function salvar() {
    setSalvando(true); setErro(null); setMsg(null);
    try {
      const body = {
        nome: form.nome, descricao: form.descricao || '', ativo: !!form.ativo,
        on_reply: form.on_reply, cancel_on_resolve: !!form.cancel_on_resolve,
        cancel_on_assign_human: !!form.cancel_on_assign_human,
        cancel_on_ai_off: !!form.cancel_on_ai_off, apply_to_groups: !!form.apply_to_groups,
        tz_offset_hours: Number(form.tz_offset_hours),
      };
      await reqJson(`${apiBase}/configuracoes/${configuracaoId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setMsg('Configuração salva.');
      carregar();
    } catch (e) { setErro(String(e.message || e)); } finally { setSalvando(false); }
  }

  async function addRetorno() {
    try {
      await reqJson(`${apiBase}/configuracoes/${configuracaoId}/retornos`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nome: `Retorno ${(configuracao.retornos || []).length + 1}` }),
      });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function removerRetorno(id) {
    try {
      await reqJson(`${apiBase}/retornos/${id}`, { method: 'DELETE' });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function moverRetorno(indice, dir) {
    const retornos = [...(configuracao.retornos || [])];
    const alvo = indice + dir;
    if (alvo < 0 || alvo >= retornos.length) return;
    const [item] = retornos.splice(indice, 1);
    retornos.splice(alvo, 0, item);
    setConfiguracao({ ...configuracao, retornos });
    try {
      await reqJson(`${apiBase}/configuracoes/${configuracaoId}/retornos/reorder`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: retornos.map((p) => p.id) }),
      });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  function retornoAtualizado(novo) {
    setConfiguracao((r) => ({
      ...r, retornos: (r.retornos || []).map((p) => (p.id === novo.id ? novo : p)),
    }));
  }

  async function testarEmConversa() {
    setPreviewCtx(null); setPreviewInfo(null); setErro(null);
    const id = Number(convId);
    if (!id) { setErro('Informe o número (ID) da conversa para testar.'); return; }
    try {
      const d = await reqJson(`${apiBase}/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: id,
          tz_offset_hours: Number(form.tz_offset_hours) }),
      });
      setPreviewCtx(d.data.contexto);
      setPreviewInfo(d.data.conversa);
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function exportar() {
    try {
      const d = await reqJson(`${apiBase}/configuracoes/${configuracaoId}/export`);
      const blob = new Blob([JSON.stringify(d.data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `configuracao-${(configuracao.nome || 'retornos').replace(/[^\w-]+/g, '_')}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function arquivoEscolhido(file) {
    if (!file) return;
    setErro(null); setMsg(null);
    try {
      const json = JSON.parse(await file.text());
      if (!json || typeof json !== 'object' || Array.isArray(json)) {
        throw new Error('O arquivo não é uma configuração exportada.');
      }
      const payload = json.configuracao && typeof json.configuracao === 'object'
        ? json.configuracao : json;
      setAImportar({
        nome: file.name,
        payload,
        nRetornos: Array.isArray(payload.retornos) ? payload.retornos.length : 0,
      });
    } catch (e) {
      setErro(`Não deu para ler o JSON: ${e.message || e}`);
    }
  }

  async function confirmarImport() {
    if (!aImportar) return;
    setImportando(true); setErro(null); setMsg(null);
    try {
      await reqJson(`${apiBase}/configuracoes/${configuracaoId}/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ configuracao: aImportar.payload }),
      });
      setAImportar(null);
      setMsg('Configuração substituída pelo JSON importado.');
      await carregar();
    } catch (e) {
      setErro(String(e.message || e));
    } finally { setImportando(false); }
  }

  if (erro && !configuracao) {
    return html`<div class="p-4 text-red-600 text-sm">Erro: ${erro}</div>`;
  }
  if (!configuracao || !form) {
    return html`<div class="p-4 text-wa-secondary text-sm">Carregando configuração…</div>`;
  }

  return html`
    <div class="space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" onClick=${onVoltar}
          class="text-sm text-wa-teal hover:opacity-80">← Todas as configurações</button>
        <span class="text-wa-secondary">/</span>
        <h2 class="text-lg font-semibold text-wa-text truncate">${configuracao.nome}</h2>
        <${Badge} cls=${form.ativo ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}>
          ${form.ativo ? 'Ativa' : 'Inativa'}
        </${Badge}>
        <div class="ml-auto flex items-center gap-2">
          <button type="button" disabled=${readOnly}
            title="Substitui esta configuração pelo conteúdo de um JSON exportado."
            onClick=${() => importRef.current && importRef.current.click()}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
            Importar JSON
          </button>
          <input ref=${importRef} type="file" accept="application/json,.json" class="hidden"
            onChange=${(e) => {
              const file = e.target.files && e.target.files[0];
              e.target.value = '';  // escolher o MESMO arquivo de novo tem de disparar outra vez
              arquivoEscolhido(file);
            }} />
          <button type="button" onClick=${exportar}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
            Exportar JSON
          </button>
          ${sujo ? html`<${Badge} cls="bg-amber-100 text-amber-700">alterações não salvas</${Badge}>` : null}
          <button type="button" disabled=${readOnly || salvando || !sujo} onClick=${salvar}
            class="text-sm px-3 py-1.5 rounded bg-wa-teal text-white disabled:opacity-40">
            ${salvando ? 'Salvando…' : 'Salvar configuração'}
          </button>
        </div>
      </div>
      ${msg ? html`<div class="text-sm text-green-600">${msg}</div>` : null}
      ${erro ? html`<div class="text-sm text-red-600">${erro}</div>` : null}

      <section class="rounded-xl border border-wa-border bg-wa-panel p-4 grid gap-4 lg:grid-cols-2">
        <div class="space-y-3">
          <${Field} label="Nome da configuração">
            <input class="wa-field w-full rounded px-3 py-2 text-sm" disabled=${readOnly}
              value=${form.nome || ''} onInput=${(e) => setForm({ ...form, nome: e.target.value })} />
          </${Field}>
          <${Field} label="Descrição (opcional)">
            <textarea rows="2" class="wa-field w-full rounded px-3 py-2 text-sm resize-y"
              disabled=${readOnly} value=${form.descricao || ''}
              onInput=${(e) => setForm({ ...form, descricao: e.target.value })}></textarea>
          </${Field}>
          <${Check} label="Configuração ativa" checked=${form.ativo} disabled=${readOnly}
            hint="Só configurações ativas recebem conversas novas."
            onChange=${(v) => setForm({ ...form, ativo: v })} />
          <${Check} label="Aplicar também a grupos" checked=${form.apply_to_groups}
            disabled=${readOnly} onChange=${(v) => setForm({ ...form, apply_to_groups: v })} />
        </div>

        <div class="space-y-3">
          <${Field} label="Quando o cliente responder"
            hint="O comportamento do plugin quando chega uma mensagem nova do cliente.">
            <select class="wa-field w-full rounded px-3 py-2 text-sm" value=${form.on_reply}
              disabled=${readOnly}
              onChange=${(e) => setForm({ ...form, on_reply: e.target.value })}>
              <option value="reset">Reiniciar a configuração do primeiro retorno</option>
              <option value="cancel">Encerrar a configuração (não cobra mais)</option>
            </select>
          </${Field}>
          <${Check} label="Cancelar quando o atendimento for resolvido/arquivado"
            checked=${form.cancel_on_resolve} disabled=${readOnly}
            onChange=${(v) => setForm({ ...form, cancel_on_resolve: v })} />
          <${Check} label="Cancelar quando um atendente assumir a conversa"
            checked=${form.cancel_on_assign_human} disabled=${readOnly}
            onChange=${(v) => setForm({ ...form, cancel_on_assign_human: v })} />
          <${Check} label="Cancelar quando a IA for desligada na conversa"
            checked=${form.cancel_on_ai_off} disabled=${readOnly}
            onChange=${(v) => setForm({ ...form, cancel_on_ai_off: v })} />
        </div>

        <div class="space-y-3">
          <${Field} label="Fuso horário (horas em relação ao UTC)"
            hint="Offset fixo — Brasil = −3. Usado por todas as regras de hora/data desta configuração.">
            <input type="number" step="0.5" class="wa-field w-full rounded px-3 py-2 text-sm"
              disabled=${readOnly} value=${form.tz_offset_hours}
              onInput=${(e) => setForm({ ...form, tz_offset_hours: e.target.value })} />
          </${Field}>
        </div>

        <div class="space-y-2">
          <${Field} label="Testar com uma conversa real"
            hint="Cole o número (ID) do atendimento. As condições passam a mostrar “passaria / não passaria” com os dados dessa conversa AGORA.">
            <div class="flex items-center gap-2">
              <input class="wa-field rounded px-2 py-1 text-sm w-[8rem]" placeholder="ID"
                value=${convId} onInput=${(e) => setConvId(e.target.value)} />
              <button type="button" onClick=${testarEmConversa}
                class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
                Testar
              </button>
              ${previewCtx ? html`
                <button type="button" onClick=${() => { setPreviewCtx(null); setPreviewInfo(null); }}
                  class="text-xs px-2 py-1 rounded text-wa-secondary hover:text-wa-text">limpar</button>` : null}
            </div>
          </${Field}>
          ${previewInfo ? html`
            <p class="text-[11px] text-wa-secondary">
              Testando com <strong>${previewInfo.contato}</strong> (atendimento #${previewInfo.display_id || previewInfo.id}, canal ${previewInfo.canal}).
            </p>` : null}
        </div>
      </section>

      <section class="space-y-2">
        <div class="flex items-center gap-2">
          <h3 class="text-base font-semibold text-wa-text">Sequência de retornos</h3>
          <span class="text-[11px] text-wa-secondary">
            Os retornos rodam em ordem: cada um avalia as próprias condições e dispara as mensagens.
            É aqui que ficam os filtros de quem recebe o follow-up — e de quando.
          </span>
          <button type="button" disabled=${readOnly} onClick=${addRetorno}
            class="ml-auto text-xs px-2 py-1 rounded bg-wa-teal text-white disabled:opacity-40">
            + Retorno
          </button>
        </div>
        ${(configuracao.retornos || []).length === 0
          ? html`<p class="text-sm text-wa-secondary">
              Nenhum retorno ainda. Adicione o primeiro retorno e as mensagens dele.
            </p>`
          : (configuracao.retornos || []).map((p, i) => html`
            <${RetornoCard} key=${p.id} apiBase=${apiBase} metadata=${metadata} retorno=${p}
              indice=${i} total=${(configuracao.retornos || []).length} previewCtx=${previewCtx}
              readOnly=${readOnly} onChanged=${retornoAtualizado}
              onRemoved=${() => removerRetorno(p.id)} onMove=${moverRetorno} />`)}
      </section>

      ${aImportar ? html`
        <div class="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
          onClick=${() => (importando ? null : setAImportar(null))}>
          <div class="bg-wa-panel text-wa-text rounded-xl border border-wa-border shadow-lg max-w-md w-full p-5"
            onClick=${(e) => e.stopPropagation()}>
            <h4 class="font-semibold mb-2">Substituir esta configuração</h4>
            <p class="text-sm text-wa-secondary mb-3">
              O arquivo <strong>${aImportar.nome}</strong> vai substituir a configuração
              “${configuracao.nome}”: os ${(configuracao.retornos || []).length} retorno(s)
              atuais e as mensagens deles são apagados e recriados a partir do JSON
              (${aImportar.nRetornos} retorno(s)). Esta ação não pode ser desfeita.
            </p>
            <p class="text-[11px] text-wa-secondary mb-4">
              O liga/desliga (“Configuração ativa”) e a posição dela na lista não mudam —
              continuam como estão aqui.
            </p>
            <div class="flex justify-end gap-2">
              <button type="button" disabled=${importando} onClick=${() => setAImportar(null)}
                class="px-3 py-1.5 rounded bg-wa-hover text-wa-text text-sm disabled:opacity-40">
                Cancelar
              </button>
              <button type="button" disabled=${importando} onClick=${confirmarImport}
                class="px-3 py-1.5 rounded bg-red-600 text-white text-sm disabled:opacity-40">
                ${importando ? 'Substituindo…' : 'Substituir'}
              </button>
            </div>
          </div>
        </div>` : null}
    </div>`;
}

// ── Lista de configurações ────────────────────────────────────────────────────────

function ConfiguracoesTab({ apiBase, metadata, can }) {
  const [configuracoes, setConfiguracoes] = useState([]);
  const [erro, setErro] = useState(null);
  const [selecionada, setSelecionada] = useState(null);
  const [confirmar, setConfirmar] = useState(null);
  const importRef = useRef(null);
  const podeEditar = !can || can('edit');

  const carregar = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/configuracoes`);
      setConfiguracoes(d.data || []);
      setErro(null);
    } catch (e) { setErro(String(e.message || e)); }
  }, [apiBase]);

  useEffect(() => { carregar(); }, [carregar]);

  async function nova() {
    try {
      const d = await reqJson(`${apiBase}/configuracoes`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nome: 'Nova configuração' }),
      });
      await carregar();
      setSelecionada(d.data.id);
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function alternarAtivo(configuracao) {
    try {
      await reqJson(`${apiBase}/configuracoes/${configuracao.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ativo: !configuracao.ativo }),
      });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function duplicar(id) {
    try {
      await reqJson(`${apiBase}/configuracoes/${id}/duplicate`, { method: 'POST' });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function excluir(id) {
    try {
      await reqJson(`${apiBase}/configuracoes/${id}`, { method: 'DELETE' });
      setConfirmar(null);
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function mover(indice, dir) {
    const lista = [...configuracoes];
    const alvo = indice + dir;
    if (alvo < 0 || alvo >= lista.length) return;
    const [item] = lista.splice(indice, 1);
    lista.splice(alvo, 0, item);
    setConfiguracoes(lista);
    try {
      await reqJson(`${apiBase}/configuracoes/reorder`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: lista.map((r) => r.id) }),
      });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  async function importar(file) {
    if (!file) return;
    try {
      const texto = await file.text();
      const json = JSON.parse(texto);
      await reqJson(`${apiBase}/configuracoes/import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ configuracao: json }),
      });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  if (selecionada) {
    return html`<${ConfiguracaoEditor} apiBase=${apiBase} metadata=${metadata} configuracaoId=${selecionada}
      readOnly=${!podeEditar}
      onVoltar=${() => { setSelecionada(null); carregar(); }} />`;
  }

  return html`
    <div class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <p class="text-sm text-wa-secondary mr-auto">
          Cada configuração é uma <strong>sequência de retornos</strong>. Quando o cliente manda
          mensagem, a primeira configuração ativa (de cima para baixo) assume a conversa — quem
          filtra quem recebe o follow-up são as condições de cada retorno.
        </p>
        <button type="button" disabled=${!podeEditar} onClick=${() => importRef.current && importRef.current.click()}
          class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
          Importar JSON
        </button>
        <input ref=${importRef} type="file" accept="application/json" class="hidden"
          onChange=${(e) => importar(e.target.files && e.target.files[0])} />
        <button type="button" disabled=${!podeEditar} onClick=${nova}
          class="text-sm px-3 py-1.5 rounded bg-wa-teal text-white disabled:opacity-40">
          + Nova configuração
        </button>
      </div>

      ${erro ? html`<div class="text-sm text-red-600">Erro: ${erro}</div>` : null}

      ${configuracoes.length === 0
        ? html`<div class="rounded-xl border border-wa-border bg-wa-panel p-6 text-center text-wa-secondary text-sm">
            Nenhuma configuração ainda. Crie a primeira para o WhatsBot voltar a falar com quem parou de responder.
          </div>`
        : html`<div class="space-y-2">
            ${configuracoes.map((r, i) => html`
              <div key=${r.id} class="rounded-xl border border-wa-border bg-wa-panel p-3 flex flex-wrap items-center gap-2">
                <div class="flex flex-col gap-0.5">
                  <button type="button" title="Subir prioridade" disabled=${!podeEditar || i === 0}
                    onClick=${() => mover(i, -1)}
                    class="text-wa-secondary hover:text-wa-text disabled:opacity-30 leading-none">↑</button>
                  <button type="button" title="Descer prioridade" disabled=${!podeEditar || i === configuracoes.length - 1}
                    onClick=${() => mover(i, 1)}
                    class="text-wa-secondary hover:text-wa-text disabled:opacity-30 leading-none">↓</button>
                </div>
                <div class="min-w-0 flex-1">
                  <button type="button" onClick=${() => setSelecionada(r.id)}
                    class="text-left font-medium text-wa-text hover:text-wa-teal truncate block max-w-full">
                    ${r.nome || `Configuração #${r.id}`}
                  </button>
                  <div class="flex flex-wrap items-center gap-2 mt-1">
                    <${Badge} cls=${r.ativo ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}>
                      ${r.ativo ? 'Ativa' : 'Inativa'}
                    </${Badge}>
                    <${Badge}>${r.n_retornos} retorno(s)</${Badge}>
                    <${Badge}>${r.n_mensagens} mensagem(ns)</${Badge}>
                    <${Badge}>${r.on_reply === 'cancel' ? 'resposta encerra' : 'resposta reinicia'}</${Badge}>
                  </div>
                  ${r.descricao ? html`<p class="text-xs text-wa-secondary mt-1 truncate">${r.descricao}</p>` : null}
                </div>
                <div class="flex items-center gap-2">
                  <button type="button" disabled=${!podeEditar} onClick=${() => alternarAtivo(r)}
                    class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
                    ${r.ativo ? 'Desativar' : 'Ativar'}
                  </button>
                  <button type="button" onClick=${() => setSelecionada(r.id)}
                    class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
                    Editar
                  </button>
                  <button type="button" disabled=${!podeEditar} onClick=${() => duplicar(r.id)}
                    class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
                    Duplicar
                  </button>
                  <button type="button" disabled=${!podeEditar} onClick=${() => setConfirmar(r)}
                    class="text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40">
                    Excluir
                  </button>
                </div>
              </div>`)}
          </div>`}

      ${confirmar ? html`
        <div class="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
          onClick=${() => setConfirmar(null)}>
          <div class="bg-wa-panel text-wa-text rounded-xl border border-wa-border shadow-lg max-w-sm w-full p-5"
            onClick=${(e) => e.stopPropagation()}>
            <h4 class="font-semibold mb-2">Excluir configuração</h4>
            <p class="text-sm text-wa-secondary mb-4">
              A configuração “${confirmar.nome}”, seus retornos, mensagens e os agendamentos
              em andamento dela serão apagados. Esta ação não pode ser desfeita.
            </p>
            <div class="flex justify-end gap-2">
              <button type="button" onClick=${() => setConfirmar(null)}
                class="px-3 py-1.5 rounded bg-wa-hover text-wa-text text-sm">Cancelar</button>
              <button type="button" onClick=${() => excluir(confirmar.id)}
                class="px-3 py-1.5 rounded bg-red-600 text-white text-sm">Excluir</button>
            </div>
          </div>
        </div>` : null}
    </div>`;
}

// ── Monitor ────────────────────────────────────────────────────────────────

// O DIA escolhido no calendário vira epoch no fuso do NAVEGADOR — o servidor só compara
// epoch, igual ao resto do painel; "até" fecha no último instante do dia.
function diaParaEpoch(dia, fimDoDia = false) {
  if (!dia) return null;
  const [ano, mes, d] = String(dia).split('-').map(Number);
  if (!ano || !mes || !d) return null;
  const dt = fimDoDia ? new Date(ano, mes - 1, d, 23, 59, 59, 999)
    : new Date(ano, mes - 1, d, 0, 0, 0, 0);
  return Math.floor(dt.getTime() / 1000);
}

function MonitorTab({ apiBase, can, ping }) {
  const [controles, setControles] = useState([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState(null);
  const [status, setStatus] = useState('active');
  const [configuracaoId, setConfiguracaoId] = useState('');
  const [disparos, setDisparos] = useState('');
  const [de, setDe] = useState('');
  const [ate, setAte] = useState('');
  const [porPagina, setPorPagina] = useState(50);
  const [offset, setOffset] = useState(0);
  const [configuracoes, setConfiguracoes] = useState([]);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);
  const podeOperar = !can || can('monitor');

  const carregar = useCallback(async () => {
    setCarregando(true);
    try {
      const params = new URLSearchParams({
        status, limit: String(porPagina), offset: String(offset),
      });
      if (configuracaoId) params.set('configuracao_id', configuracaoId);
      // `0` é filtro legítimo (quem ainda não disparou) — só o campo VAZIO não filtra.
      const disparosNum = Number.parseInt(String(disparos).trim(), 10);
      if (Number.isFinite(disparosNum) && disparosNum >= 0) {
        params.set('disparos', String(disparosNum));
      }
      const desde = diaParaEpoch(de);
      const ateEpoch = diaParaEpoch(ate, true);
      if (desde !== null) params.set('next_from', String(desde));
      if (ateEpoch !== null) params.set('next_to', String(ateEpoch));
      const [c, s] = await Promise.all([
        reqJson(`${apiBase}/monitor/controles?${params.toString()}`),
        reqJson(`${apiBase}/monitor/stats`),
      ]);
      const d = c.data || {};
      setControles(d.items || []);
      setTotal(Number(d.total || 0));
      setStats(s.data || null);
      setErro(null);
    } catch (e) { setErro(String(e.message || e)); } finally { setCarregando(false); }
  }, [apiBase, status, configuracaoId, disparos, de, ate, porPagina, offset]);

  const carregarOpcoes = useCallback(async () => {
    try {
      const d = await reqJson(`${apiBase}/monitor/filtros`);
      setConfiguracoes((d.data || {}).configuracoes || []);
    } catch { /* filtro degradado para "Todas" — a tabela continua funcionando */ }
  }, [apiBase]);

  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => { carregarOpcoes(); }, [carregarOpcoes]);
  useEffect(() => { if (ping) { carregar(); carregarOpcoes(); } }, [ping]);

  // Cancelar a última linha da última página (ou o verificador concluir agendamentos
  // enquanto a tela está aberta) deixaria a tabela vazia com o contador dizendo que
  // existem agendamentos — volta para a última página que ainda tem linha.
  useEffect(() => {
    if (carregando || total === 0 || offset < total) return;
    setOffset(Math.max(0, (Math.ceil(total / porPagina) - 1) * porPagina));
  }, [carregando, total, offset, porPagina]);

  const temFiltro = !!(configuracaoId || String(disparos).trim() !== '' || de || ate);
  // Mudar de filtro volta à página 1 — senão o offset da página antiga cairia num
  // resultado menor e a tabela abriria vazia com linhas existentes atrás.
  function filtrar(fn) {
    fn();
    setOffset(0);
  }
  function limparFiltros() {
    filtrar(() => { setConfiguracaoId(''); setDisparos(''); setDe(''); setAte(''); });
  }

  async function acao(id, verbo) {
    try {
      await reqJson(`${apiBase}/monitor/controles/${id}/${verbo}`, { method: 'POST' });
      carregar();
    } catch (e) { setErro(String(e.message || e)); }
  }

  const primeiro = total === 0 ? 0 : offset + 1;
  const ultimo = Math.min(offset + controles.length, total);
  const temAnterior = offset > 0;
  const temProxima = offset + porPagina < total;

  const cards = [
    ['Em andamento', stats && stats.active, 'bg-wa-teal/15 text-wa-teal'],
    ['Concluídos', stats && stats.completed, 'bg-green-100 text-green-700'],
    ['Cancelados', stats && stats.cancelled, 'bg-gray-100 text-gray-600'],
    ['Expirados', stats && stats.expired, 'bg-red-100 text-red-700'],
    ['Disparos', stats && stats.disparos, 'bg-amber-100 text-amber-700'],
  ];

  return html`
    <div class="space-y-3">
      <div class="grid grid-cols-2 sm:grid-cols-5 gap-2">
        ${cards.map(([label, valor, cls]) => html`
          <div class="rounded-xl border border-wa-border bg-wa-panel p-3">
            <div class=${`inline-block text-[11px] px-2 py-0.5 rounded-full ${cls}`}>${label}</div>
            <div class="text-2xl font-semibold text-wa-text mt-1">${valor ?? '—'}</div>
          </div>`)}
      </div>

      <div class="flex flex-wrap items-end gap-2">
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Status:
          <select class="wa-field rounded px-2 py-1 text-sm" value=${status}
            onChange=${(e) => filtrar(() => setStatus(e.target.value))}>
            <option value="active">Em andamento</option>
            <option value="todos">Todos</option>
            <option value="completed">Concluídos</option>
            <option value="cancelled">Cancelados</option>
            <option value="expired">Expirados</option>
          </select>
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Configuração:
          <select class="wa-field rounded px-2 py-1 text-sm max-w-[12rem]" value=${configuracaoId}
            onChange=${(e) => filtrar(() => setConfiguracaoId(e.target.value))}>
            <option value="">Todas</option>
            ${configuracoes.map((c) => html`
              <option key=${c.id} value=${String(c.id)}>${c.nome || `#${c.id}`}</option>`)}
          </select>
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Disparos:
          <input type="number" min="0" step="1" placeholder="nº" value=${disparos}
            class="wa-field rounded px-2 py-1 text-sm w-20"
            onInput=${(e) => filtrar(() => setDisparos(e.target.value))} />
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Próximo de:
          <input type="date" class="wa-field rounded px-2 py-1 text-sm" value=${de}
            onChange=${(e) => filtrar(() => setDe(e.target.value))} />
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          até:
          <input type="date" class="wa-field rounded px-2 py-1 text-sm" value=${ate}
            onChange=${(e) => filtrar(() => setAte(e.target.value))} />
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Por página:
          <select class="wa-field rounded px-2 py-1 text-sm" value=${String(porPagina)}
            onChange=${(e) => filtrar(() => setPorPagina(Number(e.target.value)))}>
            ${[25, 50, 100, 200].map((n) => html`<option key=${n} value=${String(n)}>${n}</option>`)}
          </select>
        </label>
        <button type="button" onClick=${carregar}
          class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
          Recarregar
        </button>
        ${temFiltro ? html`
          <button type="button" onClick=${limparFiltros}
            class="text-xs px-2 py-1 rounded text-wa-secondary hover:text-wa-text">
            Limpar filtros
          </button>` : null}
      </div>

      ${(de && ate && diaParaEpoch(de) > diaParaEpoch(ate, true)) ? html`
        <div class="text-xs text-amber-600">
          O início do intervalo é depois do fim — nenhum agendamento cabe aí.
        </div>` : null}

      ${erro ? html`<div class="text-sm text-red-600">Erro: ${erro}</div>` : null}

      <section class="rounded-xl border border-wa-border bg-wa-panel overflow-hidden">
        <header class="flex items-center justify-between px-3 py-2 border-b border-wa-border">
          <h3 class="text-sm font-semibold text-wa-text">Agendamentos</h3>
          <span class="text-[11px] text-wa-secondary">
            ${carregando ? 'Carregando…' : total === 0 ? 'Nenhum agendamento com esse filtro.'
              : `${primeiro}–${ultimo} de ${total}`}
          </span>
        </header>
        <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-wa-teal text-white whitespace-nowrap">
              <th class="text-left px-3 py-2">Cliente</th>
              <th class="text-left px-3 py-2">Configuração</th>
              <th class="text-left px-3 py-2">Retorno atual</th>
              <th class="text-center px-3 py-2">Disparos</th>
              <th class="text-left px-3 py-2">Próximo</th>
              <th class="text-left px-3 py-2">Status</th>
              <th class="text-center px-3 py-2">Ações</th>
            </tr>
          </thead>
          <tbody>
            ${controles.length === 0
              ? html`<tr><td colspan="7" class="text-center italic text-wa-secondary py-6">
                  Nenhum agendamento com esse filtro.</td></tr>`
              : controles.map((c) => {
                const meta = STATUS_META[c.status] || { label: c.status, cls: 'bg-gray-100 text-gray-600' };
                return html`
                  <tr key=${c.id} class="border-b border-wa-border">
                    <td class="px-3 py-2 text-wa-text">
                      ${c.conversation_id
                        ? html`<a href=${`/conversations/${c.conversation_id}`} target="_blank" rel="noopener"
                            class="text-wa-teal hover:opacity-80 text-left">
                            ${c.contact_name || c.phone || `#${c.conversation_id}`}
                          </a>`
                        : html`<span>${c.contact_name || c.phone || '—'}</span>`}
                      <span class="block text-[11px] text-wa-secondary">${c.phone}</span>
                    </td>
                    <td class="px-3 py-2 text-wa-secondary">${c.configuracao_nome || `#${c.configuracao_id}`}</td>
                    <td class="px-3 py-2 text-wa-secondary">
                      ${c.retorno_nome || '—'}
                      ${Number(c.tentativas_retorno) > 0
                        ? html`<span class="block text-[11px]">${c.tentativas_retorno} tentativa(s)</span>` : null}
                    </td>
                    <td class="px-3 py-2 text-center text-wa-text">${c.disparos_enviados}</td>
                    <td class="px-3 py-2 text-wa-secondary whitespace-nowrap">
                      ${c.status === 'active'
                        ? html`${fmtRelativo(c.next_at)}
                            <span class="block text-[11px]">${fmtDateTime(c.next_at)}</span>`
                        : '—'}
                    </td>
                    <td class="px-3 py-2">
                      <${Badge} cls=${meta.cls}>${meta.label}</${Badge}>
                      ${c.last_error
                        ? html`<span class="block text-[11px] text-red-600 max-w-[16rem] truncate"
                            title=${c.last_error}>${c.last_error}</span>` : null}
                    </td>
                    <td class="px-3 py-2 text-center whitespace-nowrap">
                      <button type="button" disabled=${!podeOperar} onClick=${() => acao(c.id, 'reset')}
                        class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
                        Reiniciar
                      </button>
                      ${c.status === 'active' ? html`
                        <button type="button" disabled=${!podeOperar} onClick=${() => acao(c.id, 'cancel')}
                          class="text-xs px-2 py-1 rounded text-red-600 hover:bg-red-50 disabled:opacity-40">
                          Cancelar
                        </button>` : null}
                    </td>
                  </tr>`;
              })}
          </tbody>
        </table>
        </div>
        <footer class="flex items-center justify-between gap-2 px-3 py-2 border-t border-wa-border">
          <button type="button" disabled=${!temAnterior || carregando}
            onClick=${() => setOffset(Math.max(0, offset - porPagina))}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
            ← Anteriores
          </button>
          <span class="text-[11px] text-wa-secondary">
            Página ${Math.floor(offset / porPagina) + 1} de ${Math.max(1, Math.ceil(total / porPagina))}
          </span>
          <button type="button" disabled=${!temProxima || carregando}
            onClick=${() => setOffset(offset + porPagina)}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
            Próximos →
          </button>
        </footer>
      </section>

      <p class="text-xs text-wa-secondary">
        O histórico de eventos do verificador (armado, disparado, reagendado, cancelado…)
        fica na aba <span class="font-medium text-wa-text">Eventos</span>.
      </p>
    </div>`;
}

// ── Eventos (log paginado) ─────────────────────────────────────────────────
// A lista rola DENTRO do próprio quadro (cabeçalho fixo) e a paginação é do SERVIDOR:
// o total vem de `count_logs`, então "1–50 de 812" não mente sobre a página carregada.

const NIVEL_CLS = {
  error: 'bg-red-100 text-red-700',
  warning: 'bg-amber-100 text-amber-700',
};

function EventoLinha({ log, onAbrirConversa }) {
  const [aberto, setAberto] = useState(false);
  const dados = JSON.stringify(log.data || {});
  const temDados = dados && dados !== '{}';
  return html`
    <li class="px-3 py-2 border-b border-wa-border last:border-0 hover:bg-wa-hover/50">
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <span class="text-wa-secondary w-[8.5rem] shrink-0">${fmtDateTime(log.ts)}</span>
        <${Badge} cls=${NIVEL_CLS[log.nivel] || 'bg-wa-hover text-wa-secondary'}>
          ${log.evento}
        </${Badge}>
        ${log.conversation_id ? html`
          <button type="button" onClick=${() => onAbrirConversa(log.conversation_id)}
            class="text-wa-teal hover:opacity-80">conversa #${log.conversation_id}</button>` : null}
        ${log.configuracao_id ? html`
          <span class="text-wa-secondary">configuração #${log.configuracao_id}</span>` : null}
        ${temDados ? html`
          <button type="button" onClick=${() => setAberto((v) => !v)}
            class=${`text-wa-secondary hover:text-wa-text flex-1 min-w-0 text-left ${
              aberto ? '' : 'truncate'}`}
            title=${aberto ? 'Recolher' : 'Clique para ver tudo'}>
            ${dados}
          </button>` : null}
      </div>
    </li>`;
}

function EventosTab({ apiBase, ping }) {
  const [logs, setLogs] = useState([]);
  const [total, setTotal] = useState(0);
  const [tipos, setTipos] = useState([]);
  const [evento, setEvento] = useState('');
  const [conversa, setConversa] = useState('');
  const [porPagina, setPorPagina] = useState(50);
  const [offset, setOffset] = useState(0);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState(null);
  const listaRef = useRef(null);

  const carregar = useCallback(async () => {
    setCarregando(true);
    try {
      const params = new URLSearchParams({ limit: String(porPagina), offset: String(offset) });
      if (evento) params.set('evento', evento);
      if (String(conversa).trim()) params.set('conversation_id', String(conversa).trim());
      const r = await reqJson(`${apiBase}/monitor/logs?${params.toString()}`);
      const d = r.data || {};
      setLogs(d.items || []);
      setTotal(Number(d.total || 0));
      setTipos(d.eventos || []);
      setErro(null);
    } catch (e) { setErro(String(e.message || e)); } finally { setCarregando(false); }
  }, [apiBase, porPagina, offset, evento, conversa]);

  useEffect(() => { carregar(); }, [carregar]);
  useEffect(() => { if (ping) carregar(); }, [ping, carregar]);

  function trocarPagina(novoOffset) {
    setOffset(Math.max(0, novoOffset));
    if (listaRef.current) listaRef.current.scrollTop = 0;
  }
  function filtrar(fn) {
    fn();
    setOffset(0);
  }

  function abrirConversa(id) {
    if (!id) return;
    history.pushState(null, '', `/conversations/${id}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }

  const primeiro = total === 0 ? 0 : offset + 1;
  const ultimo = Math.min(offset + logs.length, total);
  const temAnterior = offset > 0;
  const temProxima = offset + porPagina < total;

  return html`
    <div class="space-y-3">
      <div class="flex flex-wrap items-end gap-2">
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Evento:
          <select class="wa-field rounded px-2 py-1 text-sm" value=${evento}
            onChange=${(e) => filtrar(() => setEvento(e.target.value))}>
            <option value="">Todos</option>
            ${tipos.map((t) => html`<option key=${t} value=${t}>${t}</option>`)}
          </select>
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Conversa:
          <input type="number" min="1" placeholder="nº" value=${conversa}
            class="wa-field rounded px-2 py-1 text-sm w-24"
            onInput=${(e) => filtrar(() => setConversa(e.target.value))} />
        </label>
        <label class="text-sm text-wa-secondary flex items-center gap-1">
          Por página:
          <select class="wa-field rounded px-2 py-1 text-sm" value=${String(porPagina)}
            onChange=${(e) => filtrar(() => setPorPagina(Number(e.target.value)))}>
            ${[25, 50, 100, 200].map((n) => html`<option key=${n} value=${String(n)}>${n}</option>`)}
          </select>
        </label>
        <button type="button" onClick=${carregar}
          class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
          Recarregar
        </button>
        ${(evento || String(conversa).trim()) ? html`
          <button type="button"
            onClick=${() => filtrar(() => { setEvento(''); setConversa(''); })}
            class="text-xs px-2 py-1 rounded text-wa-secondary hover:text-wa-text">
            Limpar filtros
          </button>` : null}
      </div>

      ${erro ? html`<div class="text-sm text-red-600">Erro: ${erro}</div>` : null}

      <section class="rounded-xl border border-wa-border bg-wa-panel overflow-hidden">
        <header class="flex items-center justify-between px-3 py-2 border-b border-wa-border">
          <h3 class="text-sm font-semibold text-wa-text">Eventos</h3>
          <span class="text-[11px] text-wa-secondary">
            ${carregando ? 'Carregando…' : total === 0 ? 'Nada registrado ainda.'
              : `${primeiro}–${ultimo} de ${total}`}
          </span>
        </header>
        ${logs.length === 0 && !carregando
          ? html`<p class="text-xs text-wa-secondary px-3 py-6 text-center italic">
              Nenhum evento com esse filtro.</p>`
          : html`<ul ref=${listaRef} class="max-h-[60vh] overflow-y-auto">
              ${logs.map((l) => html`
                <${EventoLinha} key=${l.id} log=${l} onAbrirConversa=${abrirConversa} />`)}
            </ul>`}
        <footer class="flex items-center justify-between gap-2 px-3 py-2 border-t border-wa-border">
          <button type="button" disabled=${!temAnterior || carregando}
            onClick=${() => trocarPagina(offset - porPagina)}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
            ← Anteriores
          </button>
          <span class="text-[11px] text-wa-secondary">
            Página ${Math.floor(offset / porPagina) + 1} de ${Math.max(1, Math.ceil(total / porPagina))}
          </span>
          <button type="button" disabled=${!temProxima || carregando}
            onClick=${() => trocarPagina(offset + porPagina)}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-40">
            Próximos →
          </button>
        </footer>
      </section>
    </div>`;
}

// ── Componente da tela ─────────────────────────────────────────────────────

export function Retornos({ apiBase = '/api/plugins/retornos', can = null }) {
  const [aba, setAba] = useState('configuracoes');
  const [metadata, setMetadata] = useState(null);
  const [erro, setErro] = useState(null);
  const [tick, setTick] = useState(null);
  const [ping, setPing] = useState(0);

  useEffect(() => {
    let vivo = true;
    reqJson(`${apiBase}/metadata`)
      .then((d) => { if (vivo) setMetadata(d.data); })
      .catch((e) => { if (vivo) setErro(String(e.message || e)); });
    return () => { vivo = false; };
  }, [apiBase]);

  useEffect(() => subscribe({
    retornos_tick: (data) => setTick(data),
    retornos_changed: () => setPing((n) => n + 1),
  }), []);

  const abaBtn = (id, label) => html`
    <button type="button" onClick=${() => setAba(id)}
      class=${`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
        aba === id ? 'border-wa-teal text-wa-teal' : 'border-transparent text-wa-secondary hover:text-wa-text'}`}>
      ${label}
    </button>`;

  return html`
    <div class="max-w-6xl mx-auto p-4 space-y-4">
      <div class="flex flex-wrap items-center gap-3">
        <h1 class="text-xl font-semibold text-wa-text">Retorno Automático</h1>
        ${tick ? html`
          <${Badge} cls="bg-green-100 text-green-700">
            Verificador ativo · ${fmtTime(tick.checked_at)}
          </${Badge}>` : null}
      </div>

      <div class="flex border-b border-wa-border">
        ${abaBtn('configuracoes', 'Configurações')}
        ${abaBtn('monitor', 'Monitor')}
        ${abaBtn('eventos', 'Eventos')}
      </div>

      ${erro ? html`<div class="text-sm text-red-600">Erro: ${erro}</div>` : null}
      ${!metadata
        ? html`<div class="text-sm text-wa-secondary">Carregando…</div>`
        : aba === 'configuracoes'
          ? html`<${ConfiguracoesTab} apiBase=${apiBase} metadata=${metadata} can=${can} />`
          : aba === 'monitor'
            ? html`<${MonitorTab} apiBase=${apiBase} can=${can} ping=${ping} />`
            : html`<${EventosTab} apiBase=${apiBase} ping=${ping} />`}
    </div>`;
}

export default Retornos;
