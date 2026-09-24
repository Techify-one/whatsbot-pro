// Pure decision for the "Nome" field of the contact panel (ContactInfoPanel.js) — kept
// dependency-free (no preact/htm) so it can be unit-tested with `node --test`.
//
// A group has no editable name: it is the group subject the provider reported
// (`contacts.group_name`), refreshed by the inbound, while `contacts.name` stays empty.
// So for a group the field mirrors `group_name` and is locked; for a person it is the
// contact's own name (minus the "~" pushName marker), editable with `contact.write`.
//
//   isGroup   — the contact is a group
//   groupName — `contacts.group_name`
//   infoName  — `contacts.name` (info.name)
//   canWrite  — the user holds `contact.write`
export function contactNameField({ isGroup, groupName, infoName, canWrite }) {
  if (isGroup) {
    return {
      value: groupName || '',
      locked: true,
      placeholder: 'Nome do grupo ainda não sincronizado',
      hint: 'Nome definido pelo WhatsApp — não pode ser alterado.',
    };
  }
  return {
    value: (infoName || '').replace(/^~/, ''),
    locked: !canWrite,
    placeholder: 'Nome do contato',
    hint: null,
  };
}

// Save payload for the contact panel: a group's name is never sent (the backend ignores
// it too), so the locked mirror of `group_name` can't leak into `contacts.name`.
export function infoPayloadFor(isGroup, form) {
  if (!isGroup) return form;
  const { name, ...rest } = form;   // eslint-disable-line no-unused-vars
  return rest;
}
