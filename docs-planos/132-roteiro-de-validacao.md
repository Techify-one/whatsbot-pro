# Roteiro de validação — o cursor do compositor (plano 132)

> **Para quê:** reproduzir o defeito em **produção** (que ainda não recebeu a correção) e confirmar que ele **não** acontece no ambiente de teste. Os dois caminhos abaixo foram executados e conferidos em Chromium e Firefox antes de virar roteiro.
>
> ⚠️ **Use uma conversa de teste** — o seu próprio número, ou um contato de homologação. O roteiro faz você digitar num compositor de verdade; nada é enviado se você não apertar Enter, mas é melhor não treinar em cima de um cliente.

---

## O texto de teste

Sem acento nenhum, de propósito: isola o defeito da quebra de linha do outro defeito (o do acento colado, §Caminho C). Precisa ter mais de 6 linhas para o campo rolar.

```
Prezado cliente, boa tarde. Informamos que a manutencao programada da rede na sua regiao foi concluida com sucesso as 14h de hoje. Todos os servicos de internet, telefonia e TV ja estao operando normalmente. Caso ainda esteja enfrentando qualquer instabilidade, pedimos que reinicie o seu equipamento desligando-o da tomada por 30 segundos antes de religar. Se o problema persistir apos esse procedimento, responda esta mensagem que abriremos um chamado tecnico com prioridade para o seu atendimento.
```

---

## Caminho A — a sonda de console (10 segundos, resposta objetiva)

É o jeito mais rápido e não depende de você "achar" que o cursor pulou. Ele compara a altura das **duas camadas** do compositor: o campo onde o cursor vive e o espelho que você lê.

1. Abra o painel, entre numa conversa.
2. Abra o console do navegador (**F12** → aba *Console*).
3. Cole no compositor **o texto de teste da seção acima**.
4. **Com o cursor no fim do texto, aperte `Shift+Enter` uma vez.** É este o gatilho.
5. Cole no console e aperte Enter:

```js
(() => {
  const ta = [...document.querySelectorAll('textarea')]
    .find(t => t.previousElementSibling &&
               t.previousElementSibling.getAttribute('aria-hidden') === 'true');
  if (!ta) return 'compositor não encontrado — abra uma conversa primeiro';
  const esp = ta.previousElementSibling;
  const d = ta.scrollHeight - esp.scrollHeight;
  return d === 0
    ? `OK — campo ${ta.scrollHeight}px e espelho ${esp.scrollHeight}px batem`
    : `DEFEITO — campo ${ta.scrollHeight}px, espelho ${esp.scrollHeight}px: faltam ${d}px (${d/20} linha)`;
})()
```

**O que esperar:**

| ambiente | com `Shift+Enter` no fim | sem `Shift+Enter` (controle) |
|---|---|---|
| **produção (hoje)** | `DEFEITO — campo 178px, espelho 158px: faltam 20px (1 linha)` | `OK` |
| **teste (corrigido)** | `OK — campo 178px e espelho 178px batem` | `OK` |

O controle importa: ele mostra que a sonda não acusa defeito à toa. Em produção, **só o caso com a quebra final** dá `DEFEITO`.

---

## Caminho B — reproduzir com o dedo (o que o atendente sente)

1. Conversa de teste, compositor vazio.
2. Cole **o texto de teste** (está logo no começo deste arquivo).
3. **Aperte `Shift+Enter` uma vez** (o cursor vai para uma linha nova, vazia).
4. O campo está rolado no fim. Agora **clique bem no começo da palavra `reinicie`** — ela fica no meio do texto visível.
5. Digite `XXX`.

**Em produção:** o `XXX` **não** aparece em `reinicie`. Ele aparece cerca de **72 caracteres à frente**, dentro de `de religar. Se` — mais ou menos uma linha abaixo de onde você clicou. Se em vez de digitar você apertar `Backspace`, ele apaga letra de lá, não de onde você clicou. É exatamente o relato: *"ponho o cursor num lugar e apaga de outro"*.

**No ambiente de teste:** o `XXX` aparece exatamente em `reinicie`, onde você clicou.

> Se em produção o `XXX` cair no lugar certo, verifique se o passo 3 foi feito: **sem a quebra de linha no fim, o defeito não aparece**. É essa a condição que ninguém tinha percebido — e é por isso que ele parecia aleatório.

---


## Caminho C — o outro defeito: o acento colado

Independente do de cima, e é este que explica o vídeo do operador (*"palavras sem acento apagavam normalmente"*). Só aparece com texto **colado** de PDF, de página feita no macOS ou de sistema legado — o que se digita no teclado nunca tem o problema. E só em palavras que **terminam** em letra acentuada: `está` e `você` falham, `não` não falha.

⚠️ **Tem de ser uma colagem de verdade (`Ctrl+V`).** A correção age no evento de colagem; escrever no campo por outro caminho a contorna e daria "defeito" nos dois ambientes — falso positivo.

1. Clique **uma vez na página** (o navegador só libera a área de transferência com a aba em foco).
2. No console, cole e rode — isto põe o texto decomposto na área de transferência:

```js
navigator.clipboard.writeText('a manutenção está'.normalize('NFD'))
  .then(() => console.log('copiado — agora clique no compositor e aperte Ctrl+V'))
  .catch(e => console.log('falhou (clique na página antes):', e.message));
```

3. Clique no compositor, `Ctrl+V`.
4. Cursor no **fim**, um `Backspace`.

| ambiente | colou | depois de 1 `Backspace` |
|---|---|---|
| **produção (hoje)** | 20 caracteres | `a manutenção esta` — o **`á` virou `a`**: a letra continua lá e só o acento sumiu |
| **teste (corrigido)** | 17 caracteres | `a manutenção est` — a letra inteira saiu, como deveria |

A contagem já entrega o caso: **20 contra 17** para o mesmo texto na tela.

**A sonda para o caso real do operador** — no chat onde ele reclamou, **antes de apagar nada**:

```js
const v = document.querySelector('textarea').value;
console.log(v.length, v.normalize('NFC').length, v === v.normalize('NFC'));
```

Comprimentos diferentes ⇒ o texto dele está decomposto e o caso dele era este, não o da quebra de linha.

---

## O que NÃO deve mudar (contraprova)

Se qualquer um destes mudar no ambiente de teste, é regressão — me avise:

- [ ] `**negrito**` continua chegando no WhatsApp do cliente como negrito
- [ ] a bolha da conversa (mensagem já enviada) continua mostrando negrito e código monoespaçado
- [ ] `@menção` em grupo e `/atalho` continuam funcionando
- [ ] colar imagem com texto no campo continua usando o texto como legenda
- [ ] rascunho: digitar, trocar de conversa e voltar preserva o texto

**Mudança esperada e proposital:** no compositor, `` `código` `` deixou de aparecer em fonte monoespaçada e passou a aparecer com uma tarja cinza. Só na prévia — na mensagem enviada continua monoespaçado.
