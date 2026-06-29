// Re-export the canonical formatter under the historical `formatPhone` name so
// existing `import { formatPhone } from './QRCode.js'` call sites keep working.
// (The old ConnectionStatus / QRCodeModal components were removed — the QR/
// connection UI now lives in components/channels/QRConnect.js.)
export { formatPhoneDisplay as formatPhone } from '../utils/phone.js';
