// Host do modal "Enviar template" (Plano 92 · B1).
//
// O core NÃO é dono da forma de um template: categorias, tipos de botão, limites
// de upload e o vocabulário de aprovação vêm da API do provedor. Quem renderiza a
// tela, portanto, é o plugin de canal — via o override EXCLUSIVO 'template.picker'
// (plugins/registry.js). Este host só resolve quem responde:
//
//   plugin registrou  → renderiza o componente dele
//   ninguém registrou → renderiza o TemplatePicker do core (fallback)
//
// O fallback existe para que a migração não abra uma janela em que o operador
// fica sem enviar template (core novo + zip antigo). Ele sai na G1 do plano 92,
// depois que o plugin já estiver publicado e em produção — a partir daí, sem
// override o botão nem aparece (ver `templatePickerAvailable`).
//
// Props repassadas ao componente resolvido (o contrato do slot):
//   {conversationId, channelId, phone, onClose, onSent}

import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { getComponentOverride, subscribe } from '../../plugins/registry.js';
import { TemplatePicker } from './TemplatePicker.js';

/** Nome do ponto de extensão. Único lugar onde a string é escrita no core. */
export const TEMPLATE_PICKER_SLOT = 'template.picker';

// Enquanto o core ainda carrega o picker de fallback, SEMPRE há uma tela para
// abrir. Na G1 esta constante vira `false` junto com a remoção do componente, e
// o botão do compositor passa a depender só do override.
const CORE_FALLBACK = TemplatePicker;

/**
 * Existe alguma tela de template para abrir? Gate do botão no compositor — sem
 * isso, o dia em que o fallback sair o botão abriria o vazio.
 * @returns {boolean}
 */
export function templatePickerAvailable() {
  return !!getComponentOverride(TEMPLATE_PICKER_SLOT) || !!CORE_FALLBACK;
}

function resolvePicker() {
  const override = getComponentOverride(TEMPLATE_PICKER_SLOT);
  return override ? override.component : CORE_FALLBACK;
}

export function TemplatePickerHost(props) {
  // O componente resolvido é CONGELADO na montagem. Trocar o tipo do vnode com o
  // modal aberto o desmontaria e remontaria, jogando fora o formulário que o
  // atendente já preencheu. Isso não é hipotético: `loadPluginExtensions` limpa o
  // registry de forma SÍNCRONA e só o repovoa depois de N `await import()`
  // (App.js), e o `whatsapp_cloud` é o último na ordem alfabética — qualquer
  // toggle de plugin abriria essa janela embaixo do modal aberto.
  const [Picker, setPicker] = useState(resolvePicker);

  // A única transição permitida é "nada" → "alguma coisa": cobre o caso de o
  // modal ser aberto na janela de boot em que o plugin ainda não registrou (e,
  // depois da G1, quando não há mais fallback do core para segurar a ponta).
  useEffect(() => {
    if (Picker) return undefined;
    return subscribe(() => {
      const next = resolvePicker();
      if (next) setPicker(() => next);
    });
  }, [Picker]);

  if (!Picker) return null;
  return h(Picker, props);
}
