import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const apiSource = readFileSync(new URL('./api.js', import.meta.url), 'utf8');
const pickerSource = readFileSync(
  new URL('../components/contacts/TeamPickerList.js', import.meta.url),
  'utf8',
);
const contextMenuSource = readFileSync(
  new URL('../components/contacts/ContextMenu.js', import.meta.url),
  'utf8',
);
const teamPickerSource = readFileSync(
  new URL('../components/contacts/TeamPicker.js', import.meta.url),
  'utf8',
);
const actionsSource = readFileSync(
  new URL('../components/contacts/hooks/useConversationActions.js', import.meta.url),
  'utf8',
);
const contactsSource = readFileSync(
  new URL('../components/contacts/Contacts.js', import.meta.url),
  'utf8',
);

test('client API adapta catálogo e CRUD sem criar chamadas de routing ou bulk', () => {
  assert.match(apiSource, /normalizeAssignableCatalogResponse\([\s\S]*assignable-agents/);
  assert.match(apiSource, /normalizeTeamAdminResponse\(await request\('GET', `\/api\/teams/);
  assert.match(apiSource, /include_inactive=true/);
  assert.match(apiSource, /hard=true/);
  assert.doesNotMatch(apiSource, /route-to-team|route-team|bulk-team/);
});

test('picker consome capability explícita e expõe o motivo no conteúdo acessível', () => {
  assert.match(pickerSource, /assignableTeamOptions\(teams\)/);
  assert.match(pickerSource, /team\.assignable !== true/);
  assert.match(pickerSource, /aria-describedby=\$\{reasonId\}/);
  assert.match(pickerSource, /teamUnavailableReason/);
  assert.doesNotMatch(pickerSource, /assignable !== false/);
});

test('menu de time não reutiliza conversation.assign como autorização', () => {
  const teamBlock = contextMenuSource.slice(
    contextMenuSource.indexOf('const currentTeamId'),
    contextMenuSource.indexOf('const pickTeam'),
  );
  assert.match(teamBlock, /assignableTeamOptions\(teams\)/);
  assert.match(teamBlock, /teamCapabilities\.provided/);
  assert.doesNotMatch(teamBlock, /can\('conversation\.assign'\)/);
  assert.doesNotMatch(teamBlock, /member_user_ids/);
});

test('capabilities do catálogo chegam explicitamente ao menu consumidor', () => {
  assert.match(actionsSource, /setTeamCapabilities\(res\.data\.capabilities/);
  assert.match(actionsSource, /teams, teamCapabilities,/);
  assert.match(contactsSource, /teamCapabilities=\$\{teamCapabilities\}/);
});

test('picker do painel vira leitura quando capability explícita nega atribuição', () => {
  assert.match(teamPickerSource, /capabilities\.provided/);
  assert.match(teamPickerSource, /if \(!canAssignTeam\)/);
  assert.match(teamPickerSource, /assignableTeamOptions\(teams\)/);
});
