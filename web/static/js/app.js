// WhatsBot frontend entry point (Plano 23 · D4 — decomposed).
//
// The app shell was split into components/shell/*:
//   • screenRegistry.js — declarative core-screen table + pure path<->tab routing
//   • GearMenu.js        — the gear menu + PageHeader
//   • ScreenRouter.js    — renders the active screen / plugin screen / route override
//   • App.js             — top-level state, WS subscriptions (singleton wsBus),
//                          routing wiring, wizard latch, LowBalanceModal/ModalHost
//   • AuthGate.js        — the password/login gate + RBAC bootstrap
//
// This module just mounts AuthGate (which renders App once authenticated).
import { h, render } from 'preact';
import htm from 'htm';
import { AuthGate } from './components/shell/AuthGate.js';

const html = htm.bind(h);

render(html`<${AuthGate} />`, document.getElementById('app'));
